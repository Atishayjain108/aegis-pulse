"""Tests for `aegis.agents_phase3_glue.bridge`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.agents_phase3_glue.bridge import (
    enrich_scout_decision,
    enrich_sentinel_decision,
    inference_to_agent_decision,
)
from aegis.predict.schemas import (
    ModelKind,
    Prediction,
    PredictionAction,
    PredictionBundle,
    TrendStage,
    UncertaintyMethod,
)


def _bundle_with_action(action: PredictionAction, p_breakout: float = 0.5) -> PredictionBundle:
    p = Prediction(
        horizon_hours=24,
        stage=TrendStage.EMERGING,
        velocity_log=0.5,
        velocity_mean=1.6,
        velocity_p10=0.5,
        velocity_p50=1.6,
        velocity_p90=3.0,
        p_breakout=p_breakout,
        p_peak=0.0,
        p_decline=0.0,
        confidence=0.7,
        action=action,
        reasoning="test",
    )
    now = datetime.now(UTC)
    return PredictionBundle(
        trend_id="t",
        correlation_id="c",
        predictions=[p],
        model_id="m",
        model_kind=ModelKind.HEURISTIC,
        model_version="1",
        uncertainty_method=UncertaintyMethod.NONE,
        started_at=now,
        finished_at=now,
        duration_ms=1.0,
        feature_window_hash="h",
    )


class _FakeResult:
    """A minimal stand-in for InferenceResult."""

    def __init__(self, bundle, halt_reasons=(), causal=()):
        self.bundle = bundle
        self.halt_reasons = halt_reasons
        self.causal = causal
        self.audit = None
        self.duration_ms = 0.0
        self.graph_summary = {}


class TestActionMapping:
    def test_enter_maps_to_advance(self):
        b = _bundle_with_action(PredictionAction.ENTER, p_breakout=0.85)
        d = inference_to_agent_decision(_FakeResult(b), agent_name="SCOUT")
        assert d["verdict"] == "advance"
        assert d["halt"] is False
        assert d["score"] == pytest.approx(0.85)

    def test_avoid_maps_to_block(self):
        b = _bundle_with_action(PredictionAction.AVOID)
        d = inference_to_agent_decision(_FakeResult(b), agent_name="SCOUT")
        assert d["verdict"] == "block"
        assert d["halt"] is True

    def test_exit_maps_to_exit(self):
        b = _bundle_with_action(PredictionAction.EXIT)
        d = inference_to_agent_decision(_FakeResult(b), agent_name="SENTINEL")
        assert d["verdict"] == "exit"
        assert d["halt"] is True

    def test_observe_maps_to_hold(self):
        b = _bundle_with_action(PredictionAction.OBSERVE)
        d = inference_to_agent_decision(_FakeResult(b), agent_name="SCOUT")
        assert d["verdict"] == "hold"
        assert d["halt"] is False

    def test_sentinel_uses_p_decline(self):
        # Sentinel scoring axis is p_decline.
        p = Prediction(
            horizon_hours=24,
            stage=TrendStage.DECLINING,
            velocity_log=0.0,
            velocity_mean=0.0,
            velocity_p10=0.0,
            velocity_p50=0.0,
            velocity_p90=0.0,
            p_breakout=0.0,
            p_peak=0.0,
            p_decline=0.7,
            confidence=0.6,
            action=PredictionAction.EXIT,
        )
        now = datetime.now(UTC)
        b = PredictionBundle(
            trend_id="t",
            correlation_id="c",
            predictions=[p],
            model_id="m",
            model_kind=ModelKind.HEURISTIC,
            model_version="1",
            uncertainty_method=UncertaintyMethod.NONE,
            started_at=now,
            finished_at=now,
            duration_ms=1.0,
            feature_window_hash="h",
        )
        d = inference_to_agent_decision(_FakeResult(b), agent_name="SENTINEL")
        assert d["score"] == pytest.approx(0.7)


class TestEnrichScoutDecision:
    @pytest.mark.asyncio
    async def test_enrich_with_signals(self, synthetic_signals):
        state = {
            "trend_id": "t1",
            "tenant_id": "default",
            "signals": synthetic_signals,
        }
        out = await enrich_scout_decision(state)
        assert "phase3_decision" in out
        assert out["phase3_decision"]["agent_name"] == "SCOUT"
        # original state untouched
        assert "phase3_decision" not in state

    @pytest.mark.asyncio
    async def test_enrich_missing_trend_id_skips(self):
        state = {"tenant_id": "default", "signals": []}
        out = await enrich_scout_decision(state)
        assert "phase3_decision" not in out

    @pytest.mark.asyncio
    async def test_enrich_sentinel_uses_short_horizon(self, synthetic_signals):
        state = {
            "trend_id": "t1",
            "tenant_id": "default",
            "signals": synthetic_signals,
        }
        out = await enrich_sentinel_decision(state)
        # Decision was constructed against the SENTINEL primary_horizon.
        assert out["phase3_decision"]["agent_name"] == "SENTINEL"
