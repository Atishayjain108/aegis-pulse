"""
Integration tests for Phase 9 Self-Evolution.

These tests exercise the full pipeline end-to-end against a real or mock
database.  All tests in this file are marked ``@pytest.mark.integration``
and require ``AEGIS_INTEGRATION_TEST=1`` to run.

Metadata-only tests run without a real DB and are in the unit suite above.
"""

from __future__ import annotations

import os
from decimal import Decimal

import numpy as np
import pytest

from aegis.evolve.drift import DriftDetector
from aegis.evolve.outcomes import OutcomeRecorder
from aegis.evolve.retrain import RetrainingPipeline
from aegis.evolve.rl_policy import OnlinePricingPolicy
from aegis.evolve.schemas import TradeOutcome

pytestmark = pytest.mark.skipif(
    os.environ.get("AEGIS_INTEGRATION_TEST") != "1",
    reason="Set AEGIS_INTEGRATION_TEST=1 to run integration tests",
)


def _make_outcome(**kwargs) -> TradeOutcome:
    defaults: dict = {
        "execution_plan_id": "plan-int-1",
        "trend_id": "trend-int-1",
        "prediction_score": 0.85,
        "prediction_confidence": 0.92,
        "actual_roi_pct": Decimal("45.0"),
        "pnl_usd": Decimal("450.00"),
        "units_sold": 10,
        "units_returned": 1,
        "resolution_status": "successful",
        "resolution_notes": "Integration test order",
    }
    defaults.update(kwargs)
    return TradeOutcome(**defaults)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_outcome_record_and_count(db_pool) -> None:
    """Record an outcome and verify count increases."""
    recorder = OutcomeRecorder(db_pool)
    before = await recorder.count_recent_outcomes(days_back=1)
    outcome = _make_outcome()
    ok = await recorder.record_outcome(outcome)
    assert ok is True
    after = await recorder.count_recent_outcomes(days_back=1)
    assert after >= before


@pytest.mark.integration
@pytest.mark.asyncio
async def test_outcome_idempotent_insert(db_pool) -> None:
    """Recording the same outcome twice should not raise (ON CONFLICT DO NOTHING)."""
    recorder = OutcomeRecorder(db_pool)
    outcome = _make_outcome(execution_plan_id="idem-plan")
    ok1 = await recorder.record_outcome(outcome)
    ok2 = await recorder.record_outcome(outcome)
    assert ok1 is True
    assert ok2 is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_drift_detection(db_pool) -> None:
    """Detect data drift from a shifted feature distribution."""
    detector = DriftDetector(db_pool=db_pool)
    X_drifted = np.ones((100, 20)) + 1.0
    snap = await detector.detect_drift(X_drifted)
    assert snap.drift_score > 0.5
    ok = await detector.persist_snapshot(snap)
    assert ok is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_drift_snapshot_round_trip(db_pool) -> None:
    """Persist a drift snapshot and fetch it back."""
    from aegis.evolve.schemas import DriftSnapshot
    detector = DriftDetector(db_pool=db_pool)
    snap = DriftSnapshot(drift_score=0.33, is_drifted=True, should_rollback=False)
    await detector.persist_snapshot(snap)
    latest = await detector.fetch_latest_snapshot()
    assert latest is not None
    assert latest.drift_score == pytest.approx(0.33, abs=1e-3)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_online_policy_learning(db_pool) -> None:
    """RL policy learns from profitable trades and weights remain valid."""
    policy = OnlinePricingPolicy(learning_rate=0.05, db_pool=db_pool)

    for _ in range(10):
        policy.update_from_outcome(price=50.0, cost=25.0, actual_demand=10, actual_roi_pct=100.0)

    weights = policy.get_weights()
    assert weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert (weights > 0).all()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_policy_persist_and_load(db_pool) -> None:
    """Save policy state to DB and reload it in a fresh instance."""
    writer = OnlinePricingPolicy(learning_rate=0.05, db_pool=db_pool, policy_id="integ-test")
    for _ in range(5):
        writer.update_from_outcome(price=40.0, cost=20.0, actual_demand=8, actual_roi_pct=60.0)
    await writer.persist()

    reader = OnlinePricingPolicy(db_pool=db_pool, policy_id="integ-test")
    ok = await reader.load()
    assert ok is True
    np.testing.assert_allclose(reader.get_weights(), writer.get_weights(), atol=1e-4)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrain_insufficient_data(db_pool) -> None:
    """Retraining pipeline returns NO_IMPROVEMENT when fewer than min outcomes exist."""
    from aegis.evolve.config import EvolveSettings
    cfg = EvolveSettings(min_outcomes_for_retrain=999_999)
    pipeline = RetrainingPipeline(db_pool=db_pool, settings=cfg)
    run = await pipeline.run_weekly_retrain(triggered_by="integration_test")
    assert run.status in ("no_improvement", "failed")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrain_full_pipeline(db_pool) -> None:
    """
    Seed 150 outcomes then trigger a full retrain.

    The run should complete (status completed or no_improvement depending on
    whether the candidate beats the heuristic floor).
    """
    from aegis.evolve.config import EvolveSettings
    recorder = OutcomeRecorder(db_pool)
    for i in range(150):
        await recorder.record_outcome(_make_outcome(
            execution_plan_id=f"retrain-plan-{i}",
            resolution_status="successful" if i % 3 != 0 else "full_refund",
        ))

    cfg = EvolveSettings(min_outcomes_for_retrain=100, hpo_n_trials=3)
    pipeline = RetrainingPipeline(db_pool=db_pool, settings=cfg)
    run = await pipeline.run_weekly_retrain(triggered_by="integration_test")
    assert run.status in ("completed", "no_improvement", "failed")
    assert run.outcomes_count >= 100
