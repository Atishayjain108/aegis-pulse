"""
Execution policy.

Two layers, in priority order:

  1. HeuristicPolicy
     A deterministic fractional-Kelly sizer. It is the floor: every
     prediction yields a defined action even when no model is loaded
     and no Ray cluster exists.

  2. RL augmentation (optional)
     If a trained PPO policy is available we ask it for a *correction*
     to the Kelly action. The RL output multiplies the Kelly size in
     [0.5, 1.5] and may downgrade ENTER → HOLD or HOLD → EXIT, but
     can NEVER upgrade HOLD → ENTER without a Kelly recommendation
     to enter. (This mirrors Phase 2's "LLM cannot flip the verdict"
     rule for execution.)

The output schema (`PolicyDecision`) is what the executor in Phase 4
actually consumes.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

from ..constants import (
    KELLY_FRACTION,
    MAX_POSITION_FRACTION,
    PORTFOLIO_CORRELATION_CEILING,
    STOP_LOSS_DRAWDOWN,
)
from ..schemas import Prediction, PredictionAction

logger = logging.getLogger(__name__)


class KellyAction(str, enum.Enum):
    """Output action of the policy (different from PredictionAction).

    PredictionAction is what the *predictor* recommends abstractly
    (ENTER / HOLD / EXIT / AVOID / OBSERVE). KellyAction is what the
    *executor* should actually do, with sizing.
    """

    ENTER = "ENTER"
    SCALE_UP = "SCALE_UP"
    HOLD = "HOLD"
    SCALE_DOWN = "SCALE_DOWN"
    EXIT = "EXIT"
    NO_ACTION = "NO_ACTION"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Final executable decision.

    Fields:
        action: one of KellyAction
        size: fraction of allowed capital to allocate (0..MAX_POSITION_FRACTION)
        confidence: confidence of the prediction that drove this decision
        reason: short audit string
        kelly_size: raw Kelly sizing (before RL multiplier and clipping)
        rl_multiplier: RL correction applied to kelly_size, or 1.0
    """

    action: KellyAction
    size: float
    confidence: float
    reason: str
    kelly_size: float
    rl_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.size <= MAX_POSITION_FRACTION:
            raise ValueError(f"size {self.size} out of [0, {MAX_POSITION_FRACTION}]")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence} out of [0,1]")


def _kelly_size(p_win: float, payoff_ratio: float) -> float:
    """Return raw Kelly fraction.

    Standard formula: f* = p - (1-p)/b where b is payoff ratio. We
    clamp to [0, 1] and apply the fractional Kelly multiplier to
    stay risk-conservative. Fractional Kelly halves long-run growth
    rate but dramatically reduces drawdown variance, which is the
    right trade for a system trading without human supervision.
    """
    if payoff_ratio <= 0:
        return 0.0
    raw = p_win - (1.0 - p_win) / payoff_ratio
    raw = max(0.0, min(1.0, raw))
    return raw * KELLY_FRACTION


def _has_existing_position(state: dict, trend_id: str) -> bool:
    return trend_id in state.get("open_positions", {})


def _portfolio_correlation_ok(state: dict, prediction: Prediction) -> bool:
    """Block if the portfolio is already concentrated in correlated bets.

    The state dict is a stand-in for the executor's portfolio state.
    We don't compute true correlation here (that's the HEDGE agent's
    job in Phase 2); we approximate by counting open positions that
    share the platform of `prediction`. Above a ceiling we refuse to
    add another.
    """
    open_positions = state.get("open_positions", {})
    if len(open_positions) == 0:
        return True
    plat = prediction.metadata.get("primary_platform") if prediction.metadata else None
    if not plat:
        return True
    same_plat = sum(1 for v in open_positions.values() if v.get("primary_platform") == plat)
    correlation_proxy = same_plat / max(1, len(open_positions))
    return correlation_proxy <= PORTFOLIO_CORRELATION_CEILING


def _should_stop_out(state: dict, trend_id: str) -> bool:
    pos = state.get("open_positions", {}).get(trend_id)
    if not pos:
        return False
    drawdown = float(pos.get("drawdown", 0.0))
    return drawdown <= -abs(STOP_LOSS_DRAWDOWN)


@dataclass
class HeuristicPolicy:
    """Deterministic Kelly-based policy. Always available, no deps."""

    kelly_fraction: float = KELLY_FRACTION
    max_size: float = MAX_POSITION_FRACTION
    payoff_ratio: float = 3.0  # 3:1 reward:risk assumption (operator-tunable)

    def decide(
        self,
        prediction: Prediction,
        portfolio_state: dict | None = None,
    ) -> PolicyDecision:
        state = portfolio_state or {"open_positions": {}}
        trend_id = prediction.metadata.get("trend_id") if prediction.metadata else "unknown"

        # 1) Stop-loss override — fires regardless of prediction.
        if _should_stop_out(state, trend_id):
            return PolicyDecision(
                action=KellyAction.EXIT,
                size=0.0,
                confidence=prediction.confidence,
                reason="stop_loss_drawdown_breached",
                kelly_size=0.0,
            )

        held = _has_existing_position(state, trend_id)
        kelly_raw = _kelly_size(prediction.p_breakout, self.payoff_ratio)
        size = min(kelly_raw, self.max_size)

        # 2) Map PredictionAction → KellyAction with portfolio sanity checks.
        if prediction.action == PredictionAction.ENTER:
            if held:
                # Already in. Either scale up or hold — depends on
                # whether the new Kelly size is materially larger.
                current = state["open_positions"][trend_id].get("size", 0.0)
                if size > current * 1.10:  # >=10% bigger sizing
                    return PolicyDecision(
                        action=KellyAction.SCALE_UP,
                        size=size,
                        confidence=prediction.confidence,
                        reason="kelly_increased_>=10pct",
                        kelly_size=kelly_raw,
                    )
                return PolicyDecision(
                    action=KellyAction.HOLD,
                    size=current,
                    confidence=prediction.confidence,
                    reason="already_in_position",
                    kelly_size=kelly_raw,
                )
            if not _portfolio_correlation_ok(state, prediction):
                return PolicyDecision(
                    action=KellyAction.NO_ACTION,
                    size=0.0,
                    confidence=prediction.confidence,
                    reason="portfolio_correlation_ceiling",
                    kelly_size=kelly_raw,
                )
            if size <= 0.0:
                return PolicyDecision(
                    action=KellyAction.NO_ACTION,
                    size=0.0,
                    confidence=prediction.confidence,
                    reason="kelly_zero",
                    kelly_size=kelly_raw,
                )
            return PolicyDecision(
                action=KellyAction.ENTER,
                size=size,
                confidence=prediction.confidence,
                reason="enter_kelly",
                kelly_size=kelly_raw,
            )

        if prediction.action == PredictionAction.EXIT:
            if held:
                return PolicyDecision(
                    action=KellyAction.EXIT,
                    size=0.0,
                    confidence=prediction.confidence,
                    reason="prediction_exit",
                    kelly_size=0.0,
                )
            return PolicyDecision(
                action=KellyAction.NO_ACTION,
                size=0.0,
                confidence=prediction.confidence,
                reason="not_in_position",
                kelly_size=0.0,
            )

        if prediction.action == PredictionAction.HOLD and held:
            return PolicyDecision(
                action=KellyAction.HOLD,
                size=state["open_positions"][trend_id].get("size", 0.0),
                confidence=prediction.confidence,
                reason="prediction_hold",
                kelly_size=kelly_raw,
            )

        # AVOID, OBSERVE, or HOLD without position → no action.
        return PolicyDecision(
            action=KellyAction.NO_ACTION,
            size=0.0,
            confidence=prediction.confidence,
            reason=f"prediction_{prediction.action.value.lower()}",
            kelly_size=kelly_raw,
        )


def decide(
    prediction: Prediction,
    portfolio_state: dict | None = None,
    *,
    rl_multiplier: float = 1.0,
) -> PolicyDecision:
    """Public entry point.

    `rl_multiplier` is the optional adjustment from a trained RL policy.
    It is clamped to [0.5, 1.5] and applied AFTER the Kelly size — and
    cannot upgrade NO_ACTION → ENTER, only modify size of an existing
    ENTER/SCALE_UP decision. If your RL policy decides to pull out, it
    can pass `rl_multiplier=0.0`, which forces NO_ACTION even if Kelly
    wanted to enter.
    """
    rl_clamped = max(0.0, min(1.5, rl_multiplier))
    base = HeuristicPolicy().decide(prediction, portfolio_state)

    # Don't apply RL on stop-loss exits — those are non-negotiable.
    if base.reason == "stop_loss_drawdown_breached":
        return base

    if base.action in {KellyAction.ENTER, KellyAction.SCALE_UP}:
        new_size = min(base.size * rl_clamped, MAX_POSITION_FRACTION)
        if new_size <= 0.0:
            return PolicyDecision(
                action=KellyAction.NO_ACTION,
                size=0.0,
                confidence=base.confidence,
                reason=f"{base.reason}|rl_zeroed",
                kelly_size=base.kelly_size,
                rl_multiplier=rl_clamped,
            )
        return PolicyDecision(
            action=base.action,
            size=new_size,
            confidence=base.confidence,
            reason=f"{base.reason}|rl_x{rl_clamped:.2f}",
            kelly_size=base.kelly_size,
            rl_multiplier=rl_clamped,
        )

    return base
