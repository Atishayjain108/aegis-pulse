"""PROJECT OMEGA Phase D — S3 Buyer Intelligence + S4 Execution Simulation.

Query-aware fake pool, no live Postgres. Covers the demand proxy (always a
proxy, buyer trust UNVERIFIED until real orders), and the survivability
simulator (deterministic; abstains when the critical supplier input is
unverified; uses measured failure base rates when present).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from aegis.execution_intel import (
    BuyerDemandProxy,
    BuyerIntel,
    ExecutionSimulator,
    SurvivabilityScore,
)

_NOW = datetime(2026, 6, 17, tzinfo=UTC)


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        self._pool.executed.append(" ".join(query.split()))
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split())
        if "GROUP BY failure_category" in q:
            return self._pool.failure_rows
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = " ".join(query.split())
        if "FROM buyer_demand" in q:
            return self._pool.buyer_row
        if "FROM supplier_reliability" in q:
            return self._pool.supplier_row
        if "COUNT(*)::int AS n FROM execution_records" in q:
            return self._pool.settled_row
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
        self.buyer_row: dict[str, Any] | None = None
        self.supplier_row: dict[str, Any] | None = None
        self.settled_row: dict[str, Any] | None = None
        self.failure_rows: list[dict[str, Any]] = []

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))


def _buyer_row(**kw: Any) -> dict[str, Any]:
    base = {
        "region": "US",
        "category": "apparel",
        "demand_intensity": 0.6,
        "n_demand_observations": 3,
        "n_orders": 0,
        "n_fulfilled": 0,
        "n_cancelled": 0,
        "updated_at": _NOW,
    }
    base.update(kw)
    return base


def _supplier_row(**kw: Any) -> dict[str, Any]:
    base = {
        "supplier_name": "printful",
        "n_verifications": 5, "n_verified": 5,
        "n_fulfillments": 10, "n_fulfilled_ok": 9,
        "n_delays": 1, "n_cancellations": 0,
        "total_response_ms": 500, "n_responses": 5,
        "updated_at": _NOW,
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# S3 — Buyer Intelligence (demand proxy / UNVERIFIED)
# --------------------------------------------------------------------------- #


async def test_demand_proxy_default_unverified() -> None:
    pool = _Pool()
    pool.buyer_row = None
    intel = BuyerIntel(pool)  # type: ignore[arg-type]
    p = await intel.demand_proxy("US", "apparel")
    assert p.demand_intensity is None
    assert p.buyer_trust is None        # Rule 1
    assert p.buyer_is_verified is False
    assert p.demand_is_proxy is True


async def test_demand_proxy_maps_intensity_trust_stays_none() -> None:
    pool = _Pool()
    pool.buyer_row = _buyer_row()
    intel = BuyerIntel(pool)  # type: ignore[arg-type]
    p = await intel.demand_proxy("US", "apparel")
    assert p.demand_intensity == pytest.approx(0.6)
    assert p.buyer_trust is None        # no real orders → UNVERIFIED
    assert p.demand_is_proxy is True


async def test_buyer_trust_appears_only_with_real_orders() -> None:
    pool = _Pool()
    pool.buyer_row = _buyer_row(n_orders=4, n_fulfilled=3)
    intel = BuyerIntel(pool)  # type: ignore[arg-type]
    p = await intel.demand_proxy("US", "apparel")
    assert p.buyer_trust == pytest.approx(0.75)
    assert p.buyer_is_verified is True


async def test_record_demand_observation_clamps_and_runs() -> None:
    pool = _Pool()
    intel = BuyerIntel(pool)  # type: ignore[arg-type]
    assert await intel.record_demand_observation("us", "Apparel", 1.5) is True
    assert any("INSERT INTO buyer_demand" in q for q in pool.executed)


async def test_record_order_runs() -> None:
    pool = _Pool()
    intel = BuyerIntel(pool)  # type: ignore[arg-type]
    assert await intel.record_order("US", "apparel", fulfilled=True) is True


async def test_buyer_none_pool_safe() -> None:
    intel = BuyerIntel(None)  # type: ignore[arg-type]
    p = await intel.demand_proxy("US", "apparel")
    assert isinstance(p, BuyerDemandProxy)
    assert p.buyer_trust is None


# --------------------------------------------------------------------------- #
# S4 — Execution Simulation / Survivability
# --------------------------------------------------------------------------- #


async def test_simulate_abstains_without_supplier() -> None:
    pool = _Pool()
    pool.settled_row = {"n": 0}
    sim = ExecutionSimulator(pool)  # type: ignore[arg-type]
    s = await sim.simulate("plan-1")  # no supplier_name
    assert isinstance(s, SurvivabilityScore)
    assert s.overall is None
    assert s.abstained is True
    assert "UNVERIFIED" in s.reason
    assert s.basis["supplier"] == "unverified"


async def test_simulate_abstains_when_supplier_unmeasured() -> None:
    pool = _Pool()
    pool.settled_row = {"n": 0}
    pool.supplier_row = _supplier_row(n_fulfillments=0, n_fulfilled_ok=0)
    sim = ExecutionSimulator(pool)  # type: ignore[arg-type]
    s = await sim.simulate("plan-1", supplier_name="printful")
    assert s.abstained is True
    assert s.overall is None


async def test_simulate_scores_with_measured_supplier() -> None:
    pool = _Pool()
    pool.settled_row = {"n": 0}        # no learned base rates → assumed modes
    pool.supplier_row = _supplier_row()  # trust = 9/10 = 0.9
    sim = ExecutionSimulator(pool)  # type: ignore[arg-type]
    s = await sim.simulate(
        "plan-1", supplier_name="printful", compliance_risk=0.0,
    )
    assert s.abstained is False
    assert s.overall is not None
    assert 0.0 < s.overall <= 1.0
    assert s.basis["supplier"] == "measured"
    assert s.basis["compliance"] == "measured"
    assert s.is_verified is True
    # inventory/shipping/payment fall back to assumed survival
    assert s.basis["inventory"] == "assumed"


async def test_simulate_uses_measured_failure_base_rates() -> None:
    pool = _Pool()
    pool.settled_row = {"n": 10}
    pool.failure_rows = [
        {"failure_category": "shipping_delay", "n": 2},
    ]
    pool.supplier_row = _supplier_row()
    sim = ExecutionSimulator(pool)  # type: ignore[arg-type]
    s = await sim.simulate("plan-1", supplier_name="printful")
    # shipping survival = 1 - (2/10) = 0.8, measured
    assert s.mode_survival["shipping"] == pytest.approx(0.8)
    assert s.basis["shipping"] == "measured"
    # inventory has no failures → still assumed
    assert s.basis["inventory"] == "assumed"


async def test_simulate_demand_proxy_flagged() -> None:
    pool = _Pool()
    pool.settled_row = {"n": 0}
    pool.supplier_row = _supplier_row()
    pool.buyer_row = _buyer_row(demand_intensity=0.8)
    sim = ExecutionSimulator(pool)  # type: ignore[arg-type]
    s = await sim.simulate(
        "plan-1", supplier_name="printful", region="US", category="apparel",
    )
    assert s.basis["demand"] == "proxy"
    assert s.mode_survival["demand"] == pytest.approx(0.9)  # 0.5 + 0.5*0.8
