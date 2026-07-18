"""Phase 3 → Phase 4 bridge.

Accepts a Phase 3 `InferenceResult` (or any duck-typed equivalent producing
a `PredictionBundle` over horizons) and folds it into a `ComposerInput`.

A `InferenceResult` is expected to expose:
    .trend_id : str
    .bundle   : PredictionBundle
        .predictions: list[Prediction]  (one per horizon)
            .horizon_hours: int
            .p_breakout: float
            .p_decline: float
            .p_saturation: float
            .expected_margin_usd: float | None
            .loss_probability: float | None
            .confidence: float
    .policy_action : str | None   ("enter" / "hold" / "exit")

The bridge picks:
  - `p_breakout` from the 24h horizon (configurable).
  - `p_decline`  from the 6h  horizon (configurable).
  - `p_saturation` from the largest horizon present.
  - aggregate confidence = mean across horizons.

If a chosen horizon is absent, falls back to the closest horizon and logs.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aegis.execute.bridge.types import ComposerInput
from uuid import UUID

import structlog

_log = structlog.get_logger(__name__)


@runtime_checkable
class _PredictionLike(Protocol):
    horizon_hours: int
    p_breakout: float
    p_decline: float
    p_saturation: float
    expected_margin_usd: float | None
    loss_probability: float | None
    confidence: float


def _pick_by_horizon(
    predictions: Iterable[_PredictionLike], target_hours: int
) -> _PredictionLike | None:
    """Pick the prediction whose horizon is closest to `target_hours`."""
    preds = list(predictions)
    if not preds:
        return None
    return min(preds, key=lambda p: abs(int(p.horizon_hours) - int(target_hours)))


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def from_inference_result(
    result: Any,
    *,
    tenant_id: UUID,
    decision_window: str = "default",
    correlation_id: str | None = None,
    capital_budget_usd: float | None = None,
    breakout_horizon_h: int = 24,
    decline_horizon_h: int = 6,
) -> ComposerInput:
    """Bridge a Phase 3 InferenceResult → ComposerInput.

    Tolerant of partial data: if `bundle.predictions` is empty, returns a
    ComposerInput with phase3_* fields all None.
    """
    from aegis.execute.bridge.types import ComposerInput

    trend_id = str(getattr(result, "trend_id", "unknown"))
    bundle = getattr(result, "bundle", None)
    predictions: list[_PredictionLike] = []
    if bundle is not None:
        raw = getattr(bundle, "predictions", None) or []
        for p in raw:
            if isinstance(p, _PredictionLike):  # type: ignore[arg-type]
                predictions.append(p)
            else:
                _log.debug(
                    "phase3_bridge.skipping_non_prediction",
                    trend_id=trend_id,
                    type=type(p).__name__,
                )

    breakout_pred = _pick_by_horizon(predictions, breakout_horizon_h)
    decline_pred = _pick_by_horizon(predictions, decline_horizon_h)
    saturation_pred = (
        max(predictions, key=lambda p: int(p.horizon_hours)) if predictions else None
    )

    confidences = [float(p.confidence) for p in predictions if p.confidence is not None]
    margins = [
        float(p.expected_margin_usd)
        for p in predictions
        if p.expected_margin_usd is not None
    ]
    loss_probs = [
        float(p.loss_probability)
        for p in predictions
        if p.loss_probability is not None
    ]

    policy_action = getattr(result, "policy_action", None)
    if policy_action is not None:
        policy_action = str(policy_action).lower()

    return ComposerInput(
        tenant_id=tenant_id,
        trend_id=trend_id,
        decision_window=decision_window,
        correlation_id=correlation_id,
        phase3_p_breakout_24h=(
            float(breakout_pred.p_breakout) if breakout_pred is not None else None
        ),
        phase3_p_decline_6h=(
            float(decline_pred.p_decline) if decline_pred is not None else None
        ),
        phase3_p_saturation=(
            float(saturation_pred.p_saturation) if saturation_pred is not None else None
        ),
        phase3_expected_margin_usd=_mean(margins),
        phase3_loss_probability=_mean(loss_probs),
        phase3_confidence=_mean(confidences),
        phase3_policy_action=policy_action,
        capital_budget_usd=capital_budget_usd,
    )


__all__ = ["from_inference_result"]
