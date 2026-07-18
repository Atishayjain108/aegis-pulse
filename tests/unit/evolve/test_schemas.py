"""Unit tests for Phase 9 Pydantic schemas."""

from __future__ import annotations

from decimal import Decimal

import pytest

from aegis.evolve.schemas import (
    DriftSnapshot,
    EvolveStatus,
    ModelCandidate,
    PolicyState,
    RetrainRun,
    TradeOutcome,
)

# ---------------------------------------------------------------------------
# TradeOutcome
# ---------------------------------------------------------------------------

class TestTradeOutcome:
    def test_default_fields(self) -> None:
        o = TradeOutcome(
            execution_plan_id="plan-1",
            trend_id="trend-1",
            prediction_score=0.8,
            prediction_confidence=0.9,
        )
        assert o.execution_plan_id == "plan-1"
        assert o.trend_id == "trend-1"
        assert o.prediction_score == 0.8
        assert o.prediction_confidence == 0.9
        assert o.resolution_status == "pending"
        assert o.units_sold == 0
        assert isinstance(o.outcome_id, str)
        assert len(o.outcome_id) > 0

    def test_auto_uuid(self) -> None:
        a = TradeOutcome(execution_plan_id="p1", trend_id="t1", prediction_score=0.5, prediction_confidence=0.5)
        b = TradeOutcome(execution_plan_id="p2", trend_id="t2", prediction_score=0.5, prediction_confidence=0.5)
        assert a.outcome_id != b.outcome_id

    def test_frozen(self) -> None:
        o = TradeOutcome(execution_plan_id="p", trend_id="t", prediction_score=0.5, prediction_confidence=0.5)
        with pytest.raises(Exception):
            o.prediction_score = 0.99  # type: ignore[misc]

    def test_score_bounds(self) -> None:
        with pytest.raises(Exception):
            TradeOutcome(execution_plan_id="p", trend_id="t", prediction_score=1.5, prediction_confidence=0.5)
        with pytest.raises(Exception):
            TradeOutcome(execution_plan_id="p", trend_id="t", prediction_score=-0.1, prediction_confidence=0.5)

    def test_full_outcome(self) -> None:
        o = TradeOutcome(
            execution_plan_id="plan-xyz",
            trend_id="trend-abc",
            prediction_score=0.85,
            prediction_confidence=0.92,
            actual_roi_pct=Decimal("45.0"),
            pnl_usd=Decimal("450.00"),
            units_sold=10,
            units_returned=1,
            avg_sale_price=Decimal("50.00"),
            total_cost=Decimal("200.00"),
            shipping_cost=Decimal("20.00"),
            platform_fee=Decimal("25.00"),
            days_to_fulfillment=3,
            resolution_status="successful",
            resolution_notes="Order delivered",
            market_conditions_at_exec={"signal_count": 50},
        )
        assert o.units_sold == 10
        assert o.resolution_status == "successful"
        assert o.market_conditions_at_exec["signal_count"] == 50

    def test_default_timestamp(self) -> None:
        o = TradeOutcome(execution_plan_id="p", trend_id="t", prediction_score=0.5, prediction_confidence=0.5)
        assert o.settlement_timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# ModelCandidate
# ---------------------------------------------------------------------------

class TestModelCandidate:
    def test_defaults(self) -> None:
        c = ModelCandidate(candidate_id="arch-20260602T120000", architecture="patchts")
        assert c.train_auc == 0.5
        assert c.test_auc == 0.5
        assert c.architecture == "patchts"

    def test_frozen(self) -> None:
        c = ModelCandidate(candidate_id="x", architecture="autoformer")
        with pytest.raises(Exception):
            c.test_auc = 0.99  # type: ignore[misc]

    def test_auc_bounds(self) -> None:
        with pytest.raises(Exception):
            ModelCandidate(candidate_id="x", architecture="a", test_auc=1.5)

    def test_hyperparameters(self) -> None:
        hp = {"d_model": 128, "lr": 1e-3}
        c = ModelCandidate(candidate_id="c1", architecture="patchts", hyperparameters=hp)
        assert c.hyperparameters["d_model"] == 128

    def test_auto_uuid(self) -> None:
        a = ModelCandidate(candidate_id="x", architecture="a")
        b = ModelCandidate(candidate_id="y", architecture="a")
        assert a.candidate_id != b.candidate_id


# ---------------------------------------------------------------------------
# DriftSnapshot
# ---------------------------------------------------------------------------

class TestDriftSnapshot:
    def test_defaults(self) -> None:
        s = DriftSnapshot(drift_score=0.12, is_drifted=False)
        assert s.drift_score == 0.12
        assert s.is_drifted is False
        assert s.should_rollback is False

    def test_drifted_snapshot(self) -> None:
        s = DriftSnapshot(
            drift_score=0.25,
            is_drifted=True,
            precision_now=0.60,
            precision_prev=0.70,
            precision_drop=0.143,
            should_rollback=True,
        )
        assert s.should_rollback is True
        assert s.precision_drop == pytest.approx(0.143, abs=1e-3)

    def test_frozen(self) -> None:
        s = DriftSnapshot(drift_score=0.1, is_drifted=False)
        with pytest.raises(Exception):
            s.drift_score = 0.9  # type: ignore[misc]

    def test_drift_score_bounds(self) -> None:
        with pytest.raises(Exception):
            DriftSnapshot(drift_score=1.5, is_drifted=False)
        with pytest.raises(Exception):
            DriftSnapshot(drift_score=-0.1, is_drifted=False)


# ---------------------------------------------------------------------------
# PolicyState
# ---------------------------------------------------------------------------

class TestPolicyState:
    def test_defaults(self) -> None:
        p = PolicyState()
        assert len(p.weights) == 4
        assert sum(p.weights) == pytest.approx(1.0, abs=1e-6)
        assert p.policy_id == "default"

    def test_update_count(self) -> None:
        p = PolicyState(update_count=500)
        assert p.update_count == 500

    def test_frozen(self) -> None:
        p = PolicyState()
        with pytest.raises(Exception):
            p.update_count = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RetrainRun
# ---------------------------------------------------------------------------

class TestRetrainRun:
    def test_defaults(self) -> None:
        r = RetrainRun()
        assert r.status == "running"
        assert r.triggered_by == "scheduler"
        assert r.outcomes_count == 0
        assert isinstance(r.run_id, str)

    def test_completed(self) -> None:
        r = RetrainRun(
            status="completed",
            triggered_by="manual",
            outcomes_count=250,
            champion_before="old-model",
            champion_after="new-model",
            improvement_pct=3.5,
        )
        assert r.improvement_pct == pytest.approx(3.5)
        assert r.champion_after == "new-model"

    def test_frozen(self) -> None:
        r = RetrainRun()
        with pytest.raises(Exception):
            r.status = "failed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EvolveStatus
# ---------------------------------------------------------------------------

class TestEvolveStatus:
    def test_defaults(self) -> None:
        s = EvolveStatus()
        assert s.champion_auc == 0.5
        assert s.is_drifted is False
        assert s.policy_update_count == 0
        assert len(s.policy_weights) == 4

    def test_full(self) -> None:
        s = EvolveStatus(
            champion_model_id="champion-1",
            champion_auc=0.72,
            last_retrain_status="completed",
            latest_drift_score=0.08,
            is_drifted=False,
            policy_update_count=1234,
            outcomes_last_30d=850,
        )
        assert s.champion_auc == pytest.approx(0.72)
        assert s.outcomes_last_30d == 850
