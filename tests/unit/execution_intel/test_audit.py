"""PROJECT OMEGA Phase D — S6: Self-Audit (Rule 10) + arbitrage rationale (Rule 7).

Query-aware fake pool. Covers the weekly report aggregation (best/worst plans,
reliable suppliers, demanded markets, failure causes) and the advisory
arbitrage questions (always UNVERIFIED hypotheses).
"""

from __future__ import annotations

from typing import Any

from aegis.execution_intel import (
    ArbitrageRationale,
    ExecutionAudit,
    ExecutionAuditor,
    arbitrage_rationale,
)


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        return "OK"

    async def fetchval(self, query: str, *args: Any) -> Any:
        return self._pool.n_settled

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split())
        if "FROM execution_records" in q and "ORDER BY realized_pnl_usd DESC" in q:
            return self._pool.best_rows
        if "FROM execution_records" in q and "ORDER BY realized_pnl_usd ASC" in q:
            return self._pool.worst_rows
        if "FROM supplier_reliability" in q:
            return self._pool.supplier_rows
        if "FROM buyer_demand" in q:
            return self._pool.market_rows
        if "GROUP BY failure_category" in q:
            return self._pool.failure_rows
        return []


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Conn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


class _Pool:
    def __init__(self) -> None:
        self.n_settled: int = 0
        self.best_rows: list[dict[str, Any]] = []
        self.worst_rows: list[dict[str, Any]] = []
        self.supplier_rows: list[dict[str, Any]] = []
        self.market_rows: list[dict[str, Any]] = []
        self.failure_rows: list[dict[str, Any]] = []

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))


# --------------------------------------------------------------------------- #
# Self-Audit (Rule 10)
# --------------------------------------------------------------------------- #


async def test_weekly_report_empty_when_no_data() -> None:
    pool = _Pool()
    audit = await ExecutionAuditor(pool).weekly_report()  # type: ignore[arg-type]
    assert isinstance(audit, ExecutionAudit)
    assert audit.n_settled == 0
    assert audit.best_plans == []
    assert audit.common_failure_causes == []


async def test_weekly_report_aggregates() -> None:
    pool = _Pool()
    pool.n_settled = 12
    pool.best_rows = [
        {"plan_id": "p1", "supplier_name": "printful", "outcome": "succeeded",
         "realized_pnl_usd": 100.0, "failure_category": "none"},
    ]
    pool.worst_rows = [
        {"plan_id": "p2", "supplier_name": "cj", "outcome": "failed",
         "realized_pnl_usd": -20.0, "failure_category": "shipping_delay"},
    ]
    pool.supplier_rows = [
        {"supplier_name": "printful", "n_fulfillments": 10, "n_fulfilled_ok": 9},
    ]
    pool.market_rows = [
        {"region": "US", "category": "apparel", "demand_intensity": 0.7},
    ]
    pool.failure_rows = [
        {"failure_category": "shipping_delay", "n": 3},
    ]
    audit = await ExecutionAuditor(pool).weekly_report(period_days=7)  # type: ignore[arg-type]
    assert audit.n_settled == 12
    assert audit.best_plans[0]["pnl_usd"] == 100.0
    assert audit.most_profitable == audit.best_plans
    assert audit.worst_plans[0]["failure"] == "shipping_delay"
    assert audit.most_reliable_suppliers[0]["trust"] == 0.9
    assert audit.most_demanded_markets[0]["demand_is_proxy"] is True
    assert audit.common_failure_causes[0]["cause"] == "shipping_delay"


async def test_weekly_report_none_pool_safe() -> None:
    audit = await ExecutionAuditor(None).weekly_report()  # type: ignore[arg-type]
    assert audit.n_settled == 0


# --------------------------------------------------------------------------- #
# Arbitrage rationale (Rule 7)
# --------------------------------------------------------------------------- #


def test_arbitrage_rationale_advisory_and_unverified() -> None:
    r = arbitrage_rationale("geo_arb")
    assert isinstance(r, ArbitrageRationale)
    assert r.verified is False
    assert "UNVERIFIED" in r.why_exists
    assert "UNVERIFIED" in r.why_not_captured
    assert "UNVERIFIED" in r.what_destroys_it


def test_arbitrage_rationale_defaults_for_unknown_type() -> None:
    r = arbitrage_rationale("something_new")
    assert r.opportunity_type == "something_new"
    assert r.why_exists  # falls back to default hypotheses
    assert r.verified is False
