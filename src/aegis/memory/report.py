"""
aegis.memory.report — Self-Auditing Intelligence (Rule 10).

Generates a periodic self-evaluation: best/worst opportunity patterns, the
source reliability leaderboard, recurring failure categories, and the most/least
reliable entities. Pure aggregation over knowledge already accumulated — the
system grading itself. Best-effort; returns partial results on any sub-error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from aegis.memory.failure import FailureMemory
from aegis.memory.opportunity import OpportunityMemory

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.report")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class SelfAudit:
    """Compose accumulated knowledge into a weekly self-audit report."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id
        self._opps = OpportunityMemory(db_pool, tenant_id=tenant_id)
        self._fails = FailureMemory(db_pool, tenant_id=tenant_id)

    async def _source_leaderboard(self, *, limit: int = 5) -> dict[str, Any]:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT source_id, trust, reliability_score, n_outcomes
                    FROM source_profiles
                    WHERE n_outcomes > 0
                    ORDER BY trust DESC
                    """
                )
        except Exception as exc:
            _log.debug("memory.audit_sources_failed", error=str(exc))
            return {"best": [], "worst": []}
        items = [
            {
                "source_id": r["source_id"],
                "trust": round(float(r["trust"]), 4),
                "reliability": (
                    round(float(r["reliability_score"]), 4)
                    if r["reliability_score"] is not None else None
                ),
                "n_outcomes": int(r["n_outcomes"]),
            }
            for r in rows
        ]
        return {"best": items[:limit], "worst": items[-limit:][::-1]}

    async def weekly_report(self, *, days_back: int = 7) -> dict[str, Any]:
        """The self-audit artifact (Rule 10). Always returns a dict."""
        patterns = await self._opps.patterns(days_back=days_back, limit=200)
        settled = [p for p in patterns if p.get("realized_rate") is not None]
        ranked = sorted(settled, key=lambda p: p["realized_rate"], reverse=True)
        failures = await self._fails.category_counts(days_back=days_back)
        sources = await self._source_leaderboard()

        return {
            "window_days": days_back,
            "opportunity_patterns": len(patterns),
            "best_opportunities": ranked[:5],
            "worst_opportunities": ranked[-5:][::-1] if ranked else [],
            "recurring_failures": failures,
            "best_sources": sources["best"],
            "worst_sources": sources["worst"],
        }
