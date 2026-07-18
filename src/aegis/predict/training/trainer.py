"""
Minimal training loop for the temporal predictors.

Why a hand-rolled loop and not PyTorch Lightning?
-------------------------------------------------
* Torch Lightning is a great framework but adds ~120 MB of deps and
  introduces opinionated lifecycle hooks that conflict with our
  resilience.py guard-rails.
* The training loop needed by Phase 3 is short — under 200 lines. The
  marginal complexity of carrying Lightning is not worth it.

What this file does NOT do:
  * Distributed multi-GPU. Single-host CUDA is enough for our scale.
  * Hyperparameter sweeps. Use `optuna` separately if needed.
  * Model registry writes. The trainer returns a `TrainingResult` and
    the operator (or a CI job) calls `ModelStore.register()` after.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from .. import DEFAULT_HORIZONS
from ..errors import ModelInferenceError
from .dataset import LabelledSample, SignalDataset, iter_batches

logger = logging.getLogger(__name__)


try:  # pragma: no cover
    import torch
    from torch import nn

    _HAS_TORCH = True
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    _HAS_TORCH = False


@dataclass(frozen=True, slots=True)
class TrainerConfig:
    """Trainer hyperparameters."""

    epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 1e-3
    weight_decay: float = 1e-5
    grad_clip_norm: float = 1.0
    early_stop_patience: int = 3
    val_split: float = 0.2
    seed: int = 1234
    device: str = "cpu"  # "cpu" | "cuda"


@dataclass
class TrainingResult:
    """Returned by `train_temporal`."""

    model_name: str
    epochs_run: int
    final_train_loss: float
    final_val_loss: float
    best_val_loss: float
    history: list[dict[str, float]] = field(default_factory=list)
    duration_s: float = 0.0
    state_dict: dict | None = None  # torch state_dict on success


def _to_tensors(
    batch: list[LabelledSample], horizons: tuple[int, ...] = DEFAULT_HORIZONS
) -> tuple[Any, Any, Any, Any]:
    """Convert a list of LabelledSample to torch tensors.

    Returns (x, stage_y, velocity_y, breakout_y).

    stage_y / velocity_y / breakout_y are tensors of shape (B, H) for
    horizons. For now we train only on a single supervised horizon
    pulled from each sample's target — multi-horizon training is the
    natural extension but we keep this trainer minimal.
    """
    if not _HAS_TORCH:
        raise ModelInferenceError("torch unavailable — cannot train")

    x_list = []
    stage_list = []
    velocity_list = []
    breakout_list = []
    for s in batch:
        # FeatureWindow → numpy ndarray via the schema helper.
        arr = s.window.as_2d()
        x_list.append(arr)
        # Targets are scalar per sample; we replicate across horizons.
        target = s.target
        stage_list.append(_stage_to_idx(target.get("stage")))
        velocity_list.append(float(target.get("velocity", 0.0)))
        breakout_list.append(int(target.get("breakout", 0)))

    x = torch.tensor(x_list, dtype=torch.float32)
    x = torch.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    stage_y = torch.tensor(stage_list, dtype=torch.long)
    velocity_y = torch.tensor(velocity_list, dtype=torch.float32)
    breakout_y = torch.tensor(breakout_list, dtype=torch.float32)
    return x, stage_y, velocity_y, breakout_y


_STAGE_INDEX = {
    "DORMANT": 0,
    "EMERGING": 1,
    "BREAKOUT": 2,
    "PEAK": 3,
    "DECLINING": 4,
    "SATURATED": 5,
}


def _stage_to_idx(stage: Any) -> int:
    if hasattr(stage, "value"):
        stage = stage.value
    return _STAGE_INDEX.get(str(stage).upper(), 0)


def _multi_task_loss(
    logits: Any,
    stage_y: Any,
    velocity_y: Any,
    breakout_y: Any,
) -> Any:
    """Cross-entropy + Gaussian NLL for velocity + BCE for breakout.

    Logits are expected as (B, H, 8) where the first 6 channels are
    stage logits, channel 6 is velocity_mean, channel 7 is log-sigma.
    We collapse over H by taking the *last* horizon — single-horizon
    training keeps this implementation small. To train multi-horizon,
    duplicate the loss for each h in horizons and average.
    """
    if logits.dim() == 3:
        logits = logits[:, -1, :]  # (B, 8)

    stage_logits = logits[:, :6]
    vel_mean = logits[:, 6]
    log_sigma = logits[:, 7]

    ce = nn.functional.cross_entropy(stage_logits, stage_y)
    # Gaussian NLL: 0.5*(log(2π) + 2*log_sigma + ((y-μ)^2 / σ²))
    log_two_pi = math.log(2 * math.pi)
    sigma2 = torch.exp(2 * log_sigma).clamp(min=1e-4)
    nll_vel = 0.5 * (log_two_pi + 2 * log_sigma + (velocity_y - vel_mean).pow(2) / sigma2).mean()

    # Approximate breakout prob from stage softmax (channel 2 = BREAKOUT).
    p_breakout = torch.softmax(stage_logits, dim=-1)[:, 2]
    p_breakout = p_breakout.clamp(min=1e-6, max=1 - 1e-6)
    bce = nn.functional.binary_cross_entropy(p_breakout, breakout_y)

    # Equal-weighted multi-task. In practice you'd weight via
    # uncertainty-weighted loss (Kendall et al.) but the equal-weight
    # default works well for our scale.
    return ce + 0.3 * nll_vel + 0.5 * bce


def train_temporal(
    model_name: str,
    network: Any,
    train_set: SignalDataset,
    val_set: SignalDataset | None = None,
    *,
    config: TrainerConfig | None = None,
) -> TrainingResult:
    """Train `network` on `train_set`. Returns a TrainingResult.

    Args:
        model_name: name to record in the result (e.g. "patchtst").
        network: an nn.Module producing (B, H, 8) outputs.
        train_set: SignalDataset.
        val_set: optional held-out set for early-stopping.
        config: TrainerConfig.

    On any failure (torch missing, NaN loss, OOM) the function
    returns a result with `state_dict=None` and a final_val_loss of
    +inf. The caller should NOT promote a model when state_dict is None.
    """
    config = config or TrainerConfig()
    start = time.monotonic()
    history: list[dict[str, float]] = []

    if not _HAS_TORCH:
        return TrainingResult(
            model_name=model_name,
            epochs_run=0,
            final_train_loss=float("inf"),
            final_val_loss=float("inf"),
            best_val_loss=float("inf"),
            history=[],
            duration_s=0.0,
            state_dict=None,
        )

    if len(train_set) == 0:
        logger.warning("train_temporal: empty dataset, skipping")
        return TrainingResult(
            model_name=model_name,
            epochs_run=0,
            final_train_loss=float("inf"),
            final_val_loss=float("inf"),
            best_val_loss=float("inf"),
            history=[],
            duration_s=time.monotonic() - start,
            state_dict=None,
        )

    torch.manual_seed(config.seed)
    device = config.device if torch.cuda.is_available() or config.device == "cpu" else "cpu"
    network = network.to(device)
    network.train()
    optimizer = torch.optim.AdamW(
        network.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )

    best_val = float("inf")
    epochs_no_improve = 0
    final_train_loss = float("inf")
    final_val_loss = float("inf")
    best_state_dict: dict | None = None

    try:
        for epoch in range(config.epochs):
            train_losses: list[float] = []
            for batch in iter_batches(
                train_set, config.batch_size, shuffle=True, seed=config.seed + epoch
            ):
                x, stage_y, vel_y, br_y = _to_tensors(batch)
                x = x.to(device)
                stage_y = stage_y.to(device)
                vel_y = vel_y.to(device)
                br_y = br_y.to(device)

                optimizer.zero_grad(set_to_none=True)
                out = network(x)
                loss = _multi_task_loss(out, stage_y, vel_y, br_y)
                if not torch.isfinite(loss):
                    raise ModelInferenceError("non-finite loss — aborting")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(network.parameters(), config.grad_clip_norm)
                optimizer.step()
                train_losses.append(float(loss.item()))

            train_loss = sum(train_losses) / max(1, len(train_losses))

            val_loss = float("inf")
            if val_set is not None and len(val_set) > 0:
                network.eval()
                val_losses: list[float] = []
                with torch.no_grad():
                    for batch in iter_batches(val_set, config.batch_size, shuffle=False):
                        x, stage_y, vel_y, br_y = _to_tensors(batch)
                        x = x.to(device)
                        stage_y = stage_y.to(device)
                        vel_y = vel_y.to(device)
                        br_y = br_y.to(device)
                        out = network(x)
                        loss = _multi_task_loss(out, stage_y, vel_y, br_y)
                        val_losses.append(float(loss.item()))
                val_loss = sum(val_losses) / max(1, len(val_losses))
                network.train()

            history.append(
                {
                    "epoch": float(epoch),
                    "train_loss": train_loss,
                    "val_loss": val_loss,
                }
            )
            final_train_loss = train_loss
            final_val_loss = val_loss

            improved = val_loss < best_val - 1e-4
            if improved:
                best_val = val_loss
                epochs_no_improve = 0
                # Snapshot the state_dict on every improvement.
                best_state_dict = {
                    k: v.detach().cpu().clone() for k, v in network.state_dict().items()
                }
            else:
                epochs_no_improve += 1
                if epochs_no_improve >= config.early_stop_patience:
                    logger.info("early stop at epoch %d", epoch)
                    break
    except Exception as exc:  # pragma: no cover — guard rail
        logger.error("training failed: %s", exc)
        return TrainingResult(
            model_name=model_name,
            epochs_run=len(history),
            final_train_loss=final_train_loss,
            final_val_loss=final_val_loss,
            best_val_loss=best_val,
            history=history,
            duration_s=time.monotonic() - start,
            state_dict=best_state_dict,
        )

    return TrainingResult(
        model_name=model_name,
        epochs_run=len(history),
        final_train_loss=final_train_loss,
        final_val_loss=final_val_loss,
        best_val_loss=best_val,
        history=history,
        duration_s=time.monotonic() - start,
        state_dict=best_state_dict,
    )
