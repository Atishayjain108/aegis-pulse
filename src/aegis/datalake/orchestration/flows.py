"""Pipeline flows for the Bronze → Silver → Gold lifecycle.

The flows are written as **plain async functions** that can be:

1. Decorated with ``@prefect.flow`` / ``@prefect.task`` if Prefect 3 is installed
   (``aegis.datalake.orchestration.flows.PREFECT_AVAILABLE`` is ``True``).
2. Invoked directly from the CLI or tests when Prefect is absent.

This dual-path design means **Phase 10 has no hard dependency on Prefect**,
which keeps the import surface minimal for laptop installs while still giving
production users full Prefect observability when they want it.

Each flow returns a typed result dict suitable for logging or JSON-out CLI
modes — never a Prefect-only object.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from typing import Any

from .._logging import get_logger
from ..bronze.ingest_alerts import PostgresAlertsIngester
from ..bronze.ingest_postgres import PostgresSignalsIngester
from ..bronze.ingest_predictions import PostgresPredictionsIngester
from ..bronze.ingest_redis import RedisStreamIngester
from ..facade import DataLake
from ..settings import DataLakeSettings

log = get_logger(__name__)

# --- optional Prefect detection ------------------------------------------- #
try:  # pragma: no cover - import-time branch
    from prefect import flow as _prefect_flow
    from prefect import task as _prefect_task

    PREFECT_AVAILABLE = True
except Exception:  # pragma: no cover
    # Catches ImportError (Prefect not installed) AND pydantic ValidationError
    # that Prefect 3.x raises during settings init on Pydantic >=2.11 due to
    # deprecated model_fields instance access in LoggingToAPISettings.
    PREFECT_AVAILABLE = False

    def _prefect_flow(*args: Any, **kwargs: Any) -> Any:
        # When prefect is absent, return an identity decorator so source code
        # stays clean. Accepts both bare-decorator and parametrised forms.
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        def _wrap(fn: Any) -> Any:
            return fn

        return _wrap

    def _prefect_task(*args: Any, **kwargs: Any) -> Any:
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        def _wrap(fn: Any) -> Any:
            return fn

        return _wrap


# --- helpers --- #


def _as_dict(obj: Any) -> dict[str, Any]:
    """Best-effort conversion of dataclass / dict / object → JSON-able dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    # pydantic model
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return {"value": str(obj)}


def _normalise_date(d: str | date | datetime | None) -> str:
    if d is None:
        return datetime.now(UTC).date().isoformat()
    if isinstance(d, datetime):
        return d.astimezone(UTC).date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


# --- tasks --- #


@_prefect_task(name="bronze.ingest.postgres.signals")
async def _t_ingest_signals(
    lake: DataLake, pool: Any, since: datetime | None, until: datetime | None
) -> dict[str, Any]:
    ingester = PostgresSignalsIngester(writer=lake.bronze, tenant_id=lake.settings.tenant_id)
    stats = await ingester.ingest(pool=pool, since=since, until=until)
    return _as_dict(stats)


@_prefect_task(name="bronze.ingest.postgres.predictions")
async def _t_ingest_predictions(
    lake: DataLake, pool: Any, since: datetime | None, until: datetime | None
) -> dict[str, Any]:
    ingester = PostgresPredictionsIngester(
        writer=lake.bronze, tenant_id=lake.settings.tenant_id
    )
    stats = await ingester.ingest(pool=pool, since=since, until=until)
    return _as_dict(stats)


@_prefect_task(name="bronze.ingest.postgres.alerts")
async def _t_ingest_alerts(
    lake: DataLake, pool: Any, since: datetime | None, until: datetime | None
) -> dict[str, Any]:
    ingester = PostgresAlertsIngester(
        writer=lake.bronze, tenant_id=lake.settings.tenant_id
    )
    stats = await ingester.ingest(pool=pool, since=since, until=until)
    return _as_dict(stats)


@_prefect_task(name="bronze.ingest.redis.stream")
async def _t_ingest_redis(
    lake: DataLake,
    redis_client: Any,
    *,
    stream_key: str,
    consumer_group: str,
    consumer_name: str,
    bronze_table: str,
    max_iterations: int,
) -> dict[str, Any]:
    ingester = RedisStreamIngester(
        writer=lake.bronze,
        stream_key=stream_key,
        consumer_group=consumer_group,
        consumer_name=consumer_name,
        bronze_table=bronze_table,
        tenant_id=lake.settings.tenant_id,
    )
    stats = await ingester.ingest_once(
        redis=redis_client, max_iterations=max_iterations
    )
    return _as_dict(stats)


@_prefect_task(name="silver.build")
def _t_silver_build(lake: DataLake, date_iso: str) -> dict[str, Any]:
    return lake.build_silver(date_iso)


@_prefect_task(name="gold.build")
def _t_gold_build(lake: DataLake, date_iso: str) -> dict[str, Any]:
    return lake.build_gold(date_iso)


# --- flows --- #


@_prefect_flow(name="bronze.ingest.postgres.signals")
async def bronze_ingest_postgres_signals(
    *,
    settings: DataLakeSettings | None = None,
    pool: Any,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Pull Phase 1 signals into Bronze."""
    with DataLake.session(settings) as lake:
        stats = await _t_ingest_signals(lake, pool, since, until)
        log.info("flow.bronze.signals.done", **stats)
        return stats


@_prefect_flow(name="bronze.ingest.all_postgres")
async def bronze_ingest_all_postgres(
    *,
    settings: DataLakeSettings | None = None,
    pool: Any,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Pull signals + predictions + alerts in one flow."""
    with DataLake.session(settings) as lake:
        signals = await _t_ingest_signals(lake, pool, since, until)
        predictions = await _t_ingest_predictions(lake, pool, since, until)
        alerts = await _t_ingest_alerts(lake, pool, since, until)
        out = {"signals": signals, "predictions": predictions, "alerts": alerts}
        log.info(
            "flow.bronze.all_postgres.done",
            signals_rows=signals.get("rows_written", 0),
            predictions_rows=predictions.get("rows_written", 0),
            alerts_rows=alerts.get("rows_written", 0),
        )
        return out


@_prefect_flow(name="silver.build_for_date")
def silver_build_for_date(
    *,
    settings: DataLakeSettings | None = None,
    date_iso: str | None = None,
) -> dict[str, Any]:
    """Run every Silver builder for one UTC date."""
    d = _normalise_date(date_iso)
    with DataLake.session(settings) as lake:
        out = _t_silver_build(lake, d)
        log.info("flow.silver.done", date=d)
        return out


@_prefect_flow(name="gold.build_for_date")
def gold_build_for_date(
    *,
    settings: DataLakeSettings | None = None,
    date_iso: str | None = None,
) -> dict[str, Any]:
    """Run every Gold aggregator for one UTC date."""
    d = _normalise_date(date_iso)
    with DataLake.session(settings) as lake:
        out = _t_gold_build(lake, d)
        log.info("flow.gold.done", date=d)
        return out


@_prefect_flow(name="end_to_end")
async def end_to_end_for_date(
    *,
    settings: DataLakeSettings | None = None,
    pool: Any,
    date_iso: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Bronze (Postgres) → Silver → Gold for a given date.

    This is the "one button" flow used by the CLI ``aegis datalake daily``.
    Redis-stream ingestion is intentionally *not* included — that flow runs
    continuously, not on a per-date schedule.
    """
    d = _normalise_date(date_iso)
    with DataLake.session(settings) as lake:
        # Bronze
        signals = await _t_ingest_signals(lake, pool, since, until)
        predictions = await _t_ingest_predictions(lake, pool, since, until)
        alerts = await _t_ingest_alerts(lake, pool, since, until)
        # Silver
        silver = _t_silver_build(lake, d)
        # Gold
        gold = _t_gold_build(lake, d)
        out: dict[str, Any] = {
            "date": d,
            "bronze": {
                "signals": signals,
                "predictions": predictions,
                "alerts": alerts,
            },
            "silver": silver,
            "gold": gold,
        }
        log.info("flow.end_to_end.done", date=d)
        return out


@_prefect_flow(name="daily_lake_refresh")
async def daily_lake_refresh(*, date_iso: str | None = None) -> dict[str, Any]:
    """ORPH-4: parameterless daily refresh — schedulable as a Prefect cron.

    Unlike :func:`end_to_end_for_date` (which takes an injected pool), this
    flow builds its own asyncpg pool from the environment so it can be served
    as a cron deployment with zero parameters:

        uv run aegis datalake schedule    # serves this flow at 02:00 UTC

    DSN resolution: ``AEGIS_DATALAKE_POSTGRES_DSN`` first, then
    ``AEGIS_PG_DSN``. With no DSN, Bronze ingest is skipped and only
    Silver/Gold build — same contract as ``aegis datalake daily`` without
    ``--dsn``.
    """
    import os

    d = _normalise_date(date_iso)
    dsn = os.environ.get("AEGIS_DATALAKE_POSTGRES_DSN") or os.environ.get("AEGIS_PG_DSN", "")
    out: dict[str, Any] = {"date": d}
    with DataLake.session(None) as lake:
        if dsn:
            import asyncpg

            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
            try:
                out["bronze"] = {
                    "signals": await _t_ingest_signals(lake, pool, None, None),
                    "predictions": await _t_ingest_predictions(lake, pool, None, None),
                    "alerts": await _t_ingest_alerts(lake, pool, None, None),
                }
            finally:
                await pool.close()
        else:
            out["bronze"] = {"skipped": True, "reason": "no DSN in environment"}
        out["silver"] = _t_silver_build(lake, d)
        out["gold"] = _t_gold_build(lake, d)
        log.info("flow.daily_lake_refresh.done", date=d)
    return out


__all__ = [
    "PREFECT_AVAILABLE",
    "bronze_ingest_all_postgres",
    "bronze_ingest_postgres_signals",
    "daily_lake_refresh",
    "end_to_end_for_date",
    "gold_build_for_date",
    "silver_build_for_date",
]
