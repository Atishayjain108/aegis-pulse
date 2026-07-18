"""
aegis.memory.opportunity — the permanent opportunity ledger (Rule 2).

Every opportunity is anchored to a re-observable ``(prediction_id, trend_key)``
and only transitions to a terminal outcome from settled ground truth. Writes are
best-effort (log, never raise) so the prediction path is never blocked.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from aegis.memory.schemas import Opportunity

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.opportunity")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class OpportunityMemory:
    """Best-effort persistence + query of the opportunity ledger."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def record(self, opp: Opportunity) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                await conn.execute(
                    """
                    INSERT INTO opportunities (
                        opportunity_id, opportunity_type, category, region,
                        trend_key, prediction_id, source_signal_ids, evidence,
                        trust_score, reality_score, evidence_score, unknowns_score,
                        prediction_direction, prediction_score, prediction_confidence,
                        outcome, failure_id, realization_path, decay_horizon_hours,
                        settled_at, settlement_timestamp, metadata
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                        $16,$17,$18,$19,$20,$21,$22
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    opp.opportunity_id,
                    opp.opportunity_type.value,
                    opp.category,
                    opp.region,
                    opp.trend_key,
                    opp.prediction_id,
                    json.dumps(opp.source_signal_ids),
                    json.dumps(opp.evidence, default=str),
                    opp.trust_score,
                    opp.reality_score,
                    opp.evidence_score,
                    opp.unknowns_score,
                    opp.prediction_direction,
                    opp.prediction_score,
                    opp.prediction_confidence,
                    opp.outcome.value,
                    opp.failure_id,
                    json.dumps(opp.realization_path, default=str),
                    opp.decay_horizon_hours,
                    opp.settled_at,
                    opp.settlement_timestamp,
                    json.dumps(opp.metadata, default=str),
                )
            return True
        except Exception as exc:
            _log.error("memory.opportunity_record_failed", error=str(exc))
            return False

    async def settle(self, opp: Opportunity) -> bool:
        """Transition an opportunity to its terminal state from ground truth.

        UPSERT semantics: update an existing *pending* row (created at claim time)
        to the terminal outcome; if none exists (e.g. settled directly, never
        emitted live), insert the terminal row. This avoids the ON CONFLICT
        DO NOTHING trap where a pending row would otherwise stay stuck pending.
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                status = await conn.execute(
                    """
                    UPDATE opportunities
                    SET outcome = $3,
                        failure_id = $4,
                        realization_path = $5,
                        settled_at = $6,
                        prediction_direction = $7,
                        prediction_score = $8,
                        prediction_confidence = $9
                    WHERE prediction_id = $1 AND trend_key = $2 AND outcome = 'pending'
                    """,
                    opp.prediction_id,
                    opp.trend_key,
                    opp.outcome.value,
                    opp.failure_id,
                    json.dumps(opp.realization_path, default=str),
                    opp.settled_at,
                    opp.prediction_direction,
                    opp.prediction_score,
                    opp.prediction_confidence,
                )
            # asyncpg returns e.g. "UPDATE 1"; 0 rows means nothing pending → insert.
            if status.rsplit(" ", 1)[-1] == "0":
                return await self.record(opp)
            return True
        except Exception as exc:
            _log.error("memory.opportunity_settle_failed", error=str(exc))
            return False

    async def count(self) -> int:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                row = await conn.fetchrow("SELECT COUNT(*) AS n FROM opportunities")
            return int(row["n"]) if row else 0
        except Exception as exc:
            _log.error("memory.opportunity_count_failed", error=str(exc))
            return 0

    async def patterns(self, *, days_back: int = 90, limit: int = 20) -> list[dict[str, Any]]:
        """Recurring opportunity patterns with realized/failed counts (Rule 2).

        Answers: "what opportunities were most successful / failed, and how
        often do they repeat?" — grouped by (type, category).
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT opportunity_type, category,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE outcome = 'realized') AS realized,
                           COUNT(*) FILTER (WHERE outcome = 'failed')   AS failed,
                           AVG(prediction_confidence) AS avg_confidence
                    FROM opportunities
                    WHERE created_at >= NOW() - ($1 || ' days')::interval
                    GROUP BY opportunity_type, category
                    HAVING COUNT(*) FILTER (WHERE outcome IN ('realized','failed')) > 0
                    ORDER BY COUNT(*) FILTER (WHERE outcome = 'realized') DESC, total DESC
                    LIMIT $2
                    """,
                    str(days_back),
                    limit,
                )
            out: list[dict[str, Any]] = []
            for r in rows:
                total_settled = int(r["realized"]) + int(r["failed"])
                realized_rate = (
                    int(r["realized"]) / total_settled if total_settled else None
                )
                out.append(
                    {
                        "opportunity_type": r["opportunity_type"],
                        "category": r["category"],
                        "total": int(r["total"]),
                        "realized": int(r["realized"]),
                        "failed": int(r["failed"]),
                        "realized_rate": realized_rate,
                        "avg_confidence": (
                            float(r["avg_confidence"])
                            if r["avg_confidence"] is not None
                            else None
                        ),
                    }
                )
            return out
        except Exception as exc:
            _log.error("memory.opportunity_patterns_failed", error=str(exc))
            return []
