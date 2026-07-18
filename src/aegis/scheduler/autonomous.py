"""
AEGIS Pulse — Autonomous Scheduler.
Runs the system without human intervention.

Schedule:
  Every 5 min:   self-healing health check (PASS5-5A) → aegis:core:health stream
  Every 15 min:  scrape trending topics → write to DB
  Every 1 hour:  run agent analysis on fresh signals
  Every 6 hours: drift check
  Every 30 min:  health report to log
  Every Sunday 2am UTC: trigger model retraining

Run standalone: uv run python src/aegis/scheduler/autonomous.py
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from datetime import UTC, datetime
from typing import Any

import structlog

log = structlog.get_logger("aegis.scheduler.autonomous")

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    _SCHED_AVAILABLE = True
except ImportError:
    _SCHED_AVAILABLE = False

_DEFAULT_TOPICS = [
    "dropshipping",
    "print on demand",
    "trending gadgets",
    "ecommerce arbitrage",
    "viral products",
    "amazon trending",
]


def _topics() -> list[str]:
    """Scrape topics, config-driven via ``AEGIS_AUTONOMOUS_TOPICS`` (comma-sep).

    Falls back to ``_DEFAULT_TOPICS`` when the env var is unset/empty so the
    scheduler is never hardcoded but always has a sane default.
    """
    import os

    raw = os.environ.get("AEGIS_AUTONOMOUS_TOPICS", "").strip()
    if not raw:
        return _DEFAULT_TOPICS
    topics = [t.strip() for t in raw.split(",") if t.strip()]
    return topics or _DEFAULT_TOPICS


# Backwards-compatible module attribute (tests + callers may import TOPICS).
TOPICS = _DEFAULT_TOPICS

# Net-new-yield breaker: a topic that inserts < this many NET-NEW signals goes
# on a cooldown so we stop burning CPU re-scraping + re-deduping the same dead
# headlines every 15 min (the cause of the autonomous loop's 99% CPU spin).
_SCRAPE_BATCH = 3
_YIELD_MIN_INSERTED = 1
_COOLDOWN_CYCLES = 4
_REDIS_OFFSET_KEY = "aegis:scheduler:scrape_offset"
_REDIS_COOLDOWN_KEY = "aegis:scheduler:scrape_cooldown"  # hash topic -> cycles left


async def _select_scrape_topics(redis: Any, all_topics: list[str]) -> list[str]:
    """Pick the next batch of topics: rotate through the full list (so every
    topic is eventually covered, not just the first 3) and skip any topic still
    on yield-cooldown. Pure fallback to ``all_topics[:batch]`` when Redis is
    absent so the scheduler never depends on Redis to make progress."""
    if redis is None or not all_topics:
        return all_topics[:_SCRAPE_BATCH]
    try:
        cooldown = await redis.hgetall(_REDIS_COOLDOWN_KEY) or {}
        cooling = {
            (k.decode() if isinstance(k, bytes | bytearray) else k)
            for k, v in cooldown.items()
            if int(v) > 0
        }
        offset = int(await redis.get(_REDIS_OFFSET_KEY) or 0)
        n = len(all_topics)
        picked: list[str] = []
        i = 0
        while len(picked) < min(_SCRAPE_BATCH, n) and i < n:
            t = all_topics[(offset + i) % n]
            if t not in cooling and t not in picked:
                picked.append(t)
            i += 1
        # Advance the rotation offset for the next cycle.
        await redis.set(_REDIS_OFFSET_KEY, (offset + max(i, 1)) % n)
        # If everything is cooling, fall back to the rotation window so we still
        # make progress (a dead topic occasionally retrying is fine).
        return picked or all_topics[offset % n : offset % n + _SCRAPE_BATCH]
    except Exception as exc:
        log.debug("scheduler.scrape.select_fallback", error=str(exc)[:120])
        return all_topics[:_SCRAPE_BATCH]


async def _record_topic_yield(redis: Any, topic: str, inserted: int) -> None:
    """Update a topic's cooldown counter from its net-new yield. Zero-yield ->
    set cooldown; positive yield -> clear it. Best-effort; never raises."""
    if redis is None:
        return
    try:
        if inserted >= _YIELD_MIN_INSERTED:
            await redis.hdel(_REDIS_COOLDOWN_KEY, topic)
        else:
            await redis.hset(_REDIS_COOLDOWN_KEY, topic, _COOLDOWN_CYCLES)
    except Exception as exc:
        log.debug("scheduler.scrape.yield_record_failed", error=str(exc)[:120])


async def _decay_cooldowns(redis: Any) -> None:
    """Tick every cooling topic's counter down by one so cooldowns expire and
    topics get periodically retried. Best-effort; never raises."""
    if redis is None:
        return
    try:
        cooldown = await redis.hgetall(_REDIS_COOLDOWN_KEY) or {}
        for k, v in cooldown.items():
            topic = k.decode() if isinstance(k, bytes | bytearray) else k
            left = int(v) - 1
            if left <= 0:
                await redis.hdel(_REDIS_COOLDOWN_KEY, topic)
            else:
                await redis.hset(_REDIS_COOLDOWN_KEY, topic, left)
    except Exception as exc:
        log.debug("scheduler.scrape.decay_failed", error=str(exc)[:120])


async def _notify_ops(title: str, message: str, *, priority: str = "5") -> None:
    """Push an operations alert to the operator's phone via ntfy (zero-key).

    Recovery protocol 1.7: the FIRST message this system must be capable of
    sending is not a verdict — it is "ingestion is dead". No-op when
    AEGIS_NTFY_TOPIC is unset; delivery failure logs at WARNING (we cannot
    notify about failing to notify, but we can refuse to be silent about it).
    """
    import os

    topic = os.environ.get("AEGIS_NTFY_TOPIC", "").strip()
    if not topic:
        return
    base = os.environ.get("AEGIS_NTFY_BASE_URL", "https://ntfy.sh").rstrip("/")
    try:
        import httpx

        # HTTP header values are latin-1 by spec; an em-dash in the title
        # crashed the very first live delivery attempt (GATE 1.7 demo).
        safe_title = title.encode("ascii", "replace").decode("ascii")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{base}/{topic}",
                content=message.encode("utf-8"),
                headers={
                    "Title": safe_title,
                    "Priority": priority,
                    "Tags": "rotating_light",
                },
            )
        log.info("scheduler.ops_notify_sent", title=title, status=resp.status_code)
    except Exception as exc:
        log.warning("scheduler.ops_notify_failed", title=title, error=str(exc)[:120])


async def job_scrape() -> None:
    """Scrape a rotating batch of topics, skipping dead-yield ones (cooldown)."""
    log.info("scheduler.scrape.start")
    total = 0
    inserted = 0
    redis = None
    try:
        from uuid import UUID

        import redis.asyncio as redis_lib

        from aegis.config import settings as _settings
        from aegis.db.pool import PgPool
        from aegis.scrape.topic import scrape_topic

        cfg = _settings()
        tenant_id = UUID(str(cfg.default_tenant_id))
        try:
            redis = redis_lib.from_url(cfg.redis_url_str)
        except Exception:
            redis = None

        await _decay_cooldowns(redis)
        topics = await _select_scrape_topics(redis, _topics())
        log.info("scheduler.scrape.selected", topics=topics)

        # A live pool + tenant_id are REQUIRED for scrape_topic to persist.
        # Without them the persistence block is silently skipped (inserted=0).
        # NB: scrape_topic -> insert_signals calls pool.acquire(tenant_id=...),
        # which only the PgPool wrapper supports (not a raw asyncpg pool).
        pool = PgPool(dsn=cfg.pg_dsn_str)
        await pool.connect()
        try:
            for topic in topics:
                try:
                    result = await scrape_topic(
                        topic,
                        limit_per_source=20,
                        pool=pool,
                        tenant_id=tenant_id,
                    )
                    total += result.total_unique
                    inserted += result.total_inserted
                    await _record_topic_yield(redis, topic, result.total_inserted)
                    log.info(
                        "scheduler.scrape.topic",
                        topic=topic,
                        signals=result.total_unique,
                        inserted=result.total_inserted,
                    )
                except Exception as exc:
                    log.warning("scheduler.scrape.topic_error", topic=topic, error=str(exc)[:100])
        finally:
            await pool.aclose()
        # LAYER (a) — 2026-07-03..16 blackout: 13 days of zero-insert cycles
        # logged as per-topic WARNINGs and a benign-looking "done". A cycle
        # that lands ZERO new rows across ALL topics is not the low end of
        # normal (healthy cycles measured 2..819 rows/hour); it is a dead
        # cycle and must be an ERROR with its own event name.
        if inserted == 0:
            log.error(
                "scheduler.scrape.cycle_zero_yield",
                total=total,
                inserted=0,
                topics=topics,
            )
            await _notify_ops(
                "AEGIS: scrape cycle DEAD",
                f"Zero new rows across all topics {topics}. "
                "Healthy cycles land 2..819 rows/hour; zero is dead, not quiet.",
            )
        log.info("scheduler.scrape.done", total=total, inserted=inserted)
    except Exception as exc:
        log.error("scheduler.scrape.fatal", error=str(exc)[:200])
    finally:
        if redis is not None:
            with contextlib.suppress(Exception):
                await redis.aclose()


# Hard-refusal staleness ceiling for job_analyze, in hours. Guard is on
# MAX(scraped_at) DIRECTLY — never on data_confidence, which defaults to 1.0
# on the analyze path (that default is exactly how "data_confidence: 1.0 on
# 12-day-stale data" shipped hourly during the 2026-07 blackout).
_ANALYZE_MAX_STALENESS_H_ENV = "AEGIS_ANALYZE_MAX_STALENESS_H"
_ANALYZE_MAX_STALENESS_H_DEFAULT = 6.0


async def _newest_signal_age_hours() -> float | None:
    """Hours since the newest ``signals.scraped_at``; None if the table is
    empty. Raises on DB failure — the caller must treat that as unverifiable
    freshness and refuse, not proceed."""
    import asyncpg

    from aegis.config import settings as _settings

    cfg = _settings()
    conn = await asyncpg.connect(cfg.pg_dsn_str, timeout=10)
    try:
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, false)",
            str(cfg.default_tenant_id),
        )
        row = await conn.fetchrow(
            "SELECT EXTRACT(EPOCH FROM (now() - MAX(scraped_at))) / 3600.0 AS age_h"
            " FROM signals"
        )
        if row is None or row["age_h"] is None:
            return None
        return float(row["age_h"])
    finally:
        await conn.close()


async def job_analyze() -> None:
    """Run agent analysis on recent signals.

    HARD REFUSAL (recovery protocol 1.2): when the newest signal is older
    than the staleness ceiling, this job REFUSES — no subprocess, no stream
    write, no alert. Not a degraded run, not a lowered confidence: nothing.
    A verdict on dead data is worse than no verdict.
    """
    log.info("scheduler.analyze.start")
    try:
        import os

        max_age_h = float(
            os.environ.get(_ANALYZE_MAX_STALENESS_H_ENV, _ANALYZE_MAX_STALENESS_H_DEFAULT)
        )
        try:
            age_h = await _newest_signal_age_hours()
        except Exception as exc:
            # Freshness unverifiable == stale until proven otherwise.
            log.error(
                "scheduler.analyze.refused_freshness_unverifiable",
                error=str(exc)[:200],
            )
            return
        if age_h is None or age_h > max_age_h:
            log.error(
                "scheduler.analyze.refused_stale_data",
                staleness_hours=None if age_h is None else round(age_h, 2),
                max_allowed_hours=max_age_h,
            )
            await _notify_ops(
                "AEGIS: ingestion dead — analysis refused",
                f"Newest signal is {'unknown' if age_h is None else round(age_h, 1)}h old "
                f"(ceiling {max_age_h}h). No verdict was emitted.",
            )
            return

        proc = await asyncio.create_subprocess_exec(
            # Direct venv entrypoint — uv does not exist in the lockfile-driven
            # runtime image (it was only ever present by install-order accident).
            "aegis",
            "analyze",
            "--limit",
            "30",
            "--no-llm",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode == 0:
            log.info("scheduler.analyze.done")
        else:
            log.warning("scheduler.analyze.failed", stderr=(stderr or b"").decode()[:200])
    except Exception as exc:
        log.error("scheduler.analyze.fatal", error=str(exc)[:200])


async def job_drift_check() -> None:
    """Run Evidently drift check."""
    log.info("scheduler.drift.start")
    try:
        from aegis.predict.drift_monitor import run_drift_check

        await run_drift_check()
        log.info("scheduler.drift.done")
    except Exception as exc:
        log.warning("scheduler.drift.error", error=str(exc)[:200])


async def job_settle_claims() -> None:
    """PROJECT OMEGA Phase A: settle self-supervised signal claims (hourly).

    Closes the learning loop without capital. Reads pending falsifiable claims
    whose horizon has elapsed and settles them against re-scraped signal reality.
    Never raises.
    """
    log.info("scheduler.settle.start")
    try:
        import asyncpg

        from aegis.config import settings as _settings
        from aegis.evolve.settlement_loop import SignalOutcomeSettler

        pool = await asyncpg.create_pool(
            _settings().pg_dsn_str,
            min_size=1,
            max_size=2,
        )
        try:
            summary = await SignalOutcomeSettler(pool).settle_pending()
            log.info(
                "scheduler.settle.done",
                examined=summary.examined,
                settled=summary.settled,
                correct=summary.correct,
                correct_rate=round(summary.correct_rate, 3),
            )
        finally:
            await pool.close()
    except Exception as exc:
        log.warning("scheduler.settle.error", error=str(exc)[:200])


async def job_emit_claims() -> None:
    """PROJECT OMEGA Phase B: emit live varying-confidence falsifiable claims (hourly).

    Loads the fitted isotonic calibration map and runs ``ClaimEmitter.
    emit_active_claims`` so every active trend records a PENDING signal_outcome
    carrying the heuristic's own calibrated P(rise). Once the horizon elapses,
    ``job_settle_claims`` resolves these into real ``(p, y)`` pairs — closing the
    calibration loop on capital-free ground truth. Never raises.
    """
    log.info("scheduler.emit_claims.start")
    try:
        import asyncpg

        from aegis.config import settings as _settings
        from aegis.trust.claim_emitter import ClaimEmitter
        from aegis.trust.store import TrustStore

        pool = await asyncpg.create_pool(_settings().pg_dsn_str, min_size=1, max_size=2)
        try:
            calibrator = await TrustStore(pool).load_map()
            summary = await ClaimEmitter(pool, calibrator=calibrator).emit_active_claims()
            log.info(
                "scheduler.emit_claims.done",
                examined=summary.examined,
                emitted=summary.emitted,
                skipped=summary.skipped,
                calibrated=bool(calibrator.knots),
            )
        finally:
            await pool.close()
    except Exception as exc:
        log.warning("scheduler.emit_claims.error", error=str(exc)[:200])


async def job_refit_calibration() -> None:
    """PROJECT OMEGA Phase B: refit + persist the isotonic P(rise) map (daily 01:30 UTC).

    Reads settled ``(raw_rise, did-rise)`` pairs from ``signal_outcomes`` and
    upserts a fresh calibration map into ``calibration_maps``. The emitter loads
    it on the next cycle, so confidence becomes progressively better calibrated
    as more claims settle. No-ops gracefully on thin / low-variance data
    (Calibrator.fit returns identity). Never raises.
    """
    log.info("scheduler.refit_calibration.start")
    try:
        import asyncpg

        from aegis.config import settings as _settings
        from aegis.trust.calibration import calibration_report
        from aegis.trust.calibrator import Calibrator
        from aegis.trust.store import TrustStore

        cfg = _settings()
        pool = await asyncpg.create_pool(cfg.pg_dsn_str, min_size=1, max_size=2)
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    str(cfg.default_tenant_id),
                )
                rows = await conn.fetch(
                    "SELECT (metadata->>'raw_rise')::float AS raw, "
                    "observed_direction AS d FROM signal_outcomes "
                    "WHERE resolution_status IN ('correct','incorrect') "
                    "AND window_scraper_alive = TRUE "
                    "AND metadata ? 'raw_rise' AND observed_direction IS NOT NULL"
                )
                # Self-measurement: how skilful is the DEPLOYED confidence the
                # system actually emits (prediction_confidence vs realized hit)?
                # Persisted to calibration_snapshots so brier_skill is never
                # stale — previously NO job wrote snapshots and the metric was
                # frozen at its last manual `aegis trust` run.
                snap_rows = await conn.fetch(
                    "SELECT prediction_confidence::float AS p, "
                    "(claimed_direction = observed_direction)::int AS y "
                    "FROM signal_outcomes "
                    "WHERE resolution_status IN ('correct','incorrect') "
                    "AND window_scraper_alive = TRUE"
                )
            ps = [float(r["raw"]) for r in rows]
            ys = [1.0 if r["d"] == "rise" else 0.0 for r in rows]
            cal = Calibrator.fit(ps, ys)
            await TrustStore(pool).save_map(cal)

            snap_ps = [float(r["p"]) for r in snap_rows]
            snap_ys = [float(r["y"]) for r in snap_rows]
            report = calibration_report(
                snap_ps, snap_ys, entity_kind="model", entity_id="heuristic"
            )
            await TrustStore(pool).save_snapshot(report)
            log.info(
                "scheduler.refit_calibration.done",
                n_fit=cal.n_fit,
                knots=len(cal.knots),
                base_rate=round(cal.base_rate, 4),
                snapshot_status=report.status,
                brier_skill=report.brier_skill_score,
            )
        finally:
            await pool.close()
    except Exception as exc:
        log.warning("scheduler.refit_calibration.error", error=str(exc)[:200])


async def job_knowledge_refresh() -> None:
    """PROJECT OMEGA Phase C: refresh source profiles + emit a self-audit.

    Rebuilds per-platform Source Memory from settled outcomes and logs the
    weekly self-audit (best/worst opportunities, sources, recurring failures).
    Best-effort — never raises.
    """
    log.info("scheduler.knowledge.start")
    try:
        import asyncpg

        from aegis.config import settings as _settings
        from aegis.memory.report import SelfAudit
        from aegis.memory.source import SourceMemory

        if not _settings().memory_enabled:
            return
        pool = await asyncpg.create_pool(_settings().pg_dsn_str, min_size=1, max_size=2)
        try:
            profiles = await SourceMemory(pool).build_profiles()
            report = await SelfAudit(pool).weekly_report()
            log.info(
                "scheduler.knowledge.done",
                sources=len(profiles),
                opportunity_patterns=report.get("opportunity_patterns", 0),
                recurring_failures=report.get("recurring_failures", {}),
            )
        finally:
            await pool.close()
    except Exception as exc:
        log.warning("scheduler.knowledge.error", error=str(exc)[:200])


async def job_sentinel() -> None:
    """Autonomous market discovery (every 45 min).

    Sweeps the keyless global-radar adapters, detects velocity breakouts via
    the real-time ``PatternEngine``, and auto-runs a full ``ProductIntelligence``
    market report on each — no human query required. Reports are published to
    the ``aegis:sentinel:reports`` Redis stream. Best-effort — never raises.
    """
    log.info("scheduler.sentinel.start")
    try:
        import asyncpg
        import redis.asyncio as redis_lib

        from aegis.config import settings as _settings
        from aegis.scheduler.sentinel import MarketSentinel

        cfg = _settings()
        pool = await asyncpg.create_pool(cfg.pg_dsn_str, min_size=1, max_size=2)
        redis = redis_lib.from_url(cfg.redis_url_str, decode_responses=True)
        try:
            scan = await MarketSentinel(pool=pool, redis=redis).scan()
            log.info(
                "scheduler.sentinel.done",
                radar_signals=scan.radar_signals,
                breakouts=scan.breakouts,
                reports=len(scan.reports),
            )
        finally:
            await redis.aclose()
            await pool.close()
    except Exception as exc:
        log.warning("scheduler.sentinel.error", error=str(exc)[:200])


async def job_health_report() -> None:
    """Log system health snapshot."""
    try:
        import asyncpg
        import redis.asyncio as redis_lib

        from aegis.config import settings as _settings

        _cfg = _settings()
        pool = await asyncpg.create_pool(
            _cfg.pg_dsn_str,
            min_size=1,
            max_size=2,
            timeout=5,
        )
        signals = await pool.fetchval("SELECT COUNT(*) FROM signals")
        alerts = await pool.fetchval("SELECT COUNT(*) FROM alerts")
        outcomes = await pool.fetchval("SELECT COUNT(*) FROM prediction_outcomes")
        # PROJECT OMEGA Phase A: capital-free self-supervised outcome growth.
        signal_outcomes = await pool.fetchval(
            "SELECT COUNT(*) FROM signal_outcomes "
            "WHERE resolution_status IN ('correct', 'incorrect') "
            "AND window_scraper_alive = TRUE"
        )
        signal_outcomes_24h = await pool.fetchval(
            "SELECT COUNT(*) FROM signal_outcomes "
            "WHERE resolution_status IN ('correct', 'incorrect') "
            "AND window_scraper_alive = TRUE "
            "AND settled_at > NOW() - INTERVAL '24 hours'"
        )
        fresh = await pool.fetchval(
            "SELECT COUNT(*) FROM signals WHERE created_at > NOW() - INTERVAL '1 hour'"
        )
        await pool.close()

        # ORPH/REDIS-1: use the configured Redis URL, never a hardcoded host:port.
        r = await redis_lib.from_url(_cfg.redis_url_str)
        stream_len = await r.xlen("aegis:phase2:graph_results")
        await r.aclose()

        log.info(
            "scheduler.health",
            signals_total=signals,
            signals_1h=fresh,
            alerts_total=alerts,
            outcomes_total=outcomes,
            signal_outcomes_total=signal_outcomes,
            signal_outcomes_24h=signal_outcomes_24h,
            stream_entries=stream_len,
            ts=datetime.now(UTC).isoformat(),
        )
    except Exception as exc:
        log.warning("scheduler.health.error", error=str(exc)[:200])


async def job_datalake_refresh() -> None:
    """ORPH-4: daily Bronze → Silver → Gold refresh (02:00 UTC).

    Direct fallback for environments without a Prefect server — the same
    refresh can instead be served as a Prefect cron deployment via
    ``aegis datalake schedule``. Running both is harmless: Bronze writes are
    content-addressed (idempotent batch_id), so a double run produces no
    duplicates.
    """
    log.info("scheduler.datalake.start")
    try:
        from aegis.datalake.orchestration.flows import daily_lake_refresh

        result = await daily_lake_refresh()
        log.info(
            "scheduler.datalake.done",
            date=result.get("date"),
            bronze=result.get("bronze", {}),
        )
    except Exception as exc:
        log.warning("scheduler.datalake.error", error=str(exc)[:200])


async def job_weekly_retrain() -> None:
    """Trigger model retraining pipeline (Sunday 2am UTC)."""
    log.info("scheduler.retrain.start")
    try:
        import asyncpg

        from aegis.config import settings as _settings
        from aegis.evolve import RetrainingPipeline

        pool = await asyncpg.create_pool(
            _settings().pg_dsn_str,
            min_size=1,
            max_size=2,
        )
        pipeline = RetrainingPipeline(db_pool=pool)
        result = await pipeline.run_weekly_retrain()
        log.info("scheduler.retrain.done", status=getattr(result, "status", "unknown"))
        await pool.close()
    except Exception as exc:
        log.warning("scheduler.retrain.error", error=str(exc)[:200])


async def job_shadow_evaluate() -> None:
    """PASS3-3C: promote/retire shadow models past their 72h window (every 6h).

    Delegates to ``RetrainingPipeline.evaluate_shadows()`` — shadows whose
    test AUC still beats the champion by the promotion threshold become the
    new champion; the rest are retired. Never raises.
    """
    log.info("scheduler.shadow.start")
    try:
        import asyncpg

        from aegis.config import settings as _settings
        from aegis.evolve import RetrainingPipeline

        pool = await asyncpg.create_pool(
            _settings().pg_dsn_str,
            min_size=1,
            max_size=2,
        )
        try:
            pipeline = RetrainingPipeline(db_pool=pool)
            results = await pipeline.evaluate_shadows()
            promoted = sum(1 for r in results if r.get("promoted"))
            log.info(
                "scheduler.shadow.done",
                evaluated=len(results),
                promoted=promoted,
            )
        finally:
            await pool.close()
    except Exception as exc:
        log.warning("scheduler.shadow.error", error=str(exc)[:200])


async def job_threshold_update() -> None:
    """PASS2-2C: weekly adaptive-threshold update (Sunday 3 AM UTC).

    Reads the last 30 days of prediction_outcomes and nudges the confidence
    gate, velocity slope, and compliance block threshold by ±0.02 toward the
    realized-precision target. No-ops gracefully on insufficient data.
    """
    log.info("scheduler.thresholds.start")
    try:
        import asyncpg
        import redis.asyncio as redis_lib

        from aegis.config import settings as _settings
        from aegis.core.dynamic_thresholds import DynamicThresholds

        cfg = _settings()
        pool = await asyncpg.create_pool(cfg.pg_dsn_str, min_size=1, max_size=2)
        redis = redis_lib.from_url(cfg.redis_url_str, decode_responses=True)
        try:
            thresholds = DynamicThresholds(redis=redis)
            state = await thresholds.update_from_outcomes(
                pool, str(cfg.default_tenant_id)
            )
            if state is None:
                log.info("scheduler.thresholds.skipped", reason="insufficient_data")
            else:
                log.info(
                    "scheduler.thresholds.done",
                    confidence_gate=state.confidence_gate,
                    velocity_slope=state.velocity_slope,
                    comply_block=state.comply_block,
                    precision=state.precision,
                )
        finally:
            await redis.aclose()
            await pool.close()
    except Exception as exc:
        log.warning("scheduler.thresholds.error", error=str(exc)[:200])


async def job_weight_update() -> None:
    """PASS2-2D: nightly per-agent accuracy weight refresh (3:30 AM UTC).

    Joins recent Phase 2 graph results (per-agent votes from the Redis
    stream) against settled prediction_outcomes, computes each agent's
    realized accuracy, and writes ``weight = 0.5 + accuracy`` to the Redis
    hash ``aegis:agents:accuracy_weights`` (TTL 7 days) consumed by the
    supervisor's weighted ensemble.
    """
    log.info("scheduler.weights.start")
    try:
        import asyncpg
        import redis.asyncio as redis_lib

        from aegis.agents.supervisor import compute_accuracy_weights
        from aegis.config import settings as _settings

        cfg = _settings()
        pool = await asyncpg.create_pool(cfg.pg_dsn_str, min_size=1, max_size=2)
        redis = redis_lib.from_url(cfg.redis_url_str, decode_responses=True)
        try:
            weights = await compute_accuracy_weights(
                redis, pool, str(cfg.default_tenant_id)
            )
            if not weights:
                log.info("scheduler.weights.skipped", reason="no_outcome_overlap")
            else:
                log.info("scheduler.weights.done", agents=len(weights))
        finally:
            await redis.aclose()
            await pool.close()
    except Exception as exc:
        log.warning("scheduler.weights.error", error=str(exc)[:200])


async def job_health_check() -> None:
    """PASS5-5A: 5-minute self-healing health check.

    Runs ``AegisHealthChecker.check_and_heal()`` (stream staleness,
    adapter quarantine ratio, model staleness, killswitch stuck, stale
    thresholds, clock drift, evolution idle) and publishes the report to
    the ``aegis:core:health`` stream (maxlen 288 ≈ 24 h of history).
    Never raises.
    """
    log.info("scheduler.health_check.start")
    try:
        import json

        import asyncpg
        import redis.asyncio as redis_lib

        from aegis.config import settings as _settings
        from aegis.scheduler.health_checker import (
            HEALTH_STREAM,
            HEALTH_STREAM_MAXLEN,
            AegisHealthChecker,
        )

        cfg = _settings()
        pool = await asyncpg.create_pool(cfg.pg_dsn_str, min_size=1, max_size=2)
        redis = redis_lib.from_url(cfg.redis_url_str, decode_responses=True)
        try:
            checker = AegisHealthChecker(
                redis=redis,
                pool=pool,
                tenant_id=str(cfg.default_tenant_id),
            )
            report = await checker.check_and_heal()
            await redis.xadd(
                HEALTH_STREAM,
                {"body": json.dumps(report.to_dict())},
                maxlen=HEALTH_STREAM_MAXLEN,
                approximate=True,
            )
            log.info(
                "scheduler.health_check.done",
                overall=report.overall_status,
                healing_actions=report.healing_actions,
            )
        finally:
            await redis.aclose()
            await pool.close()
    except Exception as exc:
        log.warning("scheduler.health_check.error", error=str(exc)[:200])


_stop_event: asyncio.Event | None = None


def _handle_shutdown(scheduler: AsyncIOScheduler) -> None:
    import contextlib
    with contextlib.suppress(Exception):
        scheduler.shutdown(wait=False)
    log.info("scheduler.shutdown")
    if _stop_event is not None:
        _stop_event.set()


async def main() -> None:
    global _stop_event  # noqa: PLW0603
    if not _SCHED_AVAILABLE:
        sys.stderr.write("APScheduler not installed. Run: uv pip install apscheduler\n")
        sys.exit(1)

    _stop_event = asyncio.Event()
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        job_scrape,
        trigger=IntervalTrigger(minutes=15),
        id="scrape",
        name="Scrape topics",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_analyze,
        trigger=IntervalTrigger(hours=1),
        id="analyze",
        name="Agent analysis",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_drift_check,
        trigger=IntervalTrigger(hours=6),
        id="drift",
        name="Drift check",
        max_instances=1,
    )
    scheduler.add_job(
        job_settle_claims,
        trigger=IntervalTrigger(hours=1),
        id="settle_claims",
        name="Settle self-supervised signal claims",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_health_report,
        trigger=IntervalTrigger(minutes=30),
        id="health",
        name="Health report",
    )
    scheduler.add_job(
        job_sentinel,
        trigger=IntervalTrigger(minutes=45),
        id="sentinel",
        name="Autonomous market discovery",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_emit_claims,
        trigger=IntervalTrigger(hours=1),
        id="emit_claims",
        name="Emit calibrated falsifiable claims",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_refit_calibration,
        trigger=CronTrigger(hour=1, minute=30),
        id="refit_calibration",
        name="Daily isotonic calibration refit",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_health_check,
        trigger=IntervalTrigger(minutes=5),
        id="health_check",
        name="Self-healing health check",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_weekly_retrain,
        trigger=CronTrigger(day_of_week="sun", hour=2, minute=0),
        id="retrain",
        name="Weekly retrain",
        max_instances=1,
    )
    scheduler.add_job(
        job_datalake_refresh,
        trigger=CronTrigger(hour=2, minute=0),
        id="datalake",
        name="Daily lake refresh",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_shadow_evaluate,
        trigger=IntervalTrigger(hours=6),
        id="shadow",
        name="Shadow model evaluation",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_threshold_update,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="thresholds",
        name="Weekly threshold adaptation",
        max_instances=1,
    )
    scheduler.add_job(
        job_weight_update,
        trigger=CronTrigger(hour=3, minute=30),
        id="weights",
        name="Nightly agent weight update",
        max_instances=1,
    )
    scheduler.add_job(
        job_knowledge_refresh,
        trigger=CronTrigger(day_of_week="sun", hour=4, minute=0),
        id="knowledge",
        name="Weekly knowledge refresh + self-audit (Phase C)",
        max_instances=1,
    )

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: _handle_shutdown(scheduler))

    # STAGE 1.4 composition root: install the process-global shared pool BEFORE
    # any job runs. Three shipped safety mechanisms (Phase-3 prediction
    # persistence, confidence calibration, the model-skill ENTER gate) read
    # get_shared_pool() and sat inert for the project's entire history because
    # no production process ever called set_shared_pool(). A missing pool here
    # is a startup FAILURE — the process dies loudly, it does not shrug.
    from aegis.config import settings as _settings
    from aegis.db.pool import PgPool, set_shared_pool

    try:
        _shared = PgPool(dsn=_settings().pg_dsn_str)
        await _shared.connect()
        set_shared_pool(_shared)
        log.info("scheduler.shared_pool_installed")
    except Exception:
        log.error("scheduler.shared_pool_install_failed_fatal")
        raise

    scheduler.start()
    log.info("scheduler.started", job_count=len(scheduler.get_jobs()))

    sys.stdout.write("\nAEGIS Pulse Autonomous Scheduler running.\n")
    sys.stdout.write("Jobs scheduled:\n")
    for job in scheduler.get_jobs():
        sys.stdout.write(f"  {job.name}: {job.trigger}\n")
    sys.stdout.write("\nRunning immediate health report...\n")
    sys.stdout.flush()

    await job_health_report()

    try:
        await _stop_event.wait()
    finally:
        set_shared_pool(None)
        with contextlib.suppress(Exception):
            await _shared.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
