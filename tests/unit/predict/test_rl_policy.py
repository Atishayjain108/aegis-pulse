"""Tests for the RL execution policy."""

from __future__ import annotations

from aegis.predict.rl import (
    HeuristicPolicy,
    KellyAction,
    decide,
)
from aegis.predict.schemas import (
    ModelKind,
    Prediction,
    PredictionAction,
    TrendStage,
)


def _mk_pred(
    *,
    p_breakout: float = 0.7,
    confidence: float = 0.7,
    action: PredictionAction = PredictionAction.ENTER,
    stage: TrendStage = TrendStage.BREAKOUT,
    metadata: dict | None = None,
) -> Prediction:
    return Prediction(
        horizon_hours=24,
        stage=stage,
        velocity_log=0.5,
        velocity_mean=1.6,
        velocity_p10=0.5,
        velocity_p50=1.6,
        velocity_p90=3.0,
        p_breakout=p_breakout,
        p_peak=0.0,
        p_decline=0.0,
        confidence=confidence,
        action=action,
        metadata=metadata or {"trend_id": "t1"},
        model_kind=ModelKind.HEURISTIC,
    )


class TestHeuristicPolicy:
    def test_enter_within_position_cap(self):
        p = HeuristicPolicy().decide(_mk_pred(p_breakout=0.95))
        assert p.action == KellyAction.ENTER
        assert 0.0 < p.size <= 0.10

    def test_zero_kelly_yields_no_action(self):
        p = HeuristicPolicy().decide(_mk_pred(p_breakout=0.20))
        # 0.20 is below break-even → Kelly is 0 → NO_ACTION.
        assert p.action == KellyAction.NO_ACTION

    def test_held_position_scale_up(self):
        state = {
            "open_positions": {
                "t1": {"size": 0.02, "drawdown": 0.0, "primary_platform": "x"},
            }
        }
        p = HeuristicPolicy().decide(_mk_pred(p_breakout=0.95), state)
        assert p.action in {KellyAction.SCALE_UP, KellyAction.HOLD}

    def test_stop_loss_triggers_exit(self):
        state = {
            "open_positions": {
                "t1": {"size": 0.05, "drawdown": -0.30, "primary_platform": "x"},
            }
        }
        p = HeuristicPolicy().decide(_mk_pred(p_breakout=0.95), state)
        assert p.action == KellyAction.EXIT
        assert p.reason == "stop_loss_drawdown_breached"

    def test_exit_action_with_no_position(self):
        p = HeuristicPolicy().decide(_mk_pred(action=PredictionAction.EXIT, p_breakout=0.05))
        assert p.action == KellyAction.NO_ACTION

    def test_avoid_yields_no_action(self):
        p = HeuristicPolicy().decide(_mk_pred(action=PredictionAction.AVOID, p_breakout=0.10))
        assert p.action == KellyAction.NO_ACTION


class TestPolicyDecideWrapper:
    def test_rl_multiplier_zero_forces_no_action(self):
        p = decide(_mk_pred(p_breakout=0.95), rl_multiplier=0.0)
        assert p.action == KellyAction.NO_ACTION
        assert p.rl_multiplier == 0.0

    def test_rl_multiplier_clamped(self):
        p = decide(_mk_pred(p_breakout=0.95), rl_multiplier=99.0)
        assert p.action == KellyAction.ENTER
        assert p.rl_multiplier == 1.5  # clamped

    def test_rl_cannot_override_stop_loss(self):
        state = {
            "open_positions": {
                "t1": {"size": 0.05, "drawdown": -0.40, "primary_platform": "x"},
            }
        }
        p = decide(_mk_pred(p_breakout=0.95), state, rl_multiplier=1.5)
        assert p.action == KellyAction.EXIT  # RL cannot override safety


class TestSizeBounds:
    def test_size_never_exceeds_cap(self):
        for prob in (0.6, 0.75, 0.9, 0.99):
            p = decide(_mk_pred(p_breakout=prob))
            assert 0.0 <= p.size <= 0.10
