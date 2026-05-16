"""
Promotion gates: deterministic rules for moving a model from
staging → shadow → production, and for auto-rollback.

These rules are intentionally numeric and side-effect-free. The
registry calls `evaluate_promotion()`, gets a `PromotionDecision`, and
then either calls `store.promote()` or surfaces the rejection reasons.

The gate is the only place where a candidate model's metrics get
compared to incumbent metrics. Putting the policy here means we can
unit-test promotions exhaustively without ever instantiating a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..backtest.runner import AggregatedMetrics
from ..constants import (
    AUTO_ROLLBACK_PRECISION_DROP,
    PROMOTION_F1_MARGIN,
    PROMOTION_MIN_BACKTEST_SAMPLES,
    SHADOW_DEPLOY_HOURS,
)


@dataclass(frozen=True, slots=True)
class PromotionGate:
    """Hard thresholds. All rationale lives in `constants.py`."""

    f1_margin: float = PROMOTION_F1_MARGIN
    min_samples: int = PROMOTION_MIN_BACKTEST_SAMPLES
    shadow_hours: int = SHADOW_DEPLOY_HOURS
    rollback_precision_drop: float = AUTO_ROLLBACK_PRECISION_DROP


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Result of `evaluate_promotion`. Always non-throwing."""

    promote: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    suggested_stage: str = "staging"


def evaluate_promotion(
    candidate: AggregatedMetrics,
    incumbent: AggregatedMetrics | None,
    gate: PromotionGate = PromotionGate(),
) -> PromotionDecision:
    """Decide whether `candidate` should replace `incumbent`.

    Rules (priority order):
      1. Sample floor — too few backtest samples → never promote.
      2. No incumbent → straight to shadow (never production).
      3. F1 margin — candidate must beat incumbent macro-F1 by
         `gate.f1_margin`.
      4. Precision tie-break — reject if breakout-precision drops
         by more than `rollback_precision_drop`.

    Winners always go to "shadow" first. Production promotion happens
    only after `shadow_hours` of live observation; that timer is
    tracked by the inference service, not here.
    """
    reasons: list[str] = []

    if candidate.n_test_total < gate.min_samples:
        reasons.append(f"insufficient_backtest_samples:{candidate.n_test_total}<{gate.min_samples}")
        return PromotionDecision(False, tuple(reasons), suggested_stage="staging")

    if incumbent is None:
        reasons.append("no_incumbent:going_to_shadow")
        return PromotionDecision(True, tuple(reasons), suggested_stage="shadow")

    f1_delta = candidate.macro_f1 - incumbent.macro_f1
    if f1_delta < gate.f1_margin:
        reasons.append(f"f1_margin_not_met:Δf1={f1_delta:+.4f}<required:{gate.f1_margin:+.4f}")
        return PromotionDecision(False, tuple(reasons), suggested_stage="staging")

    precision_drop = incumbent.breakout_precision - candidate.breakout_precision
    if precision_drop > gate.rollback_precision_drop:
        reasons.append(
            f"precision_regression:Δprec={-precision_drop:+.4f}>"
            f"tolerated:{-gate.rollback_precision_drop:+.4f}"
        )
        return PromotionDecision(False, tuple(reasons), suggested_stage="staging")

    reasons.append(f"f1_margin_met:Δf1={f1_delta:+.4f}>={gate.f1_margin:+.4f}")
    return PromotionDecision(True, tuple(reasons), suggested_stage="shadow")


def evaluate_rollback(
    live: AggregatedMetrics,
    incumbent_at_promotion: AggregatedMetrics,
    gate: PromotionGate = PromotionGate(),
) -> PromotionDecision:
    """Decide whether the live model has degraded enough to roll back."""
    if live.n_test_total < gate.min_samples:
        return PromotionDecision(
            False,
            (f"insufficient_live_samples:{live.n_test_total}<{gate.min_samples}",),
            suggested_stage="production",
        )

    drop = incumbent_at_promotion.breakout_precision - live.breakout_precision
    if drop > gate.rollback_precision_drop:
        return PromotionDecision(
            True,
            (
                f"auto_rollback:Δprec={-drop:+.4f}>tolerated:"
                f"{-gate.rollback_precision_drop:+.4f}",
            ),
            suggested_stage="archived",
        )
    return PromotionDecision(
        False,
        (f"within_tolerance:Δprec={-drop:+.4f}",),
        suggested_stage="production",
    )
