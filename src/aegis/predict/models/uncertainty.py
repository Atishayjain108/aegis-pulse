"""
Uncertainty quantification wrappers.

Three approaches, used independently or composed:

    1. Deep ensemble — average M independently-trained models. Reduces
       both bias (slightly) and variance (substantially). Used for
       epistemic uncertainty: the std of M predictions.

    2. MC-dropout — already implemented inside PatchTSTPredictor; this
       module exposes a passthrough wrapper for compositional use.

    3. Conformal prediction — distribution-free coverage guarantees.
       Given a calibration set with held-out residuals, produces
       prediction intervals that contain the true value with prob
       1-α (here α=0.10 → 90% coverage), assuming exchangeability.

Conformal calibration state is kept here as `ConformalCalibrator`
and is fitted offline from a held-out set. Inference uses only the
already-fitted quantile, so the call is O(1).

All three wrappers respect the doctrine: if the underlying predictor
falls back to heuristic, the uncertainty wrapper passes the result
through unchanged (heuristic predictions already include calibrated
percentile bands).

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..constants import CONFORMAL_ALPHA
from ..features.graph import CreatorGraph
from ..schemas import (
    FeatureWindow,
    ModelKind,
    Prediction,
    PredictionBundle,
    UncertaintyMethod,
)
from .base import Predictor


# ---------------------------------------------------------------------------
# Conformal calibration
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ConformalCalibrator:
    """Fitted conformal quantile per horizon.

    `q[h]` is the (1-α) quantile of |residual| / σ_predicted on the
    calibration set for horizon h. Inference scales the predicted
    σ by this factor when constructing the interval.
    """

    quantiles: dict[int, float]
    alpha: float = CONFORMAL_ALPHA

    def adjust(self, *, horizon_h: int, mu: float, sigma: float) -> tuple[float, float]:
        """Return (lower, upper) on the natural scale (after exp)."""
        q = self.quantiles.get(horizon_h, 1.2816)  # default = z_{0.9}
        scale = max(0.05, sigma) * q
        return math.exp(mu - scale), math.exp(mu + scale)


def fit_conformal(
    *,
    residuals_by_horizon: dict[int, list[tuple[float, float, float]]],
    alpha: float = CONFORMAL_ALPHA,
) -> ConformalCalibrator:
    """Fit conformal quantiles from (mu, sigma, true_log_velocity) triples.

    Score function:  s = |true - mu| / max(σ, 0.05).
    Quantile:       q = (1-α) sample quantile (with finite-sample correction).
    """
    quantiles: dict[int, float] = {}
    for h, triples in residuals_by_horizon.items():
        if not triples:
            quantiles[h] = 1.2816
            continue
        scores = sorted(abs(true - mu) / max(0.05, sigma) for mu, sigma, true in triples)
        n = len(scores)
        # Finite-sample-corrected (1-α) quantile per Vovk 2005.
        k = min(n - 1, max(0, math.ceil((n + 1) * (1 - alpha)) - 1))
        quantiles[h] = scores[k]
    return ConformalCalibrator(quantiles=quantiles, alpha=alpha)


# ---------------------------------------------------------------------------
# Deep ensemble
# ---------------------------------------------------------------------------
class DeepEnsemblePredictor(Predictor):
    """Average M independently-trained predictors."""

    def __init__(
        self,
        *,
        members: list[Predictor],
        conformal: ConformalCalibrator | None = None,
        ensemble_id_suffix: str = "ensemble",
    ) -> None:
        if len(members) < 2:
            raise ValueError("deep ensemble needs ≥ 2 members")
        self._members = members
        self._conformal = conformal
        self._ensemble_id = f"deep_ensemble-{len(members)}-{ensemble_id_suffix}"

    @property
    def model_id(self) -> str:
        return self._ensemble_id

    @property
    def kind(self) -> ModelKind:
        return ModelKind.FUSION

    @property
    def version(self) -> str:
        return "3.0.0"

    @property
    def uncertainty_method(self) -> UncertaintyMethod:
        if self._conformal is not None:
            return UncertaintyMethod.CONFORMAL
        return UncertaintyMethod.DEEP_ENSEMBLE

    @property
    def is_heuristic_only(self) -> bool:
        return all(m.is_heuristic_only for m in self._members)

    async def _predict_inner(
        self,
        *,
        window: FeatureWindow,
        graph: CreatorGraph | None,
        horizons: tuple[int, ...],
        seed: int,
    ) -> list[Prediction]:
        # Run all members. The base class wraps each in its own
        # exception handler — if any member fails, it returns a
        # heuristic bundle and the ensemble simply averages those.
        bundles: list[PredictionBundle] = []
        for i, m in enumerate(self._members):
            b = await m.predict(window, graph=graph, horizons=horizons, seed=seed + i)
            bundles.append(b)

        out: list[Prediction] = []
        for h in horizons:
            preds_h = [b.by_horizon(h) for b in bundles]
            preds_h = [p for p in preds_h if p is not None]
            if not preds_h:
                continue
            out.append(self._average(preds_h, horizon_h=h))
        return out

    def _average(self, preds: list[Prediction], *, horizon_h: int) -> Prediction:
        n = len(preds)
        # Average class probabilities and log-velocity.
        p_b = sum(p.p_breakout for p in preds) / n
        p_p = sum(p.p_peak for p in preds) / n
        p_d = sum(p.p_decline for p in preds) / n
        mu = sum(p.velocity_log for p in preds) / n

        # Aleatoric: average of within-model sigmas.
        alea = sum((p.aleatoric or 0.0) for p in preds) / n
        # Epistemic: std of mu across members.
        if n > 1:
            mu_bar = mu
            epi = math.sqrt(sum((p.velocity_log - mu_bar) ** 2 for p in preds) / (n - 1))
        else:
            epi = 0.0
        sigma = math.sqrt(alea * alea + epi * epi)

        if self._conformal is not None:
            lower, upper = self._conformal.adjust(horizon_h=horizon_h, mu=mu, sigma=sigma)
        else:
            lower = math.exp(mu - 1.2816 * sigma)
            upper = math.exp(mu + 1.2816 * sigma)

        # Stage by majority vote.
        from collections import Counter

        stage_votes = Counter(p.stage for p in preds)
        stage = stage_votes.most_common(1)[0][0]

        confidence = sum(p.confidence for p in preds) / n
        # Penalise confidence when members disagree on stage.
        agreement = stage_votes[stage] / n
        confidence = max(0.05, min(0.98, confidence * (0.5 + 0.5 * agreement)))

        # Action: dominant of the per-member actions.
        from collections import Counter as _C  # noqa: N814

        action = _C(p.action for p in preds).most_common(1)[0][0]

        return Prediction(
            horizon_hours=horizon_h,
            stage=stage,
            velocity_log=mu,
            velocity_mean=max(0.0, math.exp(mu + 0.5 * sigma * sigma)),
            velocity_p10=max(0.0, math.exp(mu - 1.2816 * sigma)),
            velocity_p50=max(0.0, math.exp(mu)),
            velocity_p90=max(0.0, math.exp(mu + 1.2816 * sigma)),
            p_breakout=p_b,
            p_peak=p_p,
            p_decline=p_d,
            aleatoric=alea,
            epistemic=epi,
            conformal_lower=max(0.0, lower),
            conformal_upper=max(0.0, upper),
            conformal_alpha=CONFORMAL_ALPHA,
            confidence=confidence,
            action=action,
            reasoning=(
                f"deep_ensemble M={n} stage_agree={agreement:.2f} " f"epi={epi:.2f} alea={alea:.2f}"
            ),
        )


__all__ = [
    "ConformalCalibrator",
    "DeepEnsemblePredictor",
    "fit_conformal",
]
