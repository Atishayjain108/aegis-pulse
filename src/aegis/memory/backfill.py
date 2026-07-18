"""
aegis.memory.backfill — reconstruct opportunity + failure memory from history.

INVARIANT (Rule 12 / falsifiability): this reads ONLY settled rows
(``resolution_status IN ('correct','incorrect')``) from ``signal_outcomes``.
A pending claim never produces an opportunity here, and a 'correct' outcome
never produces a failure. Every knowledge row traces to ground truth.

Idempotent: re-running maps the same outcomes to the same opportunities
(ON CONFLICT DO NOTHING on the natural key). Safe to replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from aegis.memory.failure import FailureMemory, classify_failure
from aegis.memory.opportunity import OpportunityMemory
from aegis.memory.schemas import Failure, Opportunity
from aegis.memory.taxonomy import OpportunityOutcome, OpportunityType

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.backfill")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def map_settled_row(r: dict[str, Any]) -> tuple[Opportunity, Failure | None]:
    """Map one SETTLED ``signal_outcomes`` row → a terminal Opportunity and,
    when the outcome failed, a linked Failure (Rule 5).

    Shared by the offline backfill and the live settlement hook so both produce
    identical, falsifiable knowledge. Caller must only pass settled rows
    (``resolution_status`` in ``{'correct','incorrect'}``).
    """
    is_correct = r["resolution_status"] == "correct"
    baseline = float(r["baseline_value"] or 0.0)
    observed = r.get("observed_value")
    rel_change = (
        (float(observed) - baseline) / baseline
        if observed is not None and baseline
        else None
    )
    realization = {
        "observed_direction": r.get("observed_direction"),
        "observed_value": float(observed) if observed is not None else None,
        "baseline_value": baseline,
        "rel_change": rel_change,
    }
    confidence = (
        float(r["prediction_confidence"])
        if r.get("prediction_confidence") is not None
        else None
    )

    from aegis.memory.verify import score_evidence

    # Evidence proxy from the underlying signal volume (monotone, falsifiable).
    evidence_score = score_evidence(
        signal_count=int(baseline), author_count=0, platform_count=0
    )

    opp = Opportunity(
        opportunity_type=OpportunityType.INFO_ARB,
        category="general",
        trend_key=r["trend_key"],
        prediction_id=r["prediction_id"],
        evidence_score=evidence_score,
        evidence={
            "baseline_value": baseline,
            "horizon_hours": int(r["horizon_hours"]),
            "source": r.get("_source", "signal_outcomes"),
        },
        prediction_direction=r["claimed_direction"],
        prediction_score=(
            float(r["prediction_score"])
            if r.get("prediction_score") is not None
            else None
        ),
        prediction_confidence=confidence,
        outcome=(
            OpportunityOutcome.REALIZED if is_correct else OpportunityOutcome.FAILED
        ),
        realization_path=realization,
        decay_horizon_hours=int(r["horizon_hours"]),
        settled_at=r.get("settled_at"),
        settlement_timestamp=r["settlement_timestamp"],
    )

    if is_correct:
        return opp, None

    failure = classify_failure(
        opportunity_id=opp.opportunity_id,
        trend_key=r["trend_key"],
        prediction_id=r["prediction_id"],
        claimed_direction=r["claimed_direction"],
        observed_direction=r.get("observed_direction"),
        prediction_confidence=confidence,
        baseline_value=baseline,
    )
    opp = opp.model_copy(update={"failure_id": failure.failure_id})
    return opp, failure


@dataclass(frozen=True)
class BackfillSummary:
    examined: int = 0
    opportunities: int = 0
    failures: int = 0
    skipped: int = 0


class MemoryBackfill:
    """Populate opportunity + failure memory from settled signal_outcomes."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id
        self._opps = OpportunityMemory(db_pool, tenant_id=tenant_id)
        self._fails = FailureMemory(db_pool, tenant_id=tenant_id)

    async def run(self, *, max_rows: int = 5000) -> BackfillSummary:
        examined = opportunities = failures = skipped = 0
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT prediction_id, trend_key, claimed_direction,
                           observed_direction, prediction_score,
                           prediction_confidence, baseline_value, observed_value,
                           horizon_hours, settled_at, settlement_timestamp,
                           resolution_status, metadata
                    FROM signal_outcomes
                    WHERE resolution_status IN ('correct', 'incorrect')
                      AND window_scraper_alive = TRUE
                    ORDER BY settlement_timestamp DESC
                    LIMIT $1
                    """,
                    max_rows,
                )
        except Exception as exc:
            _log.error("memory.backfill_fetch_failed", error=str(exc))
            return BackfillSummary()

        for r in rows:
            examined += 1
            row = dict(r)
            row["_source"] = "signal_outcomes_backfill"
            opp, failure = map_settled_row(row)
            if failure is not None and await self._fails.record(failure):
                failures += 1
            if await self._opps.record(opp):
                opportunities += 1
            else:
                skipped += 1

        summary = BackfillSummary(examined, opportunities, failures, skipped)
        _log.info(
            "memory.backfill_complete",
            examined=examined,
            opportunities=opportunities,
            failures=failures,
            skipped=skipped,
        )
        return summary
