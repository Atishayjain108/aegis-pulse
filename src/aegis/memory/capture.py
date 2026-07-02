"""
aegis.memory.capture — Stage 2 live capture hooks.

A single, defensive entry point the prediction/settlement paths call to
accumulate knowledge as it happens. Every method:

* respects ``settings.memory_enabled`` (no-op when disabled),
* is fully wrapped in ``try/except`` (never raises into the caller),

so wiring memory into a hot path can never break prediction or settlement.

Two hooks (Rule 2 / Rule 5):
  * :meth:`on_claim`       — a live, falsifiable claim was emitted → pending opp.
  * :meth:`on_settlement`  — a claim settled to ground truth → terminal opp (+failure).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from aegis.config import settings
from aegis.memory.backfill import map_settled_row
from aegis.memory.failure import FailureMemory
from aegis.memory.opportunity import OpportunityMemory
from aegis.memory.schemas import Opportunity
from aegis.memory.taxonomy import OpportunityOutcome, OpportunityType

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.capture")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class MemoryCapture:
    """Defensive façade for live opportunity + failure capture."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id
        self._opps = OpportunityMemory(db_pool, tenant_id=tenant_id)
        self._fails = FailureMemory(db_pool, tenant_id=tenant_id)

    @staticmethod
    def _enabled() -> bool:
        try:
            return bool(settings().memory_enabled)
        except Exception:
            return False

    async def on_claim(
        self,
        *,
        prediction_id: str,
        trend_key: str,
        claimed_direction: str,
        prediction_score: float | None,
        prediction_confidence: float | None,
        baseline_value: float,
        horizon_hours: int,
    ) -> bool:
        """Record a PENDING opportunity for a freshly-emitted live claim."""
        if not self._enabled():
            return False
        try:
            from aegis.memory.verify import score_evidence

            opp = Opportunity(
                opportunity_type=OpportunityType.INFO_ARB,
                category="general",
                trend_key=trend_key,
                prediction_id=prediction_id,
                evidence_score=score_evidence(
                    signal_count=int(baseline_value), author_count=0, platform_count=0
                ),
                evidence={
                    "baseline_value": float(baseline_value),
                    "horizon_hours": int(horizon_hours),
                    "source": "live_claim",
                },
                prediction_direction=claimed_direction,
                prediction_score=prediction_score,
                prediction_confidence=prediction_confidence,
                outcome=OpportunityOutcome.PENDING,
                decay_horizon_hours=int(horizon_hours),
            )
            return await self._opps.record(opp)
        except Exception as exc:
            _log.debug("memory.on_claim_failed", trend_key=trend_key, error=str(exc))
            return False

    async def on_settlement(self, row: dict[str, Any]) -> bool:
        """Capture a SETTLED signal_outcome as terminal knowledge.

        ``row`` must carry the settled fields (resolution_status in
        correct/incorrect plus prediction_id/trend_key/claimed_direction/
        observed_direction/baseline_value/prediction_*/horizon_hours/
        settlement_timestamp). Falsifiable by construction.
        """
        if not self._enabled():
            return False
        if row.get("resolution_status") not in ("correct", "incorrect"):
            return False
        try:
            row = dict(row)
            row.setdefault("settled_at", datetime.now(UTC))
            row["_source"] = "live_settlement"
            opp, failure = map_settled_row(row)
            if failure is not None:
                await self._fails.record(failure)
            ok = await self._opps.settle(opp)
            # Rule 7: materialise the trend→opportunity relationship automatically.
            try:
                from aegis.memory.graph import KnowledgeGraph

                await KnowledgeGraph(self._pool, tenant_id=self._tenant_id).relate_opportunity(opp)
            except Exception as exc:
                _log.debug("memory.on_settlement_graph_failed", error=str(exc))
            return ok
        except Exception as exc:
            _log.debug(
                "memory.on_settlement_failed",
                trend_key=row.get("trend_key"),
                error=str(exc),
            )
            return False
