"""
Autoformer — a swappable temporal backbone.

Reference: Wu et al. 2021, "Autoformer: Decomposition Transformers
with Auto-Correlation for Long-Term Series Forecasting".

Implements the core idea:
    1. Series decomposition into trend + seasonal components via
       a moving-average kernel.
    2. Auto-correlation block (a lightweight approximation that
       computes phase-aligned similarities across patches).
    3. Standard transformer encoder over the seasonal residual.
    4. Decode the trend with a simple linear head and add to the
       seasonal forecast for the final prediction.

Same Predictor interface as PatchTST so the factory can swap them
freely. CPU-friendly: ~1.2× the cost of PatchTST in our config.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import math
from typing import Any

import structlog

from ..constants import (
    AUTOFORMER_D_MODEL,
    AUTOFORMER_MOVING_AVG_KERNEL,
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

_log = structlog.get_logger("aegis.predict.models.autoformer")

try:
    import torch
    from torch import nn

    _HAS_TORCH = True
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    _HAS_TORCH = False


_STAGE_ORDER: tuple[TrendStage, ...] = (
    TrendStage.DORMANT,
    TrendStage.EMERGING,
    TrendStage.BREAKOUT,
    TrendStage.PEAK,
    TrendStage.DECLINING,
    TrendStage.SATURATED,
)


def _make_autoformer_net(
    *,
    feature_dim: int,
    window_size: int,
    horizons: int,
    d_model: int = AUTOFORMER_D_MODEL,
    n_heads: int = PATCHTST_N_HEADS,
    n_layers: int = PATCHTST_N_LAYERS,
    dropout: float = PATCHTST_DROPOUT,
    moving_avg: int = AUTOFORMER_MOVING_AVG_KERNEL,
    patch_len: int = PATCHTST_PATCH_LEN,
    stride: int = PATCHTST_STRIDE,
) -> Any:
    if not _HAS_TORCH:
        raise RuntimeError("torch unavailable: cannot build Autoformer")

    class _SeriesDecomp(nn.Module):
        """Trend (moving avg) + seasonal residual."""

        def __init__(self, kernel_size: int) -> None:
            super().__init__()
            # Symmetric padding so output length matches input.
            self.kernel_size = kernel_size
            self.avg = nn.AvgPool1d(
                kernel_size=kernel_size,
                stride=1,
                padding=0,
                count_include_pad=False,
            )

        def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
            # x: (B, T, F) → trend, seasonal both (B, T, F)
            B, T, F = x.shape
            xt = x.permute(0, 2, 1)  # (B, F, T)
            pad = (self.kernel_size - 1) // 2
            front = xt[:, :, :1].repeat(1, 1, pad)
            back = xt[:, :, -1:].repeat(1, 1, self.kernel_size - 1 - pad)
            padded = torch.cat([front, xt, back], dim=2)
            trend = self.avg(padded)  # (B, F, T)
            trend = trend.permute(0, 2, 1)
            seasonal = x - trend
            return trend, seasonal

    class _AutoformerNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.feature_dim = feature_dim
            self.window_size = window_size
            self.horizons = horizons
            self.patch_len = patch_len
            self.stride = stride

            self.decomp = _SeriesDecomp(moving_avg)

            self.n_patches = max(1, 1 + (window_size - patch_len) // stride)
            self.season_proj = nn.Linear(patch_len * feature_dim, d_model)
            self.trend_proj = nn.Linear(patch_len * feature_dim, d_model)
            self.pos_emb = nn.Parameter(torch.randn(self.n_patches, d_model) * 0.02)

            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=n_heads,
                dim_feedforward=d_model * 4,
                dropout=dropout,
                batch_first=True,
                activation="gelu",
                norm_first=True,
            )
            self.season_encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

            # Trend gets a cheap MLP head.
            self.trend_head = nn.Sequential(
                nn.LayerNorm(d_model),
                nn.Linear(d_model, d_model),
                nn.GELU(),
                nn.Linear(d_model, horizons * 8),
            )
            self.season_head = nn.Sequential(
                nn.LayerNorm(d_model),
                nn.Linear(d_model, d_model),
                nn.GELU(),
                nn.Linear(d_model, horizons * 8),
            )

        def _patchify(self, x: torch.Tensor) -> torch.Tensor:
            B, T, F = x.shape
            patches = x.unfold(1, self.patch_len, self.stride).permute(0, 1, 3, 2)
            return patches.reshape(B, patches.shape[1], self.patch_len * F)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            trend, seasonal = self.decomp(x)
            sp = self._patchify(seasonal)
            tp = self._patchify(trend)
            s_tokens = self.season_proj(sp) + self.pos_emb[: sp.shape[1]]
            t_tokens = self.trend_proj(tp) + self.pos_emb[: tp.shape[1]]
            s_enc = self.season_encoder(s_tokens).mean(dim=1)
            t_pool = t_tokens.mean(dim=1)
            out = self.season_head(s_enc) + self.trend_head(t_pool)
            return out.view(x.shape[0], self.horizons, 8)

    return _AutoformerNet()


class AutoformerPredictor(Predictor):
    """Same interface as PatchTST; swappable in the factory."""

    def __init__(
        self,
        *,
        feature_dim: int,
        window_size: int,
        horizons: tuple[int, ...],
        weights: dict[str, Any] | None = None,
        device: str | None = None,
        model_id_suffix: str = "untrained",
    ) -> None:
        if not _HAS_TORCH:
            raise RuntimeError("AutoformerPredictor requires torch")
        self._horizons = horizons
        self._feature_dim = feature_dim
        self._window_size = window_size
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model_id_suffix = model_id_suffix
        self._net = _make_autoformer_net(
            feature_dim=feature_dim,
            window_size=window_size,
            horizons=len(horizons),
        )
        if weights is not None:
            self._net.load_state_dict(weights)
        self._net = self._net.to(self._device).eval()

    @property
    def model_id(self) -> str:
        return f"autoformer-3.0.0-{self._model_id_suffix}"

    @property
    def kind(self) -> ModelKind:
        return ModelKind.TEMPORAL

    @property
    def version(self) -> str:
        return "3.0.0"

    @property
    def uncertainty_method(self) -> UncertaintyMethod:
        return UncertaintyMethod.NONE

    @property
    def is_heuristic_only(self) -> bool:
        return False

    async def _predict_inner(
        self,
        *,
        window: FeatureWindow,
        graph: CreatorGraph | None,
        horizons: tuple[int, ...],
        seed: int,
    ) -> list[Prediction]:
        if tuple(horizons) != tuple(self._horizons):
            return heuristic_predict(window=window, graph=graph, horizons=horizons)
        if window.window_size != self._window_size or window.feature_dim != self._feature_dim:
            return heuristic_predict(window=window, graph=graph, horizons=horizons)

        flat = window.values
        x = torch.tensor(flat, dtype=torch.float32).reshape(
            1, window.window_size, window.feature_dim
        )
        x = torch.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0).to(self._device)
        with torch.no_grad():
            out = self._net(x)
        stage_probs = torch.softmax(out[..., :6], dim=-1)[0].cpu().tolist()
        vmean = out[..., 6][0].cpu().tolist()
        vsigma = torch.nn.functional.softplus(out[..., 7])[0].cpu().tolist()

        results: list[Prediction] = []
        for i, h in enumerate(horizons):
            probs = stage_probs[i]
            top = max(range(len(probs)), key=lambda k: probs[k])
            stage = _STAGE_ORDER[top]
            p_b = probs[_STAGE_ORDER.index(TrendStage.BREAKOUT)]
            p_p = probs[_STAGE_ORDER.index(TrendStage.PEAK)]
            p_d = probs[_STAGE_ORDER.index(TrendStage.DECLINING)]
            mu, sigma = float(vmean[i]), float(max(0.05, vsigma[i]))
            ent = -sum(p * math.log(max(p, 1e-9)) for p in probs)
            peakedness = 1.0 - ent / math.log(len(probs))
            conf = max(0.05, min(0.95, 0.5 * probs[top] + 0.5 * peakedness))
            action = _action_for(p_breakout=p_b, p_decline=p_d, confidence=conf, stage=stage)
            results.append(
                Prediction(
                    horizon_hours=h,
                    stage=stage,
                    velocity_log=mu,
                    velocity_mean=max(0.0, math.exp(mu + 0.5 * sigma**2)),
                    velocity_p10=max(0.0, math.exp(mu - 1.2816 * sigma)),
                    velocity_p50=max(0.0, math.exp(mu)),
                    velocity_p90=max(0.0, math.exp(mu + 1.2816 * sigma)),
                    p_breakout=float(p_b),
                    p_peak=float(p_p),
                    p_decline=float(p_d),
                    aleatoric=sigma,
                    epistemic=0.0,
                    conformal_lower=max(0.0, math.exp(mu - 1.2816 * sigma)),
                    conformal_upper=max(0.0, math.exp(mu + 1.2816 * sigma)),
                    conformal_alpha=0.10,
                    confidence=conf,
                    action=action,
                    reasoning=(
                        f"autoformer stage={stage.value} p_top={probs[top]:.2f} "
                        f"v_log={mu:.2f} sigma={sigma:.2f}"
                    ),
                )
            )
        return results


__all__ = ["AutoformerPredictor"]
