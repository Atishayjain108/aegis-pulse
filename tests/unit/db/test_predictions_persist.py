"""Tests for Phase 3 prediction persistence (the OMEGA wiring fix).

For the whole project history the inference pipeline built a PredictionRecord
and discarded it — `predictions` stayed empty. These tests pin the new write
path: it maps a PredictionBundle onto the table, sets the RLS tenant, is
idempotent on correlation_id, and never raises.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.db.predictions import insert_prediction
from aegis.predict.schemas import (
    Prediction,
    PredictionAction,
    PredictionBundle,
    TrendStage,
)


class _FakeConn:
    def __init__(self, *, fail: bool = False, conflict: bool = False):
        self.executed: list[tuple[str, tuple]] = []
        self._fail = fail
        self._conflict = conflict

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if self._fail and "INSERT" in query:
            raise RuntimeError("db exploded")
        if "INSERT" in query:
            return "INSERT 0 0" if self._conflict else "INSERT 0 1"
        return "SET"


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


def _bundle() -> PredictionBundle:
    now = datetime.now(UTC)
    pred = Prediction(
        horizon_hours=24,
        stage=TrendStage.BREAKOUT,
        velocity_log=0.0,
        velocity_mean=1.0,
        velocity_p10=0.5,
        velocity_p50=1.0,
        velocity_p90=1.5,
        p_breakout=0.6,
        p_peak=0.1,
        p_decline=0.2,
        confidence=0.7,
        action=PredictionAction.ENTER,
    )
    return PredictionBundle(
        trend_id="t-1",
        correlation_id="corr-1",
        tenant_id="default",
        predictions=[pred],
        model_id="heuristic-abc",
        model_kind="heuristic",
        model_version="1.0",
        uncertainty_method="none",
        started_at=now,
        finished_at=now,
        duration_ms=5.0,
        feature_window_hash="hash-1",
    )


@pytest.mark.asyncio
async def test_insert_sets_tenant_and_writes_row():
    conn = _FakeConn()
    ok = await insert_prediction(_FakePool(conn), _bundle(), tenant_id="tid-1")
    assert ok is True
    # First statement sets the RLS tenant GUC; second is the INSERT.
    assert "set_config" in conn.executed[0][0]
    assert "INSERT INTO predictions" in conn.executed[1][0]
    # bundle_json carries the full bundle for replay.
    assert any("t-1" in str(a) for a in conn.executed[1][1])


@pytest.mark.asyncio
async def test_conflict_returns_false_not_raise():
    conn = _FakeConn(conflict=True)
    ok = await insert_prediction(_FakePool(conn), _bundle())
    assert ok is False  # ON CONFLICT DO NOTHING → 0 rows


@pytest.mark.asyncio
async def test_persist_never_raises_on_db_error():
    conn = _FakeConn(fail=True)
    ok = await insert_prediction(_FakePool(conn), _bundle())
    assert ok is False  # swallowed, advisory write
