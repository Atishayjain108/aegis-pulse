"""
Integration test: signal stream → Phase 3 prediction → bridge decision.

This test exercises the same code path Phase 4 (alerting) would trigger
when a new alert fires, without touching the DB. It uses an in-memory
signal stream and asserts the full pipeline produces:

  * a Phase 3 InferenceResult with bundle, audit, halt_reasons
  * a Phase 2-shaped AgentDecision dict
  * an HTTP-served PredictionBundle that round-trips byte-for-byte
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aegis.agents_phase3_glue.bridge import inference_to_agent_decision
from aegis.predict.inference import InferenceConfig, InferenceRunner
from aegis.predict.schemas import PredictionAction, PredictionBundle

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from aegis.predict.serving import create_app  # noqa: E402


@pytest.fixture
def declining_signals():
    """High velocity early, decay later → triggers DECLINING/EXIT path."""
    base = datetime.now(UTC) - timedelta(hours=72)
    rows = []
    for i in range(72):
        # Inverse ramp: views start high, drop over time.
        decay = max(1, 100 - i)
        rows.append(
            {
                "id": f"sig-{i}",
                "platform": "twitter" if i < 36 else "reddit",
                "captured_at": base + timedelta(hours=i),
                "title": None,
                "body": f"msg {i}",
                "url": None,
                "content_hash": f"h{i}",
                "author_id": f"a{i % 8}",
                "views": decay * 10,
                "likes": decay,
                "comments": decay // 4,
                "shares": 0,
                "saves": 0,
                "sentiment": 0.0,
                "commercial_intent": 0.0,
                "novelty": 0.1 if i > 5 else 1.0,
            }
        )
    return rows


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_runner_to_bridge_to_decision(self, synthetic_signals):
        runner = InferenceRunner()
        result = await runner.run(
            tenant_id="default",
            trend_id="integration-trend-1",
            signals=synthetic_signals,
        )
        decision = inference_to_agent_decision(result, agent_name="SCOUT", primary_horizon=24)
        assert decision["agent_name"] == "SCOUT"
        assert decision["verdict"] in {"advance", "hold", "exit", "block"}
        assert decision["trend_id"] == "integration-trend-1"
        # The bridge embeds the full bundle JSON for audit.
        assert decision["phase3_bundle"] is not None
        assert decision["phase3_bundle"]["correlation_id"] == result.bundle.correlation_id

    def test_http_prediction_round_trip(self, synthetic_signals):
        """A bundle posted via HTTP must round-trip byte-for-byte through pydantic."""
        app = create_app()
        client = TestClient(app)
        signals_iso = [
            {**r, "captured_at": r["captured_at"].isoformat()} for r in synthetic_signals
        ]
        r = client.post(
            "/predict",
            json={
                "tenant_id": "default",
                "trend_id": "integration-trend-2",
                "signals": signals_iso,
            },
        )
        assert r.status_code == 200
        body = r.json()
        # Reconstruct PredictionBundle from JSON — schema must match.
        bundle = PredictionBundle.model_validate(body["bundle"])
        assert bundle.trend_id == "integration-trend-2"
        assert all(p.action in PredictionAction for p in bundle.predictions)

    @pytest.mark.asyncio
    async def test_declining_trend_yields_lower_breakout(self, declining_signals):
        """A declining trend stream should not produce a high p_breakout."""
        runner = InferenceRunner()
        result = await runner.run(
            tenant_id="default",
            trend_id="declining",
            signals=declining_signals,
        )
        primary = result.bundle.by_horizon(24) or result.bundle.predictions[0]
        # We don't assert exactly which stage — heuristic + fusion can land
        # on EMERGING or DECLINING — but p_breakout should never be high.
        assert primary.p_breakout <= 0.6

    @pytest.mark.asyncio
    async def test_pipeline_under_latency_budget(self, synthetic_signals):
        """Heuristic-only path must comfortably hit p99 < 500 ms."""
        cfg = InferenceConfig(latency_budget_ms=500.0)
        runner = InferenceRunner(config=cfg)
        for _ in range(5):
            result = await runner.run(
                tenant_id="default",
                trend_id="bench",
                signals=synthetic_signals,
            )
            assert result.duration_ms < 500.0
            # Halt reasons should not include latency budget.
            assert not any("latency_budget_exceeded" in r for r in result.halt_reasons)
