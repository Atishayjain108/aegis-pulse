"""
aegis.memory.failure — Failure intelligence (Rule 5).

A failure record is created ONLY for a settled-incorrect outcome. The classifier
is a pure, deterministic function over data already known at settlement time — no
new model, no look-ahead. This makes every failure row falsifiable and reusable.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from aegis.memory.schemas import Failure
from aegis.memory.taxonomy import FailureCategory

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.failure")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# Below this many underlying signals the evidence is considered thin.
_THIN_EVIDENCE_SIGNALS = 5.0
# Confidence at/above which a wrong call is flagged as overconfident.
_OVERCONFIDENT = 0.70


def classify_failure(
    *,
    opportunity_id: str,
    trend_key: str,
    prediction_id: str,
    claimed_direction: str | None,
    observed_direction: str | None,
    prediction_confidence: float | None,
    baseline_value: float | None,
    metadata: dict[str, Any] | None = None,
) -> Failure:
    """Classify a *settled-incorrect* outcome into reusable failure knowledge.

    Pure: same inputs always yield the same Failure. Callers must only invoke
    this for outcomes that actually failed (observed != claimed).
    """
    baseline = float(baseline_value or 0.0)
    conf = prediction_confidence

    # Primary category from the direction mismatch.
    if observed_direction == "flat":
        category = FailureCategory.DECAYED_OR_STALLED
        cause = f"claimed '{claimed_direction}' but the trend stalled (flat)"
    elif claimed_direction == "rise" and observed_direction == "fall":
        category = FailureCategory.FALSE_BREAKOUT
        cause = "predicted a breakout that reversed into decline"
    elif claimed_direction == "fall" and observed_direction == "rise":
        category = FailureCategory.MISSED_BREAKOUT
        cause = "predicted decline but the trend broke out upward"
    else:
        category = FailureCategory.UNCLASSIFIED
        cause = f"claimed '{claimed_direction}', observed '{observed_direction}'"

    # Thin evidence dominates: a wrong call on near-no data is a data problem.
    missing: list[str] = []
    if baseline < _THIN_EVIDENCE_SIGNALS:
        category = FailureCategory.EVIDENCE_THIN
        cause = f"only {baseline:.0f} underlying signals — insufficient evidence"
        missing.append("sufficient_signal_volume")
    elif conf is not None and conf >= _OVERCONFIDENT:
        # Right category, but flag the confidence error explicitly.
        category = FailureCategory.OVERCONFIDENT
        cause = f"{cause}; held high confidence ({conf:.2f}) on a wrong call"

    # confidence_error: how far the asserted confidence was from the truth (0 for
    # an incorrect outcome). Larger = more dangerous miscalibration.
    confidence_error = None if conf is None else round(conf - 0.0, 4)

    # Evidence quality: monotone-increasing in signal volume, saturating ~50.
    evidence_quality = round(min(1.0, baseline / 50.0), 4)

    return Failure(
        opportunity_id=opportunity_id,
        trend_key=trend_key,
        prediction_id=prediction_id,
        failure_category=category,
        root_cause=cause,
        evidence_quality=evidence_quality,
        missing_information=missing,
        confidence_error=confidence_error,
        metadata=metadata or {},
    )


class FailureMemory:
    """Best-effort persistence of failure knowledge — write failures log, never raise."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def record(self, failure: Failure) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                await conn.execute(
                    """
                    INSERT INTO failures (
                        failure_id, opportunity_id, trend_key, prediction_id,
                        failure_category, root_cause, evidence_quality,
                        missing_information, confidence_error, detected_at, metadata
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, NOW(), $10)
                    ON CONFLICT DO NOTHING
                    """,
                    failure.failure_id,
                    failure.opportunity_id,
                    failure.trend_key,
                    failure.prediction_id,
                    failure.failure_category.value,
                    failure.root_cause,
                    failure.evidence_quality,
                    json.dumps(failure.missing_information),
                    failure.confidence_error,
                    json.dumps(failure.metadata, default=str),
                )
            return True
        except Exception as exc:
            _log.error("memory.failure_record_failed", error=str(exc))
            return False

    async def category_counts(self, *, days_back: int = 90) -> dict[str, int]:
        """How often each failure category recurs — the reusable knowledge."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT failure_category, COUNT(*) AS n
                    FROM failures
                    WHERE detected_at >= NOW() - ($1 || ' days')::interval
                    GROUP BY failure_category
                    ORDER BY n DESC
                    """,
                    str(days_back),
                )
            return {r["failure_category"]: int(r["n"]) for r in rows}
        except Exception as exc:
            _log.error("memory.failure_counts_failed", error=str(exc))
            return {}
