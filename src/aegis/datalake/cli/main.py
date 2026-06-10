"""Phase 10 CLI.

Commands::

    aegis datalake doctor                      health check
    aegis datalake migrate                     apply pending catalog migrations
    aegis datalake list-tables [--layer L]     list registered tables
    aegis datalake list-partitions TABLE       list partitions for a table
    aegis datalake ingest-postgres-signals     pull Phase 1 signals into Bronze
    aegis datalake ingest-postgres-predictions pull Phase 3 predictions
    aegis datalake ingest-postgres-alerts      pull Phase 4 alerts
    aegis datalake ingest-redis                drain Phase 2 result stream once
    aegis datalake build-silver [--date D]     run Silver builders for a date
    aegis datalake build-gold [--date D]       run Gold aggregators for a date
    aegis datalake daily [--date D]            full Bronze→Silver→Gold for date
    aegis datalake query 'SELECT ...'          run a read-only SQL query
    aegis datalake retention LAYER             plan retention; --apply to delete

Each command supports ``--json-out`` for machine-readable output and
``--no-color`` to suppress structlog colour codes.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

import click

from .. import VERSION
from .._logging import get_logger
from ..constants import (
    BRONZE,
    GOLD,
    SILVER,
    VALID_LAYERS,
)
from ..facade import DataLake
from ..migrations import current_version, run_migrations, target_version
from ..retention import RetentionEnforcer
from ..settings import DataLakeSettings

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _emit(payload: dict[str, Any], *, json_out: bool) -> None:
    """Emit a result as JSON or as a human-readable table."""
    if json_out:
        click.echo(json.dumps(payload, default=str, indent=2))
        return
    # tiny pretty-printer
    for k, v in payload.items():
        click.echo(f"{k}: {v}")


def _settings_from_ctx(ctx: click.Context) -> DataLakeSettings:
    """Build a :class:`DataLakeSettings` from CLI flags + env."""
    obj: dict[str, Any] = ctx.obj or {}
    overrides: dict[str, Any] = {}
    if obj.get("local_root"):
        overrides["local_root"] = obj["local_root"]
        overrides["use_local_filesystem"] = True
    if obj.get("catalog_db_path"):
        overrides["catalog_db_path"] = obj["catalog_db_path"]
    if obj.get("tenant_id"):
        overrides["tenant_id"] = obj["tenant_id"]
    return DataLakeSettings(**overrides) if overrides else DataLakeSettings()


def _parse_date(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).date().isoformat()
    # support "today" / "yesterday" sugar
    if value.lower() == "today":
        return datetime.now(UTC).date().isoformat()
    if value.lower() == "yesterday":
        return (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    # ISO date
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise click.BadParameter(
            f"date must be YYYY-MM-DD or 'today'/'yesterday'; got {value!r}"
        ) from exc


# --------------------------------------------------------------------------- #
# Top-level group
# --------------------------------------------------------------------------- #


@click.group(help="AEGIS Pulse Phase 10 — Data Lake & Analytics CLI.")
@click.option(
    "--local-root",
    envvar="AEGIS_DATALAKE_LOCAL_ROOT",
    default=None,
    help="Use local filesystem under this root (sets use_local_filesystem=True).",
)
@click.option(
    "--catalog-db-path",
    envvar="AEGIS_DATALAKE_CATALOG_DB_PATH",
    default=None,
    help="Path to the SQLite catalog DB.",
)
@click.option(
    "--tenant-id",
    envvar="AEGIS_DATALAKE_TENANT_ID",
    default=None,
    help="Tenant UUID to scope all operations.",
)
@click.version_option(version=VERSION, prog_name="aegis-datalake")
@click.pass_context
def cli(
    ctx: click.Context,
    local_root: str | None,
    catalog_db_path: str | None,
    tenant_id: str | None,
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["local_root"] = local_root
    ctx.obj["catalog_db_path"] = catalog_db_path
    ctx.obj["tenant_id"] = tenant_id


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


@cli.command(help="Run a quick health check on storage + catalog.")
@click.option("--json-out", is_flag=True)
@click.pass_context
def doctor(ctx: click.Context, json_out: bool) -> None:
    settings = _settings_from_ctx(ctx)
    with DataLake.session(settings) as lake:
        h = lake.health()
        payload = {
            "phase": "phase10",
            "version": VERSION,
            "backend_ok": h.backend_ok,
            "catalog_ok": h.catalog_ok,
            "tables_registered": h.tables_registered,
            "storage_kind": h.storage_kind,
            "bucket": h.bucket,
            "tenant_id": settings.tenant_id,
            "catalog_schema_version": current_version(lake.catalog),
            "target_schema_version": target_version(),
        }
    _emit(payload, json_out=json_out)
    if not (h.backend_ok and h.catalog_ok):
        sys.exit(1)


# --------------------------------------------------------------------------- #
# migrate
# --------------------------------------------------------------------------- #


@cli.command(help="Apply pending catalog schema migrations.")
@click.option("--json-out", is_flag=True)
@click.pass_context
def migrate(ctx: click.Context, json_out: bool) -> None:
    settings = _settings_from_ctx(ctx)
    with DataLake.session(settings) as lake:
        applied = run_migrations(lake.catalog)
        payload = {
            "current_version": current_version(lake.catalog),
            "target_version": target_version(),
            "applied": applied,
        }
    _emit(payload, json_out=json_out)


# --------------------------------------------------------------------------- #
# list-tables / list-partitions
# --------------------------------------------------------------------------- #


@cli.command("list-tables", help="List catalog-registered tables.")
@click.option("--layer", type=click.Choice(sorted(VALID_LAYERS)), default=None)
@click.option("--json-out", is_flag=True)
@click.pass_context
def list_tables_cmd(
    ctx: click.Context, layer: str | None, json_out: bool
) -> None:
    settings = _settings_from_ctx(ctx)
    with DataLake.session(settings) as lake:
        tables = lake.list_tables(layer=layer)
    if json_out:
        click.echo(json.dumps(tables, default=str, indent=2))
        return
    if not tables:
        click.echo("(no tables registered)")
        return
    for t in tables:
        click.echo(
            f"{t['layer']:7s}  {t['name']:30s}  partition_keys={t['partition_keys']}"
        )


@cli.command("list-partitions", help="List partitions for a table.")
@click.argument("table_name")
@click.option("--layer", type=click.Choice(sorted(VALID_LAYERS)), required=True)
@click.option("--json-out", is_flag=True)
@click.pass_context
def list_partitions_cmd(
    ctx: click.Context, table_name: str, layer: str, json_out: bool
) -> None:
    settings = _settings_from_ctx(ctx)
    with DataLake.session(settings) as lake:
        parts = lake.catalog.list_partitions(table_name=table_name, layer=layer)
    payload = [
        {
            "table": p.table_name,
            "layer": p.layer,
            "partition_key": p.partition_key,
            "tenant_id": p.tenant_id,
            "rows": p.row_count,
            "bytes": p.byte_size,
            "written_at": p.written_at.isoformat(),
        }
        for p in parts
    ]
    if json_out:
        click.echo(json.dumps(payload, default=str, indent=2))
        return
    if not payload:
        click.echo("(no partitions)")
        return
    for p in payload:
        click.echo(
            f"{p['partition_key']:18s}  rows={p['rows']:>8d}  bytes={p['bytes']:>10d}  written={p['written_at']}"
        )


# --------------------------------------------------------------------------- #
# build-silver / build-gold / daily
# --------------------------------------------------------------------------- #


@cli.command("build-silver", help="Run Silver builders for a given UTC date.")
@click.option("--date", "date_iso", default=None, help="YYYY-MM-DD or today/yesterday.")
@click.option("--json-out", is_flag=True)
@click.pass_context
def build_silver_cmd(
    ctx: click.Context, date_iso: str | None, json_out: bool
) -> None:
    settings = _settings_from_ctx(ctx)
    d = _parse_date(date_iso)
    with DataLake.session(settings) as lake:
        out = lake.build_silver(d)
    _emit({"date": d, "results": out}, json_out=json_out)


@cli.command("build-gold", help="Run Gold aggregators for a given UTC date.")
@click.option("--date", "date_iso", default=None)
@click.option("--json-out", is_flag=True)
@click.pass_context
def build_gold_cmd(
    ctx: click.Context, date_iso: str | None, json_out: bool
) -> None:
    settings = _settings_from_ctx(ctx)
    d = _parse_date(date_iso)
    with DataLake.session(settings) as lake:
        out = lake.build_gold(d)
    _emit({"date": d, "results": out}, json_out=json_out)


# --------------------------------------------------------------------------- #
# Ingest commands (need real Phase 1/3/4 services; safe to expose anyway)
# --------------------------------------------------------------------------- #


async def _build_pool(dsn: str) -> Any:
    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover
        raise click.ClickException(
            "asyncpg is not installed — `pip install asyncpg`"
        ) from exc
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)


def _parse_since_until(
    since: str | None, until: str | None
) -> tuple[datetime | None, datetime | None]:
    def _p(v: str | None) -> datetime | None:
        if v is None:
            return None
        try:
            dt = datetime.fromisoformat(v)
        except ValueError as exc:
            raise click.BadParameter(f"invalid ISO timestamp {v!r}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt

    return _p(since), _p(until)


@cli.command("ingest-postgres-signals", help="Pull Phase 1 signals into Bronze.")
@click.option("--since", default=None, help="ISO timestamp (default: 7d ago).")
@click.option("--until", default=None, help="ISO timestamp (default: now).")
@click.option("--dsn", envvar="AEGIS_DATALAKE_POSTGRES_DSN", required=True)
@click.option("--json-out", is_flag=True)
@click.pass_context
def ingest_signals_cmd(
    ctx: click.Context,
    since: str | None,
    until: str | None,
    dsn: str,
    json_out: bool,
) -> None:
    settings = _settings_from_ctx(ctx)
    s, u = _parse_since_until(since, until)

    async def _run() -> dict[str, Any]:
        from ..bronze.ingest_postgres import PostgresSignalsIngester

        pool = await _build_pool(dsn)
        try:
            with DataLake.session(settings) as lake:
                ing = PostgresSignalsIngester(
                    writer=lake.bronze, tenant_id=settings.tenant_id
                )
                stats = await ing.ingest(pool=pool, since=s, until=u)
                return {
                    "rows_read": stats.rows_read,
                    "rows_written": stats.rows_written,
                    "partitions_written": stats.partitions_written,
                }
        finally:
            await pool.close()

    out = asyncio.run(_run())
    _emit(out, json_out=json_out)


@cli.command(
    "ingest-postgres-predictions",
    help="Pull Phase 3 predictions into Bronze.",
)
@click.option("--since", default=None)
@click.option("--until", default=None)
@click.option("--dsn", envvar="AEGIS_DATALAKE_POSTGRES_DSN", required=True)
@click.option("--json-out", is_flag=True)
@click.pass_context
def ingest_predictions_cmd(
    ctx: click.Context,
    since: str | None,
    until: str | None,
    dsn: str,
    json_out: bool,
) -> None:
    settings = _settings_from_ctx(ctx)
    s, u = _parse_since_until(since, until)

    async def _run() -> dict[str, Any]:
        from ..bronze.ingest_predictions import PostgresPredictionsIngester

        pool = await _build_pool(dsn)
        try:
            with DataLake.session(settings) as lake:
                ing = PostgresPredictionsIngester(
                    writer=lake.bronze, tenant_id=settings.tenant_id
                )
                stats = await ing.ingest(pool=pool, since=s, until=u)
                return {
                    "rows_read": stats.rows_read,
                    "rows_written": stats.rows_written,
                    "partitions_written": stats.partitions_written,
                }
        finally:
            await pool.close()

    out = asyncio.run(_run())
    _emit(out, json_out=json_out)


@cli.command("ingest-postgres-alerts", help="Pull Phase 4 alerts into Bronze.")
@click.option("--since", default=None)
@click.option("--until", default=None)
@click.option("--dsn", envvar="AEGIS_DATALAKE_POSTGRES_DSN", required=True)
@click.option("--json-out", is_flag=True)
@click.pass_context
def ingest_alerts_cmd(
    ctx: click.Context,
    since: str | None,
    until: str | None,
    dsn: str,
    json_out: bool,
) -> None:
    settings = _settings_from_ctx(ctx)
    s, u = _parse_since_until(since, until)

    async def _run() -> dict[str, Any]:
        from ..bronze.ingest_alerts import PostgresAlertsIngester

        pool = await _build_pool(dsn)
        try:
            with DataLake.session(settings) as lake:
                ing = PostgresAlertsIngester(
                    writer=lake.bronze, tenant_id=settings.tenant_id
                )
                stats = await ing.ingest(pool=pool, since=s, until=u)
                return {
                    "rows_read": stats.rows_read,
                    "rows_written": stats.rows_written,
                    "partitions_written": stats.partitions_written,
                }
        finally:
            await pool.close()

    out = asyncio.run(_run())
    _emit(out, json_out=json_out)


@cli.command("ingest-redis", help="Drain Phase 2 Redis stream once into Bronze.")
@click.option(
    "--redis-url",
    envvar="AEGIS_DATALAKE_REDIS_URL",
    default="redis://localhost:6379/0",
)
@click.option(
    "--stream-key",
    envvar="AEGIS_DATALAKE_PHASE2_STREAM_KEY",
    default="aegis:phase2:graph_results",
)
@click.option("--consumer-name", default="cli-1")
@click.option("--max-iterations", default=10, type=int)
@click.option("--json-out", is_flag=True)
@click.pass_context
def ingest_redis_cmd(
    ctx: click.Context,
    redis_url: str,
    stream_key: str,
    consumer_name: str,
    max_iterations: int,
    json_out: bool,
) -> None:
    settings = _settings_from_ctx(ctx)

    async def _run() -> dict[str, Any]:
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:  # pragma: no cover
            raise click.ClickException("redis is not installed") from exc

        from ..bronze.ingest_redis import RedisStreamIngester

        client = aioredis.from_url(redis_url)
        try:
            with DataLake.session(settings) as lake:
                ing = RedisStreamIngester(
                    writer=lake.bronze,
                    stream_key=stream_key,
                    consumer_name=consumer_name,
                    tenant_id=settings.tenant_id,
                )
                stats = await ing.ingest_once(
                    redis=client, max_iterations=max_iterations
                )
                return {
                    "entries_read": stats.entries_read,
                    "entries_written": stats.entries_written,
                    "partitions_written": stats.partitions_written,
                    "last_message_id": stats.last_message_id,
                }
        finally:
            await client.aclose()

    out = asyncio.run(_run())
    _emit(out, json_out=json_out)


# --------------------------------------------------------------------------- #
# query / retention / daily
# --------------------------------------------------------------------------- #


@cli.command(help="Run a read-only SQL query (auto-registers all tables).")
@click.argument("sql")
@click.option("--timeout", "timeout_s", default=None, type=float)
@click.option("--limit", default=100, type=int, help="Cap printed rows.")
@click.option("--json-out", is_flag=True)
@click.pass_context
def query(
    ctx: click.Context,
    sql: str,
    timeout_s: float | None,
    limit: int,
    json_out: bool,
) -> None:
    settings = _settings_from_ctx(ctx)
    with DataLake.session(settings) as lake:
        result = lake.query(sql, timeout_s=timeout_s)
        rows = result.to_dicts()[:limit]
        payload = {
            "columns": list(result.columns),
            "rowcount": result.rowcount,
            "duration_ms": result.duration_ms,
            "rows": rows,
        }
    if json_out:
        click.echo(json.dumps(payload, default=str, indent=2))
        return
    click.echo(
        f"({result.rowcount} rows, {result.duration_ms:.1f} ms; showing up to {limit})"
    )
    if not rows:
        return
    cols = list(result.columns)
    click.echo("  ".join(cols))
    click.echo("  ".join("-" * max(3, len(c)) for c in cols))
    for r in rows:
        click.echo("  ".join(str(r.get(c, "")) for c in cols))


@cli.command(help="Plan (and optionally apply) retention deletion for a layer.")
@click.argument("layer", type=click.Choice([BRONZE, SILVER, GOLD]))
@click.option("--bronze-retention-days", default=None, type=int)
@click.option("--apply", "apply_flag", is_flag=True, help="Actually delete data.")
@click.option("--json-out", is_flag=True)
@click.pass_context
def retention(
    ctx: click.Context,
    layer: str,
    bronze_retention_days: int | None,
    apply_flag: bool,
    json_out: bool,
) -> None:
    settings = _settings_from_ctx(ctx)
    with DataLake.session(settings) as lake:
        enforcer = RetentionEnforcer(
            settings=settings, backend=lake.backend, catalog=lake.catalog
        )
        plan = enforcer.plan(layer, bronze_retention_days=bronze_retention_days)
        if apply_flag:
            result = enforcer.apply(plan, dry_run=False)
        else:
            result = enforcer.apply(plan, dry_run=True)
    payload = {
        "layer": result.layer,
        "cutoff_date": result.cutoff_date,
        "partitions_planned": plan.total_partitions,
        "partitions_deleted": result.partitions_deleted,
        "objects_deleted": result.objects_deleted,
        "bytes_deleted": result.bytes_deleted,
        "applied": apply_flag,
    }
    _emit(payload, json_out=json_out)


@cli.command(
    help="Daily: Bronze (Postgres) → Silver → Gold for one UTC date.",
)
@click.option("--date", "date_iso", default=None)
@click.option(
    "--dsn",
    envvar="AEGIS_DATALAKE_POSTGRES_DSN",
    default=None,
    help="If unset, skips Postgres ingest and only runs Silver/Gold.",
)
@click.option("--json-out", is_flag=True)
@click.pass_context
def daily(
    ctx: click.Context, date_iso: str | None, dsn: str | None, json_out: bool
) -> None:
    settings = _settings_from_ctx(ctx)
    d = _parse_date(date_iso)
    payload: dict[str, Any] = {"date": d}

    async def _do_bronze() -> dict[str, Any]:
        from dataclasses import asdict

        from ..bronze.ingest_alerts import PostgresAlertsIngester
        from ..bronze.ingest_postgres import PostgresSignalsIngester
        from ..bronze.ingest_predictions import PostgresPredictionsIngester

        pool = await _build_pool(dsn)  # type: ignore[arg-type]
        try:
            with DataLake.session(settings) as lake:
                out: dict[str, Any] = {}
                s_ing = PostgresSignalsIngester(
                    writer=lake.bronze, tenant_id=settings.tenant_id
                )
                p_ing = PostgresPredictionsIngester(
                    writer=lake.bronze, tenant_id=settings.tenant_id
                )
                a_ing = PostgresAlertsIngester(
                    writer=lake.bronze, tenant_id=settings.tenant_id
                )
                out["signals"] = asdict(await s_ing.ingest(pool=pool))
                out["predictions"] = asdict(await p_ing.ingest(pool=pool))
                out["alerts"] = asdict(await a_ing.ingest(pool=pool))
                return out
        finally:
            await pool.close()

    if dsn:
        payload["bronze"] = asyncio.run(_do_bronze())
    else:
        payload["bronze"] = {"skipped": True, "reason": "no --dsn provided"}

    with DataLake.session(settings) as lake:
        payload["silver"] = lake.build_silver(d)
        payload["gold"] = lake.build_gold(d)

    _emit(payload, json_out=json_out)


__all__ = ["cli"]
