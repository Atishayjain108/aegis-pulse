"""
aegis.evolve.outcomes
=====================

Record execution outcomes as ground truth labels for model retraining.

Every trade → execution → fulfillment → settlement produces a TradeOutcome
row that the weekly retraining pipeline uses as a supervised training label.

Public API:
    OutcomeRecorder.record_outcome(outcome)  → bool
    OutcomeRecorder.fetch_recent_outcomes(days_back, limit) → list[TradeOutcome]
    OutcomeRecorder.count_recent_outcomes(days_back) → int
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

from aegis.evolve.constants import ERR_OUTCOME_RECORD_FAILED
from aegis.evolve.schemas import TradeOutcome

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.evolve.outcomes")


_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class OutcomeRecorder:
    """Persist and retrieve trade outcomes from the database."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def record_outcome(self, outcome: TradeOutcome) -> bool:
        """
        Persist a trade outcome as a ground truth label.

        Returns True on success, False on failure (non-fatal — caller may
        still continue without the outcome being recorded).
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                await conn.execute(
                    """
                    INSERT INTO prediction_outcomes (
                        outcome_id, execution_plan_id, trend_id,
                        prediction_score, prediction_confidence,
                        actual_roi_pct, pnl_usd,
                        units_sold, units_returned,
                        settlement_timestamp, resolution_status, metadata
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT DO NOTHING
                    """,
                    outcome.outcome_id,
                    outcome.execution_plan_id,
                    outcome.trend_id,
                    outcome.prediction_score,
                    outcome.prediction_confidence,
                    float(outcome.actual_roi_pct),
                    float(outcome.pnl_usd),
                    outcome.units_sold,
                    outcome.units_returned,
                    outcome.settlement_timestamp,
                    outcome.resolution_status,
                    json.dumps(outcome.model_dump(mode="json"), default=str),
                )

            _log.info(
                "evolve.outcome_recorded",
                outcome_id=outcome.outcome_id,
                pnl_usd=float(outcome.pnl_usd),
                roi_pct=float(outcome.actual_roi_pct),
                resolution=outcome.resolution_status,
            )
            return True

        except Exception as exc:
            _log.error(
                "evolve.outcome_record_failed",
                error=str(exc),
                error_code=ERR_OUTCOME_RECORD_FAILED,
                outcome_id=outcome.outcome_id,
            )
            return False

    async def fetch_recent_outcomes(
        self,
        days_back: int = 30,
        limit: int = 10_000,
    ) -> list[TradeOutcome]:
        """
        Fetch outcome rows from the last *days_back* days.

        Returns an empty list on DB failure so callers can degrade gracefully.
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                rows = await conn.fetch(
                    """
                    SELECT metadata
                    FROM prediction_outcomes
                    WHERE settlement_timestamp > NOW() - $1::INTERVAL
                    ORDER BY settlement_timestamp DESC
                    LIMIT $2
                    """,
                    timedelta(days=days_back),
                    limit,
                )

            outcomes: list[TradeOutcome] = []
            for row in rows:
                try:
                    data = json.loads(row["metadata"])
                    outcomes.append(TradeOutcome(**data))
                except Exception as parse_exc:
                    _log.debug("evolve.outcome_parse_skip", error=str(parse_exc))

            _log.info(
                "evolve.outcomes_fetched",
                count=len(outcomes),
                days_back=days_back,
            )
            return outcomes

        except Exception as exc:
            _log.error("evolve.fetch_failed", error=str(exc))
            return []

    async def count_recent_outcomes(self, days_back: int = 30) -> int:
        """Return count of outcomes in the last N days without fetching metadata."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) AS n
                    FROM prediction_outcomes
                    WHERE settlement_timestamp > NOW() - $1::INTERVAL
                    """,
                    timedelta(days=days_back),
                )
            return int(row["n"]) if row else 0
        except Exception as exc:
            _log.error("evolve.count_failed", error=str(exc))
            return 0

    async def fetch_outcomes_for_drift(
        self,
        days_back: int = 7,
        limit: int = 1_000,
    ) -> list[dict[str, float]]:
        """
        Fetch lightweight feature vectors for drift detection.

        Returns a list of {prediction_score, prediction_confidence} dicts.
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                rows = await conn.fetch(
                    """
                    SELECT prediction_score, prediction_confidence
                    FROM prediction_outcomes
                    WHERE settlement_timestamp > NOW() - $1::INTERVAL
                    ORDER BY settlement_timestamp DESC
                    LIMIT $2
                    """,
                    timedelta(days=days_back),
                    limit,
                )
            return [dict(row) for row in rows]
        except Exception as exc:
            _log.error("evolve.drift_fetch_failed", error=str(exc))
            return []
