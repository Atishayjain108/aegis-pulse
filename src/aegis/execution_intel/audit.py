"""
aegis.execution_intel.audit — Self-Audit (Rule 10) + arbitrage rationale (Rule 7).

``ExecutionAuditor.weekly_report`` reads real settled execution records,
measured supplier reliability, and the demand proxy to answer Rule 10:
best/worst plans, most reliable suppliers, most-demanded markets, most
profitable opportunities, and most common failure causes. Every list is empty
when there is no data — nothing is fabricated.

``arbitrage_rationale`` produces the three Rule 7 questions as ADVISORY
hypotheses (``verified=False``), grounded in the opportunity type. They are
framed as questions to investigate, never asserted as fact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from aegis.execution_intel.schemas import ArbitrageRationale, ExecutionAudit

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.execution_intel.audit")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# Per-type arbitrage hypotheses (Rule 7) — advisory prompts, not verified facts.
_ARB_HYPOTHESES = {
    "product": (
        "a price/availability gap exists between a source and a destination market",
        "logistics, tariffs, or supplier access have deterred others so far",
        "competitors entering, supplier price rises, tariffs, or demand cooling",
    ),
    "geo_arb": (
        "FX/tariff/shipping differentials make the same good cheaper to land elsewhere",
        "cross-border friction (customs, FX risk, shipping time) raises the barrier",
        "FX moves, tariff changes, or shipping-cost spikes erasing the margin",
    ),
    "info_arb": (
        "a signal trend is not yet priced into the destination market",
        "the signal is noisy or hard to act on at small scale",
        "the information becoming widely known, collapsing the edge",
    ),
    "regulatory_arb": (
        "differing regulations permit something in one jurisdiction not another",
        "compliance complexity and legal risk deter most actors",
        "regulatory harmonisation or enforcement closing the gap",
    ),
}
_ARB_DEFAULT = (
    "an unexploited gap between supply cost and achievable demand price",
    "execution friction or capital/access barriers have kept others out",
    "competition, cost increases, or demand decay removing the gap",
)


class ExecutionAuditor:
    """Generate the weekly execution self-audit (Rule 10)."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def _set_tenant(self, conn: Any) -> None:
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
        )

    async def weekly_report(self, *, period_days: int = 7) -> ExecutionAudit:
        """Aggregate settled execution memory over the trailing window."""
        if self._pool is None:
            return ExecutionAudit(period_days=period_days)
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                n_settled = await conn.fetchval(
                    """
                    SELECT COUNT(*)::int FROM execution_records
                    WHERE outcome != 'pending'
                      AND created_at > NOW() - ($1::int * INTERVAL '1 day')
                    """,
                    period_days,
                ) or 0
                best = await self._plans(conn, period_days, desc=True)
                worst = await self._plans(conn, period_days, desc=False)
                suppliers = await self._suppliers(conn)
                markets = await self._markets(conn)
                failures = await self._failures(conn, period_days)
        except Exception as exc:
            _log.error("execution_intel.audit_failed", error=str(exc))
            return ExecutionAudit(period_days=period_days)

        return ExecutionAudit(
            period_days=period_days,
            n_settled=int(n_settled),
            best_plans=best,
            worst_plans=worst,
            most_reliable_suppliers=suppliers,
            most_demanded_markets=markets,
            most_profitable=best,  # best by pnl == most profitable
            common_failure_causes=failures,
        )

    async def _plans(
        self, conn: Any, period_days: int, *, desc: bool,
    ) -> list[dict[str, Any]]:
        order = "DESC" if desc else "ASC"
        rows = await conn.fetch(
            f"""
            SELECT plan_id, supplier_name, outcome, realized_pnl_usd, failure_category
            FROM execution_records
            WHERE outcome != 'pending'
              AND realized_pnl_usd IS NOT NULL
              AND created_at > NOW() - ($1::int * INTERVAL '1 day')
            ORDER BY realized_pnl_usd {order}
            LIMIT 5
            """,  # noqa: S608 — `order` is a fixed ASC/DESC literal, not user input
            period_days,
        )
        return [
            {
                "plan_id": r["plan_id"],
                "supplier": r["supplier_name"],
                "outcome": r["outcome"],
                "pnl_usd": r["realized_pnl_usd"],
                "failure": r["failure_category"],
            }
            for r in rows
        ]

    async def _suppliers(self, conn: Any) -> list[dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT supplier_name, n_fulfillments, n_fulfilled_ok
            FROM supplier_reliability
            WHERE n_fulfillments > 0
            ORDER BY n_fulfilled_ok::float / n_fulfillments DESC, n_fulfillments DESC
            LIMIT 5
            """
        )
        return [
            {
                "supplier": r["supplier_name"],
                "trust": round(r["n_fulfilled_ok"] / r["n_fulfillments"], 4),
                "n_fulfillments": r["n_fulfillments"],
            }
            for r in rows
        ]

    async def _markets(self, conn: Any) -> list[dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT region, category, demand_intensity
            FROM buyer_demand
            WHERE demand_intensity IS NOT NULL
            ORDER BY demand_intensity DESC
            LIMIT 5
            """
        )
        return [
            {
                "region": r["region"],
                "category": r["category"],
                "demand_intensity": r["demand_intensity"],
                "demand_is_proxy": True,
            }
            for r in rows
        ]

    async def _failures(self, conn: Any, period_days: int) -> list[dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT failure_category, COUNT(*)::int AS n
            FROM execution_records
            WHERE outcome = 'failed' AND failure_category != 'none'
              AND created_at > NOW() - ($1::int * INTERVAL '1 day')
            GROUP BY failure_category
            ORDER BY n DESC
            LIMIT 10
            """,
            period_days,
        )
        return [{"cause": r["failure_category"], "count": r["n"]} for r in rows]


def arbitrage_rationale(opportunity_type: str = "product") -> ArbitrageRationale:
    """The three Rule 7 questions as advisory hypotheses (never asserted fact)."""
    why_exists, why_not, what_destroys = _ARB_HYPOTHESES.get(
        opportunity_type, _ARB_DEFAULT
    )
    return ArbitrageRationale(
        opportunity_type=opportunity_type,
        why_exists=f"Hypothesis (UNVERIFIED): {why_exists}.",
        why_not_captured=f"Hypothesis (UNVERIFIED): {why_not}.",
        what_destroys_it=f"Hypothesis (UNVERIFIED): {what_destroys}.",
        verified=False,
    )
