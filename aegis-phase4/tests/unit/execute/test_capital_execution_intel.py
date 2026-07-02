"""Phase D wiring: capital create_plan records ExecutionRecord + FailureForecast.

Verifies the best-effort recorder issues plan + assumption + forecast inserts
against the audit pool, and never raises when the pool is absent.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aegis.execute.api.routes.capital import _record_execution_intel
from aegis.execute.engine import ExecutionPlan, FulfillmentMethod, PlanStatus


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        self._pool.executed.append(" ".join(query.split()))
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return None  # no supplier history → forecast abstains, still recorded


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

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))


def _plan(**kw: Any) -> ExecutionPlan:
    defaults = {
        "intent_id": "intent-1",
        "trend_id": "trend-1",
        "quantity": 10,
        "fulfillment_method": FulfillmentMethod.POD,
        "unit_cost_usd": 5.0,
        "unit_price_usd": 10.0,
        "total_capital_usd": 50.0,
        "estimated_profit_usd": 50.0,
        "kelly_fraction_raw": 0.1,
        "kelly_fraction_used": 0.025,
        "risk_score": 0.3,
        "requires_approval": True,
        "status": PlanStatus.PENDING,
        "execution_mode": "live",
        "supplier_name": "printful",
    }
    defaults.update(kw)
    return ExecutionPlan(**defaults)


def _request(pool: Any) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(audit_pool=pool)))


@pytest.mark.asyncio
async def test_recorder_writes_plan_assumption_and_forecast():
    pool = _Pool()
    intent = SimpleNamespace(category="apparel")
    await _record_execution_intel(
        _request(pool), _plan(), intent, tenant="00000000-0000-0000-0000-000000000001"
    )
    joined = " || ".join(pool.executed)
    assert "INSERT INTO execution_records" in joined
    assert "INSERT INTO execution_assumptions" in joined
    assert "INSERT INTO execution_forecasts" in joined


@pytest.mark.asyncio
async def test_recorder_no_pool_is_safe():
    intent = SimpleNamespace(category="apparel")
    # Must not raise when there is no audit pool.
    await _record_execution_intel(
        _request(None), _plan(), intent, tenant="00000000-0000-0000-0000-000000000001"
    )


@pytest.mark.asyncio
async def test_recorder_unverified_supplier_logs_unverified_assumption():
    pool = _Pool()
    intent = SimpleNamespace(category="apparel")
    await _record_execution_intel(
        _request(pool), _plan(supplier_name=None), intent,
        tenant="00000000-0000-0000-0000-000000000001",
    )
    # An assumption row is still written; status is decided inside log_assumption.
    assert any("INSERT INTO execution_assumptions" in q for q in pool.executed)
