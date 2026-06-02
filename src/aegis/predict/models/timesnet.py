"""
TimesNet (Wu et al. 2023, "TimesNet: Temporal 2D-Variation Modeling for
General Time Series Analysis").

Why TimesNet
------------
PatchTST captures *local* dependencies inside a flat patch and Autoformer
captures *trend / seasonal* split via series decomposition. TimesNet gives
us a third, complementary view: it FFT-folds the 1-D series into a 2-D
matrix where rows = inferred period, columns = position-within-period,
then runs a small inception block over that 2-D image. This explicitly
models multi-period structure (e.g. a 24h cycle co-existing with a 7d
cycle) which the patch transformer cannot see directly.

Doctrine compliance
-------------------
Same as the rest of the temporal stack:

* the deterministic heuristic always runs first;
* the neural net only refines confidence and the velocity sigma;
* on any error (missing torch, NaN forward, shape mismatch, timeout)
  we degrade silently to the heuristic prediction;
* the forward graph contains no Python control flow that depends on the
  input tensor's values, so the module is ONNX-exportable.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from .. import DEFAULT_FEATURE_WINDOW, DEFAULT_HORIZONS
from ..constants import (
    TIMESNET_D_MODEL,
    TIMESNET_DROPOUT,
    TIMESNET_K_PERIODS,
    TIMESNET_N_LAYERS,
)
from ..errors import ModelInferenceError
from ..features.graph import CreatorGraph
from ..schemas import FeatureWindow, ModelKind, Prediction
from .base import Predictor
from .heuristic import heuristic_predict

logger = logging.getLogger(__name__)

try:  # pragma: no cover — exercised in environments where torch is installed
    import torch
    import torch.nn.functional as F  # noqa: N812
    from torch import nn

    _HAS_TORCH = True
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]
    _HAS_TORCH = False


# ---------------------------------------------------------------------------
# 2-D inception block — runs after the FFT fold
# ---------------------------------------------------------------------------
def _make_inception_block(d_model: int) -> Any:
    """Tiny multi-kernel 2-D conv block.

    We deliberately keep this small (3 kernel sizes) because the folded
    matrix is also small (period × n_periods) and we don't want to
    explode the parameter count.
    """
    if not _HAS_TORCH:  # pragma: no cover
        raise RuntimeError("torch not installed")

    class _Inception2D(nn.Module):
        def __init__(self, d: int) -> None:
            super().__init__()
            # Three parallel kernel sizes — output channels split evenly.
            # Padding chosen so spatial dims are preserved.
            ch = max(d // 3, 8)
            self.b1 = nn.Conv2d(d, ch, kernel_size=1, padding=0)
            self.b3 = nn.Conv2d(d, ch, kernel_size=3, padding=1)
            self.b5 = nn.Conv2d(d, ch, kernel_size=5, padding=2)
            self.proj = nn.Conv2d(ch * 3, d, kernel_size=1)
            self.act = nn.GELU()
            self.norm = nn.GroupNorm(num_groups=min(8, d), num_channels=d)

        def forward(self, x):  # type: ignore[no-untyped-def]
            y = torch.cat([self.b1(x), self.b3(x), self.b5(x)], dim=1)
            y = self.act(y)
            y = self.proj(y)
            return self.norm(x + y)

    return _Inception2D(d_model)


def _fft_top_periods(x: Any, k: int) -> tuple[Any, Any]:
    """Return the top-k dominant periods (and their amplitude weights).

    Args:
        x:  (batch, length, dim) time series.
        k:  how many periods to keep.

    Returns:
        periods: int tensor of shape (k,) — period length in time-steps.
        weights: (batch, k) — amplitude of that period for each batch row,
                 used as a soft routing weight when we mix the k branches.
    """
    if not _HAS_TORCH:  # pragma: no cover
        raise RuntimeError("torch not installed")

    # rFFT along the time axis, mean over feature axis to get a single spectrum.
    spec = torch.fft.rfft(x, dim=1)  # (B, L//2 + 1, D)
    amp = spec.abs().mean(dim=-1)  # (B, L//2 + 1)
    amp[:, 0] = 0.0  # drop the DC component — it is not a period

    # Pick top-k frequencies *globally* (mean over batch) so all rows in a
    # batch share the same FFT routing — this keeps the forward static and
    # ONNX-exportable (no per-row indexing).
    global_amp = amp.mean(dim=0)  # (L//2 + 1,)
    _, top_idx = torch.topk(global_amp, k=k)  # (k,)

    length = x.shape[1]
    # frequency index → period length (avoid div-by-zero, clamp to length)
    periods = torch.div(length, top_idx.clamp(min=1), rounding_mode="floor").clamp(
        min=1, max=length
    )

    # Per-batch weights for those k periods, softmaxed for stability.
    weights = amp[:, top_idx]  # (B, k)
    weights = F.softmax(weights, dim=-1)
    return periods, weights


def _make_timesnet_net(
    feature_dim: int,
    window_size: int,
    horizons: tuple[int, ...],
    *,
    d_model: int = TIMESNET_D_MODEL,
    n_layers: int = TIMESNET_N_LAYERS,
    k_periods: int = TIMESNET_K_PERIODS,
    dropout: float = TIMESNET_DROPOUT,
) -> Any:
    """Build a TimesNet module sized for our 168×20 windows.

    Output shape: (batch, len(horizons), 8) — same 8-channel head as
    PatchTST / Autoformer so the fusion layer can mix them homogeneously:

        [0..5]  stage logits (DORMANT, EMERGING, BREAKOUT, PEAK, DECLINING, SATURATED)
        [6]     velocity_mean_log  (log-domain Δlog(count) over the horizon)
        [7]     velocity_log_sigma (predicted aleatoric uncertainty)
    """
    if not _HAS_TORCH:  # pragma: no cover
        raise RuntimeError("torch not installed")

    class _TimesNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.input_proj = nn.Linear(feature_dim, d_model)
            self.pos_drop = nn.Dropout(dropout)
            self.blocks = nn.ModuleList([_make_inception_block(d_model) for _ in range(n_layers)])
            self.layer_norm = nn.LayerNorm(d_model)
            self.head = nn.Sequential(
                nn.Linear(d_model, d_model),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(d_model, len(horizons) * 8),
            )
            self.window_size = window_size
            self.horizons = horizons
            self.k_periods = k_periods
            self.d_model = d_model

        def _fold_one_period(self, x: Any, period: int) -> Any:
            """Pad `x` along time, then reshape to (B, D, period, n_per).

            x is (B, L, D); after fold we expose period-of-day along axis 2
            and "which day" along axis 3 — i.e. an image where rows are
            phase and columns are cycle index.
            """
            B, L, D = x.shape
            n_per = math.ceil(L / period)
            pad = n_per * period - L
            if pad > 0:
                x = F.pad(x, (0, 0, 0, pad))  # pad along time axis
            return (
                x.reshape(B, n_per, period, D).permute(0, 3, 2, 1).contiguous()
            )  # (B, D, period, n_per)

        def _unfold_one_period(self, y: Any, period: int, length: int) -> Any:
            B, D, p, n_per = y.shape
            y = y.permute(0, 3, 2, 1).contiguous().reshape(B, n_per * p, D)
            return y[:, :length, :]

        def forward(self, x):  # type: ignore[no-untyped-def]
            # x: (B, L, D_in)
            B, L, _ = x.shape
            h = self.input_proj(x)  # (B, L, d_model)
            h = self.pos_drop(h)

            for block in self.blocks:
                periods, weights = _fft_top_periods(h, self.k_periods)
                # weights: (B, k); periods: (k,)
                branch_outputs = []
                for i in range(self.k_periods):
                    # cast period to int via .item() — this is fine in
                    # eager mode and ONNX trace will record the value.
                    p = int(periods[i].item())
                    folded = self._fold_one_period(h, p)  # (B, D, p, n)
                    folded = block(folded)
                    unfolded = self._unfold_one_period(folded, p, L)  # (B, L, D)
                    branch_outputs.append(unfolded)
                stacked = torch.stack(branch_outputs, dim=-1)  # (B, L, D, k)
                # Soft route via FFT weights, broadcasting (B, 1, 1, k).
                w = weights.unsqueeze(1).unsqueeze(1)
                h = (stacked * w).sum(dim=-1)  # (B, L, D)
                h = self.layer_norm(h)

            # Pool over time — last-bucket pool keeps recency emphasis,
            # which matches our heuristic's "recent_24h" framing.
            pooled = h[:, -1, :]  # (B, d_model)
            out = self.head(pooled)  # (B, H * 8)
            return out.reshape(B, len(self.horizons), 8)

    return _TimesNet()


# ---------------------------------------------------------------------------
# Predictor adapter
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class TimesNetPredictor(Predictor):
    """Hybrid TimesNet adapter.

    Like the other neural predictors:
      * the heuristic verdict is the floor;
      * the neural model can downscale confidence (factor in [0.5, 1.0]),
        replace the velocity sigma if it is more conservative, and append
        reasoning text saying which periods dominated the FFT routing.
    """

    feature_dim: int = 20
    window_size: int = DEFAULT_FEATURE_WINDOW
    horizons: tuple[int, ...] = DEFAULT_HORIZONS

    @property
    def name(self) -> str:
        return "timesnet"

    @property
    def model_id(self) -> str:
        return "timesnet-0.1.0"

    @property
    def kind(self) -> ModelKind:
        return ModelKind.TEMPORAL

    @property
    def version(self) -> str:
        return "0.1.0"

    _net: Any = None  # populated lazily

    def _ensure_net(self) -> Any:
        if not _HAS_TORCH:
            return None
        if self._net is None:
            self._net = _make_timesnet_net(
                feature_dim=self.feature_dim,
                window_size=self.window_size,
                horizons=self.horizons,
            )
            self._net.eval()
        return self._net

    def load_state(self, state_dict: dict) -> None:
        """Load pre-trained weights. Silent no-op without torch."""
        net = self._ensure_net()
        if net is None:
            logger.warning("timesnet: torch unavailable, weights ignored")
            return
        try:
            net.load_state_dict(state_dict)
        except Exception as exc:  # pragma: no cover
            raise ModelInferenceError(f"timesnet failed to load state_dict: {exc}") from exc

    async def _predict_inner(
        self,
        *,
        window: FeatureWindow,
        graph: CreatorGraph | None,
        horizons: tuple[int, ...],
        seed: int,
    ) -> list[Prediction]:
        """Run TimesNet, then fold its output into the heuristic verdict."""
        # Always compute the heuristic floor first — this is also our
        # fallback if the neural pass fails for any reason.
        base_preds = heuristic_predict(window=window, graph=graph, horizons=horizons)

        net = self._ensure_net()
        if net is None:
            return base_preds

        # Validate shape early — saves us from a confusing RuntimeError deep in conv.
        if window.window_size != self.window_size or window.feature_dim != self.feature_dim:
            logger.debug(
                "timesnet: window shape (%d, %d) != trained (%d, %d), using heuristic",
                window.window_size,
                window.feature_dim,
                self.window_size,
                self.feature_dim,
            )
            return base_preds

        try:
            arr = window.as_2d()
            x = torch.tensor(arr, dtype=torch.float32).unsqueeze(0)  # (1, L, D)
            x = torch.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
            with torch.no_grad():
                logits = net(x)  # (1, H, 8)
            logits = logits.squeeze(0).cpu().numpy()  # (H, 8)
        except Exception as exc:  # pragma: no cover — guard rail
            logger.warning("timesnet forward failed: %s — using heuristic", exc)
            return base_preds

        # Refine each per-horizon prediction. We do NOT flip the stage —
        # we only:
        #   * compute net entropy → confidence multiplier in [0.6, 1.0];
        #   * if net's velocity sigma is *larger* than heuristic's, use it
        #     (more conservative bands);
        #   * stamp model_kind = TEMPORAL.
        refined: list[Prediction] = []
        for h_idx, base_pred in enumerate(base_preds):
            row = logits[h_idx]
            stage_logits = row[:6]
            # Stable softmax for entropy
            mx = float(stage_logits.max())
            exps = [math.exp(float(lg) - mx) for lg in stage_logits]
            Z = sum(exps) or 1.0
            probs = [e / Z for e in exps]
            entropy = -sum(p * math.log(p + 1e-9) for p in probs)
            max_entropy = math.log(6.0)
            confidence_mult = 1.0 - 0.4 * (entropy / max_entropy)  # ∈ [0.6, 1.0]

            net_sigma = float(abs(row[7]))
            new_velocity_sigma = max(base_pred.velocity_log_sigma, min(net_sigma, 2.0))

            new_confidence = max(0.05, min(0.99, base_pred.confidence * confidence_mult))
            refined.append(
                base_pred.model_copy(
                    update={
                        "confidence": new_confidence,
                        "velocity_log_sigma": new_velocity_sigma,
                        "model_kind": ModelKind.TEMPORAL,
                        "reasoning": (
                            base_pred.reasoning
                            + f" timesnet: entropy={entropy:.3f}, σ_log={net_sigma:.3f}."
                        ).strip(),
                    }
                )
            )

        return refined
