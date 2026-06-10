"""
AEGIS Pulse — Autonomous Scheduler.
Runs the system without human intervention.

Schedule:
  Every 15 min:  scrape trending topics → write to DB
  Every 1 hour:  run agent analysis on fresh signals
  Every 6 hours: drift check
  Every 30 min:  health report to log
  Every Sunday 2am UTC: trigger model retraining

Run standalone: uv run python src/aegis/scheduler/autonomous.py
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import UTC, datetime

import structlog

log = structlog.get_logger("aegis.scheduler.autonomous")

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    _SCHED_AVAILABLE = True
except ImportError:
    _SCHED_AVAILABLE = False

TOPICS = [
    "dropshipping",
    "print on demand",
    "trending gadgets",
    "ecommerce arbitrage",
    "viral products",
    "amazon trending",
]


async def job_scrape() -> None:
    """Scrape 3 topics per cycle to stay within rate limits."""
    log.info("scheduler.scrape.start")
    total = 0
    try:
        from aegis.scrape.topic import scrape_topic

        for topic in TOPICS[:3]:
            try:
                result = await scrape_topic(topic, limit_per_source=20)
                total += result.total_unique
                log.info("scheduler.scrape.topic", topic=topic, signals=result.total_unique)
            except Exception as exc:
                log.warning("scheduler.scrape.topic_error", topic=topic, error=str(exc)[:100])
        log.info("scheduler.scrape.done", total=total)
    except Exception as exc:
        log.error("scheduler.scrape.fatal", error=str(exc)[:200])


async def job_analyze() -> None:
    """Run agent analysis on recent signals."""
    log.info("scheduler.analyze.start")
    try:
        proc = await asyncio.create_subprocess_exec(
            "uv",
            "run",
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
        fresh = await pool.fetchval(
            "SELECT COUNT(*) FROM signals WHERE created_at > NOW() - INTERVAL '1 hour'"
        )
        await pool.close()

        r = await redis_lib.from_url("redis://localhost:6380/0")
        stream_len = await r.xlen("aegis:phase2:graph_results")
        await r.aclose()

        log.info(
            "scheduler.health",
            signals_total=signals,
            signals_1h=fresh,
            alerts_total=alerts,
            outcomes_total=outcomes,
            stream_entries=stream_len,
            ts=datetime.now(UTC).isoformat(),
        )
    except Exception as exc:
        log.warning("scheduler.health.error", error=str(exc)[:200])


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
        pipeline = RetrainingPipeline(pool=pool)
        result = await pipeline.run_weekly_retrain()
        log.info("scheduler.retrain.done", status=getattr(result, "status", "unknown"))
        await pool.close()
    except Exception as exc:
        log.warning("scheduler.retrain.error", error=str(exc)[:200])


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
        job_health_report,
        trigger=IntervalTrigger(minutes=30),
        id="health",
        name="Health report",
    )
    scheduler.add_job(
        job_weekly_retrain,
        trigger=CronTrigger(day_of_week="sun", hour=2, minute=0),
        id="retrain",
        name="Weekly retrain",
        max_instances=1,
    )

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: _handle_shutdown(scheduler))

    scheduler.start()
    log.info("scheduler.started", job_count=len(scheduler.get_jobs()))

    sys.stdout.write("\nAEGIS Pulse Autonomous Scheduler running.\n")
    sys.stdout.write("Jobs scheduled:\n")
    for job in scheduler.get_jobs():
        sys.stdout.write(f"  {job.name}: {job.trigger}\n")
    sys.stdout.write("\nRunning immediate health report...\n")
    sys.stdout.flush()

    await job_health_report()

    await _stop_event.wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
