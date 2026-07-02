"""
aegis.trust.claim_emitter — Phase B Stage 0 (close the data gap)
================================================================

Phase A proved the learning loop closes, but its backfill wrote a CONSTANT
confidence (0.5) — so there is nothing to calibrate. This module emits the
model's OWN, VARYING confidence as a falsifiable claim, so that once the claims
settle we have genuine ``(p, y)`` pairs for calibration.

For each active trend tag it:
  1. fetches the tag's recent signal rows,
  2. builds a FeatureWindow (pure, deterministic) and runs ``heuristic_predict``,
  3. records a PENDING ``signal_outcome`` carrying the model's real probability
     of the claimed direction (``p_breakout`` for a 'rise' claim).

The existing hourly ``SignalOutcomeSettler.settle_pending`` resolves it after the
horizon. No new models, no neural networks — this reuses the existing heuristic
predictor unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from aegis.evolve.outcomes import OutcomeRecorder
from aegis.evolve.schemas import SignalOutcome
from aegis.trust.calibrator import Calibrator, rise_probability

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.trust.claim_emitter")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
_DEFAULT_HORIZON_H = 72
# Confidence band for direction: within this of 0.5 -> 'flat'.
_FLAT_BAND = 0.05
# Honesty bounds on emitted confidence. A media-velocity heuristic forecasting a
# noisy 72h-ahead target must NEVER claim certainty: p_decline=0 from a degenerate
# window otherwise produced p=1.0 claims that, measured live, were only ~75%
# correct — a single bucket that dominated the Brier loss and drove brier_skill
# negative. Clamping the extreme removes that sink without touching the
# discriminating middle of the distribution.
_CONF_CEIL = 0.95
_CONF_FLOOR = 0.50


def _clamp_conf(p: float) -> float:
    """Clamp claimed-direction confidence to the honest [floor, ceil] band."""
    return min(_CONF_CEIL, max(_CONF_FLOOR, p))


@dataclass(frozen=True)
class EmitSummary:
    examined: int = 0
    emitted: int = 0
    skipped: int = 0


def _row_to_builder_dict(r: dict[str, Any]) -> dict[str, Any]:
    """Map a signals-table row to the shape build_window_from_rows expects."""
    return {
        "id": str(r.get("signal_id", "")),
        "platform": r.get("platform", "unknown"),
        "captured_at": r.get("ts"),
        "title": r.get("title"),
        "body": r.get("pii_scrubbed_text"),
        "url": r.get("url"),
        "content_hash": r.get("external_id", ""),
        "author_id": str(r["author_id"]) if r.get("author_id") else None,
        "views": r.get("views"),
        "likes": r.get("likes"),
        "comments": r.get("comments"),
        "shares": r.get("shares"),
        "saves": r.get("saves"),
        "sentiment": float(r["source_confidence"]) if r.get("source_confidence") else 0.0,
        "commercial_intent": 0.0,
        "novelty": float(r["completeness"]) if r.get("completeness") else 0.0,
    }


class ClaimEmitter:
    """Emit varying-confidence falsifiable claims from the heuristic predictor."""

    def __init__(
        self,
        db_pool: Pool,
        tenant_id: str = _DEFAULT_TENANT,
        calibrator: Calibrator | None = None,
    ) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id
        self._recorder = OutcomeRecorder(db_pool, tenant_id=tenant_id)
        # The audited, truthful P(rise) transform. Identity until a map is fitted.
        self._cal = calibrator or Calibrator.identity()

    def _rise_p(self, pred) -> tuple[float, float]:  # noqa: ANN001 — Prediction
        """Return (raw, calibrated) P(rise). raw = 1 - p_decline (the audited
        discriminating signal); calibrated = isotonic(raw). The raw value is
        persisted so the calibration map can be refit without recompute."""
        raw = rise_probability(float(pred.p_decline))
        return raw, self._cal.apply(raw)

    async def _capture_claim(self, outcome: SignalOutcome) -> None:
        """Phase C hook: record a freshly-emitted live claim as a pending
        opportunity. Best-effort + flag-guarded inside ``MemoryCapture``; a
        memory error must never abort claim emission. Lazy import keeps the
        trust package free of a hard dependency on aegis.memory."""
        try:
            from aegis.memory import MemoryCapture

            await MemoryCapture(self._pool, tenant_id=self._tenant_id).on_claim(
                prediction_id=outcome.prediction_id,
                trend_key=outcome.trend_key,
                claimed_direction=outcome.claimed_direction,
                prediction_score=outcome.prediction_score,
                prediction_confidence=outcome.prediction_confidence,
                baseline_value=float(outcome.baseline_value),
                horizon_hours=outcome.horizon_hours,
            )
        except Exception as exc:  # never break emission on a memory error
            _log.debug("trust.memory_capture_failed", error=str(exc))

    async def emit_active_claims(
        self,
        *,
        horizon_hours: int = _DEFAULT_HORIZON_H,
        max_trends: int = 100,
        min_signals: int = 5,
        baseline_window_hours: int = 72,
    ) -> EmitSummary:
        from aegis.predict.features.builder import build_window_from_rows
        from aegis.predict.models.heuristic import heuristic_predict

        now = datetime.now(UTC)
        window_start = now - timedelta(hours=baseline_window_hours)
        examined = emitted = skipped = 0

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                tags = await conn.fetch(
                    """
                    SELECT tag AS trend_key, COUNT(*) AS n
                    FROM signals, unnest(tags) AS tag
                    WHERE ts >= $1
                    GROUP BY tag
                    HAVING COUNT(*) >= $2
                    ORDER BY COUNT(*) DESC
                    LIMIT $3
                    """,
                    window_start,
                    min_signals,
                    max_trends,
                )

            for tag_row in tags:
                examined += 1
                trend_key = tag_row["trend_key"]
                baseline = int(tag_row["n"])
                try:
                    async with self._pool.acquire() as conn:
                        await conn.execute(
                            "SELECT set_config('app.current_tenant', $1, false)",
                            self._tenant_id,
                        )
                        rows = await conn.fetch(
                            """
                            SELECT signal_id, platform, ts, title, pii_scrubbed_text,
                                   url, external_id, author_id, views, likes, comments,
                                   shares, saves, source_confidence, completeness
                            FROM signals
                            WHERE $1 = ANY(tags) AND ts >= $2
                            ORDER BY ts
                            """,
                            trend_key,
                            window_start,
                        )
                    window = build_window_from_rows(
                        trend_id=trend_key,
                        tenant_id=self._tenant_id,
                        rows=[_row_to_builder_dict(dict(r)) for r in rows],
                        window_end=now,
                    )
                    preds = heuristic_predict(
                        window=window, graph=None, horizons=(horizon_hours,)
                    )
                    if not preds:
                        skipped += 1
                        continue
                    pred = preds[0]
                    # Truthful, calibrated P(rise) from the audited signal.
                    raw_rise, p_rise = self._rise_p(pred)
                    claimed = "rise" if p_rise >= 0.5 else "fall"
                    # Confidence is the probability of the CLAIMED direction,
                    # clamped to the honest band (never 100% certain).
                    p = _clamp_conf(p_rise if claimed == "rise" else 1.0 - p_rise)

                    outcome = SignalOutcome(
                        prediction_id=f"heuristic-{trend_key}-{now:%Y%m%d%H}",
                        trend_key=trend_key,
                        metric="signal_count",
                        claimed_direction=claimed,
                        prediction_score=p_rise,
                        prediction_confidence=p,
                        baseline_value=float(baseline),
                        horizon_hours=horizon_hours,
                        claim_ts=now,
                        settle_after=now + timedelta(hours=horizon_hours),
                        resolution_status="pending",
                        metadata={
                            "source": "heuristic_emit",
                            "stage": str(pred.stage),
                            "raw_rise": raw_rise,
                        },
                    )
                    if await self._recorder.record_signal_outcome(outcome):
                        emitted += 1
                        # Phase C: record the live claim as a pending opportunity.
                        await self._capture_claim(outcome)
                    else:
                        skipped += 1
                except Exception as exc:  # one bad tag must not abort the batch
                    skipped += 1
                    _log.debug("trust.emit_tag_failed", trend_key=trend_key, error=str(exc))

            _log.info(
                "trust.claims_emitted",
                examined=examined,
                emitted=emitted,
                skipped=skipped,
            )
            return EmitSummary(examined, emitted, skipped)
        except Exception as exc:
            _log.error("trust.emit_failed", error=str(exc))
            return EmitSummary(examined, emitted, skipped)

    async def _signal_count(self, trend_key: str, lo: datetime, hi: datetime) -> int:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
            )
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM signals "
                "WHERE $1 = ANY(tags) AND ts >= $2 AND ts < $3",
                trend_key, lo, hi,
            )
        return int(row["n"]) if row else 0

    async def backfill_heuristic_claims(
        self,
        *,
        horizon_hours: int = _DEFAULT_HORIZON_H,
        max_claims: int = 500,
        min_baseline_signals: int = 3,
        lookback_days: int = 120,
    ) -> EmitSummary:
        """
        Reconstruct + settle VARYING-confidence claims from signal history.

        Identical no-look-ahead windows as Phase A's backfill, but the confidence
        ``p`` is the heuristic's real ``p_breakout`` computed from signals strictly
        BEFORE ``claim_ts`` — never the future. Produces immediately-settled
        ``(p, y)`` pairs that make calibration statistically meaningful today.
        """
        from aegis.predict.features.builder import build_window_from_rows
        from aegis.predict.models.heuristic import heuristic_predict

        now = datetime.now(UTC)
        horizon = timedelta(hours=horizon_hours)
        earliest = now - timedelta(days=lookback_days)
        examined = emitted = skipped = 0
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                anchors = await conn.fetch(
                    """
                    SELECT tag AS trend_key, MIN(ts) AS claim_ts
                    FROM signals, unnest(tags) AS tag
                    WHERE ts >= $1 AND ts < $2
                    GROUP BY tag, date_trunc('day', ts)
                    HAVING COUNT(*) >= $3
                    ORDER BY MIN(ts)
                    LIMIT $4
                    """,
                    earliest, now - horizon, min_baseline_signals, max_claims,
                )

            for row in anchors:
                examined += 1
                trend_key = row["trend_key"]
                claim_ts: datetime = row["claim_ts"]
                obs_end = claim_ts + horizon
                if obs_end > now:
                    skipped += 1
                    continue
                baseline = await self._signal_count(
                    trend_key, claim_ts - horizon, claim_ts
                )
                if baseline < min_baseline_signals:
                    skipped += 1
                    continue
                # Window built ONLY from signals before claim_ts (no look-ahead).
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "SELECT set_config('app.current_tenant', $1, false)",
                        self._tenant_id,
                    )
                    rows = await conn.fetch(
                        """
                        SELECT signal_id, platform, ts, title, pii_scrubbed_text,
                               url, external_id, author_id, views, likes, comments,
                               shares, saves, source_confidence, completeness
                        FROM signals
                        WHERE $1 = ANY(tags) AND ts >= $2 AND ts < $3
                        ORDER BY ts
                        """,
                        trend_key, claim_ts - horizon, claim_ts,
                    )
                window = build_window_from_rows(
                    trend_id=trend_key,
                    tenant_id=self._tenant_id,
                    rows=[_row_to_builder_dict(dict(r)) for r in rows],
                    window_end=claim_ts,
                )
                preds = heuristic_predict(
                    window=window, graph=None, horizons=(horizon_hours,)
                )
                if not preds:
                    skipped += 1
                    continue
                # Truthful, calibrated P(rise) from the audited 1 - p_decline.
                raw_rise, p_rise = self._rise_p(preds[0])
                claimed = "rise" if p_rise >= 0.5 else "fall"
                p = _clamp_conf(p_rise if claimed == "rise" else 1.0 - p_rise)

                observed = await self._signal_count(trend_key, claim_ts, obs_end)
                rel = (observed - baseline) / baseline if baseline else 0.0
                observed_dir = "rise" if rel > 0.10 else ("fall" if rel < -0.10 else "flat")
                is_correct = observed_dir == claimed

                outcome = SignalOutcome(
                    prediction_id=f"heuristic-bf-{trend_key}-{claim_ts:%Y%m%d}",
                    trend_key=trend_key,
                    metric="signal_count",
                    claimed_direction=claimed,
                    prediction_score=p_rise,
                    prediction_confidence=p,
                    baseline_value=float(baseline),
                    horizon_hours=horizon_hours,
                    claim_ts=claim_ts,
                    settle_after=obs_end,
                    observed_value=float(observed),
                    observed_direction=observed_dir,
                    settled_at=now,
                    settlement_timestamp=obs_end,
                    resolution_status="correct" if is_correct else "incorrect",
                    metadata={"source": "heuristic_backfill", "raw_rise": raw_rise},
                )
                if await self._recorder.record_signal_outcome(outcome):
                    emitted += 1
                else:
                    skipped += 1

            _log.info(
                "trust.heuristic_backfill",
                examined=examined, emitted=emitted, skipped=skipped,
            )
            return EmitSummary(examined, emitted, skipped)
        except Exception as exc:
            _log.error("trust.heuristic_backfill_failed", error=str(exc))
            return EmitSummary(examined, emitted, skipped)
