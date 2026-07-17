"""
aegis.evolve.settlement_loop
============================

PROJECT OMEGA — Phase A (Reality Loop Activation).

The capital trade-outcome path (``SettlementManager`` → ``prediction_outcomes``)
only fires when a real Phase 6 trade settles. In advisory mode — the default —
zero trades execute, so the learning loop never closes and ``prediction_outcomes``
stays empty.

This module closes the loop *without capital* using self-supervised
"falsifiable claims": a prediction commits to a re-observable direction
("this trend's signal COUNT over the next H hours will rise / fall / flat"),
and we settle it by re-reading the ``signals`` table after the horizon.

Two entry points:

* :meth:`SignalOutcomeSettler.settle_pending` — settle live claims whose horizon
  has elapsed (called hourly by the scheduler). Drives ongoing growth.
* :meth:`SignalOutcomeSettler.backfill_from_history` — reconstruct and settle
  claims from signal history ALREADY in the DB, to reach the first 100 outcomes
  on day one. Strictly no look-ahead: the baseline only reads rows at/before the
  claim timestamp; the observation only reads rows in the (claim, settle] window.

A claim is a falsifiable directional bet. We score *lift over base rate*, not raw
accuracy — a metric that rises 80% of the time would make a naive "rise" claim
look skilful, so the caller must compare correct-rate against the base rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from aegis.evolve.constants import ERR_SIGNAL_SETTLE_FAILED
from aegis.evolve.outcomes import OutcomeRecorder
from aegis.evolve.schemas import SignalOutcome

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.evolve.settlement_loop")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# A relative change inside ±this band counts as "flat" rather than rise/fall.
_FLAT_BAND = 0.10

# STAGE 1.3 settlement precondition: a claim may only settle to a direction
# if ingestion was continuously alive across its ENTIRE observation window.
# "Alive" reuses the Block-D container-healthcheck definition — max gap
# between consecutive signals.scraped_at values (including window edges)
# must not exceed AEGIS_INGEST_HEALTH_MAX_AGE_H (default 2h). One notion of
# alive, shared with scripts/healthcheck_ingestion.py — never two.
# Rationale: 633 claims settled against the dead scraper of 2026-07-03..16
# read fall=97.1%/rise=16.4% "accuracy" — measurements of an outage, not a
# market. This precondition is the record whose absence made that possible.
_INGEST_GAP_ENV = "AEGIS_INGEST_HEALTH_MAX_AGE_H"
_INGEST_GAP_DEFAULT_H = 2.0


def _max_ingest_gap_hours_env() -> float:
    import os

    return float(os.environ.get(_INGEST_GAP_ENV, _INGEST_GAP_DEFAULT_H))


async def max_ingest_gap_hours(
    pool: Pool | Any,
    tenant_id: str,
    lo: datetime,
    hi: datetime,
) -> float:
    """Largest gap (hours) in signals.scraped_at coverage over [lo, hi],
    counting the edges — an empty window returns the full window length.

    Module-level so the claim emitter's backfill shares the exact same
    definition instead of growing a second one.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, false)", tenant_id
        )
        row = await conn.fetchrow(
            """
            WITH ts AS (
                SELECT scraped_at FROM signals
                WHERE scraped_at >= $1 AND scraped_at <= $2
                UNION ALL SELECT $1::timestamptz
                UNION ALL SELECT $2::timestamptz
            ),
            gaps AS (
                SELECT EXTRACT(EPOCH FROM
                    scraped_at - lag(scraped_at) OVER (ORDER BY scraped_at)
                ) / 3600.0 AS gap_h
                FROM ts
            )
            SELECT COALESCE(MAX(gap_h), 0.0) AS max_gap_h FROM gaps
            """,
            lo,
            hi,
        )
    return float(row["max_gap_h"]) if row else float("inf")


async def window_scraper_alive(
    pool: Pool | Any, tenant_id: str, lo: datetime, hi: datetime
) -> bool:
    """True when ingestion had no gap larger than the healthcheck window."""
    return await max_ingest_gap_hours(pool, tenant_id, lo, hi) <= _max_ingest_gap_hours_env()


def _classify(baseline: float, observed: float, *, flat_band: float = _FLAT_BAND) -> str:
    """Direction of change from baseline → observed, with a flat dead-band."""
    if baseline <= 0:
        # From a zero baseline, any positive observation is a rise.
        return "rise" if observed > 0 else "flat"
    rel = (observed - baseline) / baseline
    if rel > flat_band:
        return "rise"
    if rel < -flat_band:
        return "fall"
    return "flat"


@dataclass(frozen=True)
class SettlementSummary:
    """Result of a settlement or backfill pass."""

    examined: int = 0
    settled: int = 0
    correct: int = 0
    skipped: int = 0
    voided: int = 0  # observation window had an ingestion gap — no direction

    @property
    def correct_rate(self) -> float:
        return self.correct / self.settled if self.settled else 0.0


class SignalOutcomeSettler:
    """Settle self-supervised signal claims against the ``signals`` table."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id
        self._recorder = OutcomeRecorder(db_pool, tenant_id=tenant_id)

    # ------------------------------------------------------------------
    # Internal: count signals for a trend_key (tag) within a time window.
    # Strictly bounded by [lo, hi) so callers control look-ahead.
    # ------------------------------------------------------------------

    async def _signal_count(self, trend_key: str, lo: datetime, hi: datetime) -> int:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, false)",
                self._tenant_id,
            )
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS n
                FROM signals
                WHERE $1 = ANY(tags)
                  AND ts >= $2 AND ts < $3
                """,
                trend_key,
                lo,
                hi,
            )
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # Live settlement — settle pending claims whose horizon has elapsed.
    # ------------------------------------------------------------------

    async def settle_pending(self, *, limit: int = 500) -> SettlementSummary:
        """Settle every pending claim whose ``settle_after`` has passed."""
        now = datetime.now(UTC)
        examined = settled = correct = skipped = voided = 0
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                rows = await conn.fetch(
                    """
                    SELECT outcome_id, prediction_id, trend_key, metric,
                           claimed_direction, prediction_score, prediction_confidence,
                           baseline_value, horizon_hours, claim_ts, settle_after,
                           settlement_timestamp
                    FROM signal_outcomes
                    WHERE resolution_status = 'pending'
                      AND settle_after <= $1
                    ORDER BY settle_after
                    LIMIT $2
                    """,
                    now,
                    limit,
                )

            for row in rows:
                examined += 1
                # STAGE 1.3 PRECONDITION: settle to a direction ONLY if
                # ingestion was continuously alive across the observation
                # window. A gapped window settles to VOID — a count of zero
                # from a dead scraper is not a market observation.
                alive = await window_scraper_alive(
                    self._pool, self._tenant_id, row["claim_ts"], row["settle_after"]
                )
                if not alive:
                    if await self._void_row(str(row["outcome_id"])):
                        voided += 1
                        _log.warning(
                            "evolve.signal_settle_voided",
                            outcome_id=str(row["outcome_id"]),
                            trend_key=row["trend_key"],
                            reason="ingestion_gap_in_observation_window",
                        )
                    else:
                        skipped += 1
                    continue
                # Observe what happened in the window (claim_ts, settle_after].
                observed = float(
                    await self._signal_count(
                        row["trend_key"], row["claim_ts"], row["settle_after"]
                    )
                )
                observed_dir = _classify(float(row["baseline_value"]), observed)
                is_correct = observed_dir == row["claimed_direction"]
                ok = await self._settle_row(
                    str(row["outcome_id"]),
                    observed_value=observed,
                    observed_direction=observed_dir,
                    is_correct=is_correct,
                )
                if ok:
                    settled += 1
                    correct += int(is_correct)
                    # Phase C: capture as verified opportunity/failure knowledge.
                    await self._capture_settlement(
                        row, observed=observed, observed_dir=observed_dir,
                        is_correct=is_correct,
                    )
                else:
                    skipped += 1

            summary = SettlementSummary(examined, settled, correct, skipped, voided)
            _log.info(
                "evolve.signal_settle_pass",
                examined=examined,
                settled=settled,
                correct=correct,
                voided=voided,
                correct_rate=round(summary.correct_rate, 3),
            )
            return summary
        except Exception as exc:
            _log.error(
                "evolve.signal_settle_failed",
                error=str(exc),
                error_code=ERR_SIGNAL_SETTLE_FAILED,
            )
            return SettlementSummary(examined, settled, correct, skipped, voided)

    async def _settle_row(
        self,
        outcome_id: str,
        *,
        observed_value: float,
        observed_direction: str,
        is_correct: bool,
    ) -> bool:
        status = "correct" if is_correct else "incorrect"
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                await conn.execute(
                    """
                    UPDATE signal_outcomes
                    SET observed_value = $2,
                        observed_direction = $3,
                        resolution_status = $4,
                        settled_at = NOW(),
                        window_scraper_alive = TRUE
                    WHERE outcome_id = $1 AND resolution_status = 'pending'
                    """,
                    outcome_id,
                    observed_value,
                    observed_direction,
                    status,
                )
            return True
        except Exception as exc:
            _log.error("evolve.signal_settle_row_failed", error=str(exc))
            return False

    async def _void_row(self, outcome_id: str) -> bool:
        """STAGE 1.3: settle a claim to VOID — its observation window had an
        ingestion gap, so no directional label is derivable from it."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                await conn.execute(
                    """
                    UPDATE signal_outcomes
                    SET resolution_status = 'void',
                        window_scraper_alive = FALSE,
                        settled_at = NOW()
                    WHERE outcome_id = $1 AND resolution_status = 'pending'
                    """,
                    outcome_id,
                )
            return True
        except Exception as exc:
            _log.error("evolve.signal_void_row_failed", error=str(exc))
            return False

    async def _capture_settlement(
        self,
        row: Any,
        *,
        observed: float,
        observed_dir: str,
        is_correct: bool,
    ) -> None:
        """Phase C hook: persist the settled claim as opportunity/failure memory.

        Best-effort and flag-guarded inside ``MemoryCapture`` — a failure here
        must never disrupt settlement. Imported lazily so the settler has no hard
        dependency on the memory package.
        """
        try:
            from aegis.memory import MemoryCapture

            await MemoryCapture(self._pool, tenant_id=self._tenant_id).on_settlement(
                {
                    "prediction_id": row["prediction_id"],
                    "trend_key": row["trend_key"],
                    "claimed_direction": row["claimed_direction"],
                    "observed_direction": observed_dir,
                    "observed_value": observed,
                    "prediction_score": row["prediction_score"],
                    "prediction_confidence": row["prediction_confidence"],
                    "baseline_value": row["baseline_value"],
                    "horizon_hours": row["horizon_hours"],
                    "settlement_timestamp": row["settlement_timestamp"],
                    "resolution_status": "correct" if is_correct else "incorrect",
                }
            )
        except Exception as exc:  # never break settlement on a memory error
            _log.debug("evolve.memory_capture_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Backfill — reconstruct + settle claims from existing signal history.
    # ------------------------------------------------------------------

    async def backfill_from_history(
        self,
        *,
        horizon_hours: int = 72,
        max_claims: int = 200,
        min_baseline_signals: int = 3,
        lookback_days: int = 60,
    ) -> SettlementSummary:
        """
        Walk historical ``signals`` grouped by tag, synthesise a falsifiable
        claim at each eligible point, and settle it immediately against the
        already-known future window — with NO look-ahead leakage:

            baseline window:    [claim_ts - horizon, claim_ts)
            observation window: [claim_ts, claim_ts + horizon)

        A claim is only created when both windows are fully in the past, so the
        observation window is reconstructed, never predicted from the future
        relative to ``claim_ts``.
        """
        now = datetime.now(UTC)
        horizon = timedelta(hours=horizon_hours)
        # The latest a claim can sit and still have a complete, past observation
        # window is now - horizon. Baseline needs one horizon before that.
        earliest = now - timedelta(days=lookback_days)
        examined = settled = correct = skipped = voided = 0

        try:
            # Candidate (trend_key, claim_ts) anchors: the median signal ts per
            # tag/day. One claim per tag/day keeps the set diverse and bounded.
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                anchors = await conn.fetch(
                    """
                    SELECT tag AS trend_key,
                           date_trunc('day', ts) AS day,
                           MIN(ts) AS claim_ts
                    FROM signals, unnest(tags) AS tag
                    WHERE ts >= $1 AND ts < $2
                    GROUP BY tag, date_trunc('day', ts)
                    HAVING COUNT(*) >= $3
                    ORDER BY MIN(ts)
                    LIMIT $4
                    """,
                    earliest,
                    now - horizon,  # claim_ts must allow a full past obs window
                    min_baseline_signals,
                    max_claims,
                )

            for row in anchors:
                examined += 1
                trend_key = row["trend_key"]
                claim_ts: datetime = row["claim_ts"]
                obs_end = claim_ts + horizon
                if obs_end > now:
                    skipped += 1
                    continue

                # STAGE 1.3 PRECONDITION: never reconstruct a claim whose
                # observation window overlaps an ingestion gap — that is how
                # 633 dead-scraper settlements got labeled as market outcomes.
                if not await window_scraper_alive(
                    self._pool, self._tenant_id, claim_ts, obs_end
                ):
                    voided += 1
                    continue

                baseline = float(
                    await self._signal_count(trend_key, claim_ts - horizon, claim_ts)
                )
                if baseline < min_baseline_signals:
                    skipped += 1
                    continue
                observed = float(
                    await self._signal_count(trend_key, claim_ts, obs_end)
                )
                observed_dir = _classify(baseline, observed)

                # The synthetic claim: the heuristic prior is "active trends keep
                # producing signals" → claim 'rise'. This is a real, falsifiable
                # directional bet whose correctness we now measure honestly.
                claimed = "rise"
                is_correct = observed_dir == claimed

                outcome = SignalOutcome(
                    prediction_id=f"backfill-{trend_key}-{claim_ts:%Y%m%d}",
                    trend_key=trend_key,
                    metric="signal_count",
                    claimed_direction=claimed,
                    prediction_score=0.6,
                    prediction_confidence=0.5,
                    baseline_value=baseline,
                    horizon_hours=horizon_hours,
                    claim_ts=claim_ts,
                    settle_after=obs_end,
                    observed_value=observed,
                    observed_direction=observed_dir,
                    settled_at=now,
                    settlement_timestamp=obs_end,
                    resolution_status="correct" if is_correct else "incorrect",
                    metadata={"source": "backfill"},
                    window_scraper_alive=True,
                )
                if await self._recorder.record_signal_outcome(outcome):
                    settled += 1
                    correct += int(is_correct)
                else:
                    skipped += 1

            summary = SettlementSummary(examined, settled, correct, skipped, voided)
            _log.info(
                "evolve.signal_backfill",
                examined=examined,
                settled=settled,
                correct=correct,
                voided=voided,
                correct_rate=round(summary.correct_rate, 3),
            )
            return summary
        except Exception as exc:
            _log.error(
                "evolve.signal_backfill_failed",
                error=str(exc),
                error_code=ERR_SIGNAL_SETTLE_FAILED,
            )
            return SettlementSummary(examined, settled, correct, skipped, voided)
