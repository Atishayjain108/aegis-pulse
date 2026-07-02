"""PROJECT OMEGA Phase D — S1 Execution Knowledge Engine + S2 Supplier Intel.

Query-aware fake pool, no live Postgres. Covers execution memory
record/log/outcome/upsert/fetch, supplier verification + fulfillment counters,
the Supplier Trust Score (including the UNVERIFIED-when-unmeasured invariant),
and schema defaults (Rule 1: assumptions default to UNVERIFIED).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from aegis.execution_intel import (
    UNVERIFIED,
    AssumptionKind,
    AssumptionStatus,
    ExecutionAssumption,
    ExecutionFailureCategory,
    ExecutionMemory,
    ExecutionOutcome,
    ExecutionRecord,
    SupplierIntel,
    SupplierTrustScore,
)

_NOW = datetime(2026, 6, 16, tzinfo=UTC)


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        self._pool.executed.append(" ".join(query.split()))
        self._pool.exec_args.append(args)
        return self._pool.exec_result

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split())
        if "FROM execution_records" in q:
            return self._pool.record_rows
        if "FROM supplier_reliability" in q:
            return self._pool.supplier_rows
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = " ".join(query.split())
        if "FROM supplier_reliability" in q:
            return self._pool.supplier_row
        if "FROM entities" in q:
            return self._pool.entity_id_row
        return None


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Conn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


class _Pool:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.exec_args: list[tuple[Any, ...]] = []
        self.exec_result: str = "OK"
        self.record_rows: list[dict[str, Any]] = []
        self.supplier_rows: list[dict[str, Any]] = []
        self.supplier_row: dict[str, Any] | None = None
        self.entity_id_row: dict[str, Any] | None = None

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))


def _record_row(**kw: Any) -> dict[str, Any]:
    base = {
        "record_id": "11111111-1111-1111-1111-111111111111",
        "plan_id": "plan-1",
        "trend_id": "trend-1",
        "opportunity_type": "product",
        "region": None,
        "supplier_name": "printful",
        "planned_units": 10,
        "planned_unit_cost_usd": 5.0,
        "planned_margin_pct": 0.3,
        "outcome": "succeeded",
        "realized_units": 10,
        "realized_pnl_usd": 42.0,
        "realized_cost_usd": 50.0,
        "delay_hours": None,
        "failure_category": "none",
        "failure_detail": "",
        "created_at": _NOW,
        "settled_at": _NOW,
        "settlement_timestamp": _NOW,
        "metadata": "{}",
    }
    base.update(kw)
    return base


def _supplier_row(**kw: Any) -> dict[str, Any]:
    base = {
        "supplier_name": "printful",
        "n_verifications": 0,
        "n_verified": 0,
        "n_fulfillments": 0,
        "n_fulfilled_ok": 0,
        "n_delays": 0,
        "n_cancellations": 0,
        "total_response_ms": 0,
        "n_responses": 0,
        "updated_at": _NOW,
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# Schema / taxonomy invariants (Rule 1)
# --------------------------------------------------------------------------- #


def test_assumption_defaults_to_unverified() -> None:
    a = ExecutionAssumption(plan_id="p", kind=AssumptionKind.SUPPLIER_EXISTS)
    assert a.status is AssumptionStatus.UNVERIFIED
    assert a.verified_via is None


def test_supplier_trust_unverified_when_no_fulfillment() -> None:
    s = SupplierTrustScore(supplier_name="x")
    assert s.trust is None
    assert s.is_verified is False
    assert UNVERIFIED == "UNVERIFIED"


def test_execution_record_pending_by_default() -> None:
    r = ExecutionRecord(plan_id="p")
    assert r.outcome is ExecutionOutcome.PENDING
    assert r.failure_category is ExecutionFailureCategory.NONE


# --------------------------------------------------------------------------- #
# ExecutionMemory (S1)
# --------------------------------------------------------------------------- #


async def test_record_plan_inserts() -> None:
    pool = _Pool()
    mem = ExecutionMemory(pool)  # type: ignore[arg-type]
    ok = await mem.record_plan(ExecutionRecord(plan_id="plan-1", supplier_name="printful"))
    assert ok is True
    assert any("INSERT INTO execution_records" in q for q in pool.executed)


async def test_log_assumption_persists_status() -> None:
    pool = _Pool()
    mem = ExecutionMemory(pool)  # type: ignore[arg-type]
    ok = await mem.log_assumption(
        ExecutionAssumption(
            plan_id="plan-1",
            kind=AssumptionKind.INVENTORY,
            status=AssumptionStatus.VERIFIED,
            verified_via="printful_api",
        )
    )
    assert ok is True
    assert any("INSERT INTO execution_assumptions" in q for q in pool.executed)


async def test_record_outcome_updates_existing() -> None:
    pool = _Pool()
    pool.exec_result = "UPDATE 1"
    mem = ExecutionMemory(pool)  # type: ignore[arg-type]
    ok = await mem.record_outcome(
        "plan-1", ExecutionOutcome.SUCCEEDED, realized_pnl_usd=42.0,
    )
    assert ok is True


async def test_record_outcome_missing_plan_returns_false() -> None:
    pool = _Pool()
    pool.exec_result = "UPDATE 0"
    mem = ExecutionMemory(pool)  # type: ignore[arg-type]
    ok = await mem.record_outcome("ghost", ExecutionOutcome.FAILED)
    assert ok is False


async def test_upsert_settled_born_settled() -> None:
    pool = _Pool()
    mem = ExecutionMemory(pool)  # type: ignore[arg-type]
    ok = await mem.upsert_settled(
        "plan-9",
        ExecutionOutcome.FAILED,
        trend_id="t9",
        realized_pnl_usd=-3.0,
        failure_category=ExecutionFailureCategory.MARGIN_EVAPORATED,
    )
    assert ok is True
    assert any("ON CONFLICT" in q for q in pool.executed)


async def test_fetch_recent_maps_rows() -> None:
    pool = _Pool()
    pool.record_rows = [_record_row()]
    mem = ExecutionMemory(pool)  # type: ignore[arg-type]
    recs = await mem.fetch_recent(limit=5)
    assert len(recs) == 1
    assert recs[0].plan_id == "plan-1"
    assert recs[0].outcome is ExecutionOutcome.SUCCEEDED
    assert recs[0].realized_pnl_usd == 42.0


async def test_fetch_recent_by_outcome_filters() -> None:
    pool = _Pool()
    pool.record_rows = [_record_row(outcome="failed", failure_category="shipping_delay")]
    mem = ExecutionMemory(pool)  # type: ignore[arg-type]
    recs = await mem.fetch_recent(outcome=ExecutionOutcome.FAILED)
    assert recs[0].failure_category is ExecutionFailureCategory.SHIPPING_DELAY


async def test_memory_none_pool_is_safe() -> None:
    mem = ExecutionMemory(None)  # type: ignore[arg-type]
    assert await mem.record_plan(ExecutionRecord(plan_id="p")) is False
    assert await mem.fetch_recent() == []


# --------------------------------------------------------------------------- #
# SupplierIntel (S2)
# --------------------------------------------------------------------------- #


async def test_record_verification_bumps_counters() -> None:
    pool = _Pool()
    intel = SupplierIntel(pool)  # type: ignore[arg-type]
    ok = await intel.record_verification("Printful", verified=True, response_ms=120.0)
    assert ok is True
    # entity upsert + reliability upsert both ran
    assert any("INSERT INTO entities" in q for q in pool.executed)
    assert any("INSERT INTO supplier_reliability" in q for q in pool.executed)


async def test_record_fulfillment_bumps_counters() -> None:
    pool = _Pool()
    intel = SupplierIntel(pool)  # type: ignore[arg-type]
    ok = await intel.record_fulfillment("printful", succeeded=True)
    assert ok is True


async def test_trust_score_unverified_with_no_history() -> None:
    pool = _Pool()
    pool.supplier_row = None
    intel = SupplierIntel(pool)  # type: ignore[arg-type]
    s = await intel.trust_score("printful")
    assert s.trust is None  # Rule 1: never guessed
    assert s.is_verified is False


async def test_trust_score_measured_rates() -> None:
    pool = _Pool()
    pool.supplier_row = _supplier_row(
        n_verifications=4, n_verified=3,
        n_fulfillments=10, n_fulfilled_ok=8, n_delays=2, n_cancellations=1,
        total_response_ms=400, n_responses=4,
    )
    intel = SupplierIntel(pool)  # type: ignore[arg-type]
    s = await intel.trust_score("printful")
    assert s.trust == pytest.approx(0.8)
    assert s.verification_rate == pytest.approx(0.75)
    assert s.delay_rate == pytest.approx(0.2)
    assert s.cancellation_rate == pytest.approx(0.1)
    assert s.avg_response_ms == pytest.approx(100.0)
    assert s.is_verified is True


async def test_top_suppliers_maps_rows() -> None:
    pool = _Pool()
    pool.supplier_rows = [_supplier_row(n_fulfillments=5, n_fulfilled_ok=5)]
    intel = SupplierIntel(pool)  # type: ignore[arg-type]
    rows = await intel.top_suppliers()
    assert len(rows) == 1
    assert rows[0].trust == pytest.approx(1.0)


async def test_supplier_none_pool_is_safe() -> None:
    intel = SupplierIntel(None)  # type: ignore[arg-type]
    s = await intel.trust_score("x")
    assert s.trust is None
    assert await intel.top_suppliers() == []
