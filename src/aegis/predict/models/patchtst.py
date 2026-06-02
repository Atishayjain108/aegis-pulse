"""
PatchTST — patch-based time-series transformer.

Reference: Nie et al. 2023, "A Time Series is Worth 64 Words".
Patches the input series, projects each patch to d_model, applies
a stack of standard transformer encoder layers, and decodes via a
shared linear head.

This module is **torch-optional**:
    * If torch is installed and the model loads, we run the real
      forward pass.
    * If torch is missing, the model construction fails fast at
      `__init__` and the factory drops back to HeuristicTemporalPredictor.
    * If torch is present but the forward fails (NaN, OOM, etc.),
      the base Predictor's exception handler catches it and falls
      back to heuristic_predict — the user always gets a result.

We split the model into:
    1. `_PatchTSTNet` — pure nn.Module (importable only if torch is).
    2. `PatchTSTPredictor` — Predictor adapter; converts FeatureWindow
       to a tensor, runs forward, decodes head outputs back to a
       PredictionBundle. Also handles MC-dropout sampling when
       configured to do so.

The decoder produces, per horizon:
    * 6 stage logits (DORMANT, EMERGING, BREAKOUT, PEAK, DECLINING, SATURATED)
    * 1 mean log-velocity scalar
    * 1 log-sigma scalar (aleatoric noise estimate)

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import structlog

from ..constants import (
    MC_DROPOUT_SAMPLES,
    PATCHTST_D_MODEL,
    PATCHTST_DROPOUT,
    PATCHTST_N_HEADS,
    PATCHTST_N_LAYERS,
    PATCHTST_PATCH_LEN,
    PATCHTST_STRIDE,
)
from ..features.graph import CreatorGraph
from ..schemas import (
    FeatureWindow,
    ModelKind,
    Prediction,
    TrendStage,
    UncertaintyMethod,
)
from .base import Predictor
from .heuristic import _action_for, heuristic_predict

_log = structlog.get_logger("aegis.predict.models.patchtst")

# Try to import torch lazily. If absent, the factory will route around us.
try:
    import torch
    from torch import nn

    _HAS_TORCH = True
except Exception:  # pragma: no cover - environment-dependent
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    _HAS_TORCH = False

if TYPE_CHECKING:  # pragma: no cover
    pass


# Class labels in the canonical order used by the head.
_STAGE_ORDER: tuple[TrendStage, ...] = (
    TrendStage.DORMANT,
    TrendStage.EMERGING,
    TrendStage.BREAKOUT,
    TrendStage.PEAK,
    TrendStage.DECLINING,
    TrendStage.SATURATED,
)


# ---------------------------------------------------------------------------
# Module definition — only created if torch is available.
# ---------------------------------------------------------------------------
def _make_patchtst_net(
    *,
    feature_dim: int,
    window_size: int,
    horizons: int,
    d_model: int = PATCHTST_D_MODEL,
    n_heads: int = PATCHTST_N_HEADS,
    n_layers: int = PATCHTST_N_LAYERS,
    dropout: float = PATCHTST_DROPOUT,
    patch_len: int = PATCHTST_PATCH_LEN,
    stride: int = PATCHTST_STRIDE,
) -> Any:
    """Construct the nn.Module. Lazy so non-torch envs never call it.

    Outputs a tensor of shape (batch, horizons, 6+2):
        [stage_logits (6), velocity_mean_log, velocity_log_sigma].
    """
    if not _HAS_TORCH:
        raise RuntimeError("torch unavailable: cannot build PatchTST")

    class _PatchTSTNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.feature_dim = feature_dim
            self.window_size = window_size
            self.horizons = horizons
            self.patch_len = patch_len
            self.stride = stride

            # n_patches per series with valid stride.
            self.n_patches = max(1, 1 + (window_size - patch_len) // stride)

            # Linear patch embedding shared across feature channels.
            self.patch_proj = nn.Linear(patch_len * feature_dim, d_model)

            # Learnable positional embeddings.
            self.pos_emb = nn.Parameter(torch.randn(self.n_patches, d_model) * 0.02)

            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=n_heads,
                dim_feedforward=d_model * 4,
                dropout=dropout,
                batch_first=True,
                activation="gelu",
                norm_first=True,  # pre-norm — more stable training
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

            # Shared head per horizon: output 8 values (6 stage logits + 2 velocity).
            self.head = nn.Sequential(
                nn.LayerNorm(d_model),
                nn.Dropout(dropout),
                nn.Linear(d_model, d_model),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(d_model, horizons * 8),
            )

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            """x: (B, window_size, feature_dim) → (B, horizons, 8)"""
            B, T, F = x.shape
            # Build patches: unfold time axis with stride.
            patches = x.unfold(1, self.patch_len, self.stride)  # (B, n_patches, F, patch_len)
            patches = patches.permute(0, 1, 3, 2).contiguous()
            patches = patches.reshape(B, patches.shape[1], self.patch_len * F)
            tokens = self.patch_proj(patches) + self.pos_emb[: patches.shape[1]]
            encoded = self.encoder(tokens)
            pooled = encoded.mean(dim=1)  # (B, d_model)
            out = self.head(pooled)  # (B, horizons * 8)
            return out.view(B, self.horizons, 8)

    return _PatchTSTNet()


# ---------------------------------------------------------------------------
# Predictor adapter
# ---------------------------------------------------------------------------
class PatchTSTPredictor(Predictor):
    """Adapter that exposes a trained PatchTST as a Predictor.

    Accepts:
        weights:           torch state_dict-compatible mapping or None.
        horizons:          tuple of horizon-hours; head dim is len(horizons).
        use_mc_dropout:    if True, run MC-dropout for uncertainty.
        mc_samples:        number of stochastic forward passes when
                           use_mc_dropout=True.
        device:            "cpu" | "cuda" | None (auto).
    """

    def __init__(
        self,
        *,
        feature_dim: int,
        window_size: int,
        horizons: tuple[int, ...],
        weights: dict[str, Any] | None = None,
        use_mc_dropout: bool = False,
        mc_samples: int = MC_DROPOUT_SAMPLES,
        device: str | None = None,
        model_id_suffix: str = "untrained",
    ) -> None:
        if not _HAS_TORCH:
            raise RuntimeError(
                "PatchTSTPredictor requires torch; " "install with `uv sync --extra ml`"
            )
        self._horizons = horizons
        self._feature_dim = feature_dim
        self._window_size = window_size
        self._use_mc_dropout = bool(use_mc_dropout)
        self._mc_samples = int(mc_samples)
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model_id_suffix = model_id_suffix

        self._net = _make_patchtst_net(
            feature_dim=feature_dim,
            window_size=window_size,
            horizons=len(horizons),
        )
        if weights is not None:
            self._net.load_state_dict(weights)
        self._net = self._net.to(self._device)
        # Default eval; the adapter will toggle train() for MC dropout.
        self._net.eval()

    @property
    def model_id(self) -> str:
        return f"patchtst-3.0.0-{self._model_id_suffix}"

    @property
    def kind(self) -> ModelKind:
        return ModelKind.TEMPORAL

    @property
    def version(self) -> str:
        return "3.0.0"

    @property
    def uncertainty_method(self) -> UncertaintyMethod:
        return UncertaintyMethod.MC_DROPOUT if self._use_mc_dropout else UncertaintyMethod.NONE

    @property
    def is_heuristic_only(self) -> bool:
        return False

    # ------------------------------------------------------------------
    async def _predict_inner(
        self,
        *,
        window: FeatureWindow,
        graph: CreatorGraph | None,
        horizons: tuple[int, ...],
        seed: int,
    ) -> list[Prediction]:
        # Validate horizons match training-time head shape.
        if tuple(horizons) != tuple(self._horizons):
            _log.warning(
                "patchtst.horizon_mismatch_using_heuristic",
                requested=horizons,
                trained=self._horizons,
            )
            return heuristic_predict(window=window, graph=graph, horizons=horizons)

        # Validate input shape.
        if window.window_size != self._window_size or window.feature_dim != self._feature_dim:
            _log.warning(
                "patchtst.shape_mismatch_using_heuristic",
                expected=(self._window_size, self._feature_dim),
                got=(window.window_size, window.feature_dim),
            )
            return heuristic_predict(window=window, graph=graph, horizons=horizons)

        x = self._tensor_from_window(window)

        if self._use_mc_dropout:
            stage_probs, vmean, vsigma = self._mc_dropout_forward(x, seed=seed)
            method = "mc_dropout"
        else:
            with torch.no_grad():
                self._net.eval()
                out = self._net(x)  # (1, H, 8)
            stage_logits = out[..., :6]
            stage_probs = torch.softmax(stage_logits, dim=-1)[0].cpu().tolist()
            vmean = out[..., 6][0].cpu().tolist()
            vsigma = torch.nn.functional.softplus(out[..., 7])[0].cpu().tolist()
            method = "none"

        return self._decode(
            window=window,
            graph=graph,
            horizons=horizons,
            stage_probs=stage_probs,
            vmean_log=vmean,
            vsigma=vsigma,
            method=method,
        )

    # ------------------------------------------------------------------
    def _tensor_from_window(self, window: FeatureWindow) -> torch.Tensor:
        flat = window.values
        x = torch.tensor(flat, dtype=torch.float32).reshape(
            1, window.window_size, window.feature_dim
        )
        # Defensive scrub: replace any rogue NaN/Inf with 0.
        x = torch.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
        return x.to(self._device)

    def _mc_dropout_forward(
        self, x: torch.Tensor, *, seed: int
    ) -> tuple[list[list[float]], list[float], list[float]]:
        """Run S forward passes with dropout active; return mean stats."""
        torch.manual_seed(seed)
        # Activate dropout layers but keep BN/LN in eval mode. PatchTST
        # uses LayerNorm only, so .train() is safe.
        self._net.train()
        outs: list[torch.Tensor] = []
        with torch.no_grad():
            for _ in range(self._mc_samples):
                outs.append(self._net(x))
        self._net.eval()

        stacked = torch.stack(outs, dim=0)  # (S, 1, H, 8)
        # Stage probs: softmax per sample, then mean.
        stage_logits = stacked[..., :6]
        sample_probs = torch.softmax(stage_logits, dim=-1)
        mean_probs = sample_probs.mean(dim=0)[0].cpu().tolist()  # (H, 6)
        # Velocity mean/std across samples (MC) and within-sample sigma.
        vmean_samples = stacked[..., 6]  # (S, 1, H)
        vsigma_samples = torch.nn.functional.softplus(stacked[..., 7])
        # Total predictive sigma = sqrt(sigma_aleatoric^2 + sigma_epistemic^2)
        sigma_alea = vsigma_samples.mean(dim=0)[0]
        sigma_epi = vmean_samples.std(dim=0, unbiased=False)[0]
        total_sigma = torch.sqrt(sigma_alea**2 + sigma_epi**2)
        vmean = vmean_samples.mean(dim=0)[0].cpu().tolist()
        vsigma = total_sigma.cpu().tolist()
        return mean_probs, vmean, vsigma

    def _decode(
        self,
        *,
        window: FeatureWindow,
        graph: CreatorGraph | None,
        horizons: tuple[int, ...],
        stage_probs: list[list[float]],
        vmean_log: list[float],
        vsigma: list[float],
        method: str,
    ) -> list[Prediction]:
        out: list[Prediction] = []
        for i, h in enumerate(horizons):
            probs = stage_probs[i]
            top_idx = max(range(len(probs)), key=lambda k: probs[k])
            stage = _STAGE_ORDER[top_idx]

            # Map class probs to the (breakout, peak, decline) triplet
            # the schema exposes.
            p_breakout = probs[_STAGE_ORDER.index(TrendStage.BREAKOUT)]
            p_peak = probs[_STAGE_ORDER.index(TrendStage.PEAK)]
            p_decline = probs[_STAGE_ORDER.index(TrendStage.DECLINING)]

            mu = float(vmean_log[i])
            sigma = float(max(0.05, vsigma[i]))

            mean_v = math.exp(mu + 0.5 * sigma * sigma)
            p10 = math.exp(mu - 1.2816 * sigma)
            p50 = math.exp(mu)
            p90 = math.exp(mu + 1.2816 * sigma)

            # Composite confidence: the model's top-class probability
            # tempered by how peaked the distribution is (entropy).
            ent = -sum(p * math.log(max(p, 1e-9)) for p in probs)
            ent_max = math.log(len(probs))
            peakedness = 1.0 - ent / ent_max
            confidence = max(0.05, min(0.95, 0.5 * probs[top_idx] + 0.5 * peakedness))

            action = _action_for(
                p_breakout=p_breakout,
                p_decline=p_decline,
                confidence=confidence,
                stage=stage,
            )

            out.append(
                Prediction(
                    horizon_hours=h,
                    stage=stage,
                    velocity_log=mu,
                    velocity_mean=max(0.0, mean_v),
                    velocity_p10=max(0.0, p10),
                    velocity_p50=max(0.0, p50),
                    velocity_p90=max(0.0, p90),
                    p_breakout=float(p_breakout),
                    p_peak=float(p_peak),
                    p_decline=float(p_decline),
                    aleatoric=sigma,
                    epistemic=0.0 if method == "none" else max(0.0, sigma * 0.5),
                    conformal_lower=max(0.0, p10),
                    conformal_upper=max(0.0, p90),
                    conformal_alpha=0.10,
                    confidence=confidence,
                    action=action,
                    reasoning=(
                        f"patchtst stage={stage.value} p_top={probs[top_idx]:.2f} "
                        f"v_log={mu:.2f} sigma={sigma:.2f} method={method}"
                    ),
                )
            )
        return out


__all__ = ["_HAS_TORCH", "PatchTSTPredictor"]
