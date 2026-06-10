"""Unit tests for OutcomeRecorder."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from aegis.evolve.outcomes import OutcomeRecorder
from aegis.evolve.schemas import TradeOutcome


def _make_outcome(**kwargs) -> TradeOutcome:
    defaults: dict = {
        "execution_plan_id": "plan-1",
        "trend_id": "trend-1",
        "prediction_score": 0.8,
        "prediction_confidence": 0.9,
        "actual_roi_pct": Decimal("25.0"),
        "pnl_usd": Decimal("100.0"),
        "units_sold": 5,
        "resolution_status": "successful",
    }
    defaults.update(kwargs)
    return TradeOutcome(**defaults)


def _make_pool(rows=None, scalar=None):
    """Build a minimal asyncpg pool mock."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchrow = AsyncMock(return_value={"n": scalar} if scalar is not None else None)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class TestOutcomeRecorder:
    @pytest.mark.asyncio
    async def test_record_outcome_success(self) -> None:
        pool, conn = _make_pool()
        recorder = OutcomeRecorder(pool)
        outcome = _make_outcome()
        result = await recorder.record_outcome(outcome)
        assert result is True
        assert conn.execute.call_count == 2  # set_config + INSERT

    @pytest.mark.asyncio
    async def test_record_outcome_db_failure_returns_false(self) -> None:
        pool, conn = _make_pool()
        conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
        recorder = OutcomeRecorder(pool)
        outcome = _make_outcome()
        result = await recorder.record_outcome(outcome)
        assert result is False

    @pytest.mark.asyncio
    async def test_record_outcome_sql_args(self) -> None:
        pool, conn = _make_pool()
        recorder = OutcomeRecorder(pool)
        outcome = _make_outcome(units_sold=10, resolution_status="partial_refund")
        await recorder.record_outcome(outcome)
        call_args = conn.execute.call_args[0]
        # positional args: sql, outcome_id, plan_id, trend_id, score, conf, roi, pnl, units, returned, ts, status, metadata
        assert call_args[1] == outcome.outcome_id
        assert call_args[2] == "plan-1"
        assert call_args[3] == "trend-1"
        assert call_args[4] == pytest.approx(0.8)
        assert call_args[8] == 10
        assert call_args[11] == "partial_refund"

    @pytest.mark.asyncio
    async def test_fetch_recent_outcomes_empty(self) -> None:
        pool, _ = _make_pool(rows=[])
        recorder = OutcomeRecorder(pool)
        outcomes = await recorder.fetch_recent_outcomes()
        assert outcomes == []

    @pytest.mark.asyncio
    async def test_fetch_recent_outcomes_parses_metadata(self) -> None:
        outcome = _make_outcome()
        metadata = outcome.model_dump(mode="json")
        row = MagicMock()
        row.__getitem__ = lambda self, k: json.dumps(metadata)
        pool, conn = _make_pool(rows=[row])
        recorder = OutcomeRecorder(pool)
        outcomes = await recorder.fetch_recent_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].execution_plan_id == "plan-1"

    @pytest.mark.asyncio
    async def test_fetch_recent_outcomes_skips_bad_rows(self) -> None:
        bad_row = MagicMock()
        bad_row.__getitem__ = lambda self, k: '{"broken": true'  # invalid JSON
        pool, conn = _make_pool(rows=[bad_row])
        conn.fetch = AsyncMock(return_value=[bad_row])
        recorder = OutcomeRecorder(pool)
        outcomes = await recorder.fetch_recent_outcomes()
        assert outcomes == []

    @pytest.mark.asyncio
    async def test_fetch_recent_outcomes_db_failure(self) -> None:
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(side_effect=RuntimeError("connection lost"))
        recorder = OutcomeRecorder(pool)
        outcomes = await recorder.fetch_recent_outcomes()
        assert outcomes == []

    @pytest.mark.asyncio
    async def test_count_recent_outcomes(self) -> None:
        pool, conn = _make_pool(scalar=42)
        recorder = OutcomeRecorder(pool)
        n = await recorder.count_recent_outcomes(days_back=7)
        assert n == 42

    @pytest.mark.asyncio
    async def test_count_recent_outcomes_db_failure(self) -> None:
        pool, conn = _make_pool()
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("timeout"))
        recorder = OutcomeRecorder(pool)
        n = await recorder.count_recent_outcomes()
        assert n == 0

    @pytest.mark.asyncio
    async def test_count_recent_outcomes_none_row(self) -> None:
        pool, conn = _make_pool()
        conn.fetchrow = AsyncMock(return_value=None)
        recorder = OutcomeRecorder(pool)
        n = await recorder.count_recent_outcomes()
        assert n == 0

    @pytest.mark.asyncio
    async def test_fetch_outcomes_for_drift(self) -> None:
        row = {"prediction_score": 0.75, "prediction_confidence": 0.88}
        pool, conn = _make_pool(rows=[row])
        conn.fetch = AsyncMock(return_value=[row])
        recorder = OutcomeRecorder(pool)
        items = await recorder.fetch_outcomes_for_drift()
        assert len(items) == 1
        assert items[0]["prediction_score"] == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_fetch_outcomes_for_drift_failure(self) -> None:
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(side_effect=RuntimeError("db error"))
        recorder = OutcomeRecorder(pool)
        items = await recorder.fetch_outcomes_for_drift()
        assert items == []

    @pytest.mark.asyncio
    async def test_record_multiple_distinct_ids(self) -> None:
        pool, _ = _make_pool()
        recorder = OutcomeRecorder(pool)
        ids: set[str] = set()
        for i in range(5):
            o = _make_outcome(execution_plan_id=f"plan-{i}")
            await recorder.record_outcome(o)
            ids.add(o.outcome_id)
        assert len(ids) == 5
