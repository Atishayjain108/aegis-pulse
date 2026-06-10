"""
aegis.dr.cli
============
Click CLI for Phase 15 — Disaster Recovery.

Commands
--------
aegis dr backup  [--target postgres|redis|models|restic|all]
aegis dr restore [--target postgres] [--dry-run]
aegis dr drill   [--dry-run]
aegis dr status
aegis dr runbook [failure-mode] [--dry-run]
aegis dr health  [--watch]

Integration
-----------
Registers under the main ``aegis`` CLI group (``aegis.cli``) by
exporting ``dr_group`` — the parent CLI just does:

    from aegis.dr.cli import dr_group
    cli.add_command(dr_group, name="dr")

All MinIO / Redis connections are created fresh per invocation using
the settings singleton so the CLI works standalone.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import click
import structlog

from aegis.dr.config import get_dr_settings
from aegis.dr.runbooks import FAILURE_MODES

_log = structlog.get_logger("aegis.dr.cli")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minio_client(settings: Any) -> Any:
    """Build a minio.Minio client from DR settings."""
    try:
        from minio import Minio  # type: ignore[import-untyped]

        return Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
        )
    except ImportError:
        click.echo(
            "ERROR: minio package not installed. "
            "Run: uv pip install 'minio>=7'",
            err=True,
        )
        sys.exit(1)


def _ensure_bucket(minio: Any, bucket: str) -> None:
    """Create DR bucket if it doesn't exist."""
    if not minio.bucket_exists(bucket):
        minio.make_bucket(bucket)
        click.echo(f"Created bucket: {bucket}")


async def _redis_client_async(settings: Any) -> Any:
    """Build an async Redis client from DR settings."""
    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        return await aioredis.from_url(settings.redis_url, decode_responses=False)
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group(name="dr")
def dr_group() -> None:
    """Phase 15 — Disaster Recovery: backup, restore, drill, runbooks."""


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


@dr_group.command("backup")
@click.option(
    "--target",
    type=click.Choice(["postgres", "redis", "models", "restic", "all"]),
    default="all",
    show_default=True,
    help="Which target to back up.",
)
@click.option("--dry-run", is_flag=True, help="Skip actual upload; verify only.")
def cmd_backup(target: str, dry_run: bool) -> None:
    """Run a backup job for the specified target(s)."""

    async def _run() -> None:
        settings = get_dr_settings()
        minio = _minio_client(settings)
        _ensure_bucket(minio, settings.dr_bucket)
        redis = await _redis_client_async(settings)

        targets_to_run = (
            ["postgres", "redis", "models", "restic"] if target == "all" else [target]
        )

        for t in targets_to_run:
            click.echo(f"▶ Backing up: {t} ({'dry-run' if dry_run else 'live'}) …")
            try:
                manifest = await _run_single_backup(t, settings, minio, redis)
                status = manifest.status.value if manifest else "skipped"
                size_mb = (manifest.size_bytes / 1e6) if manifest else 0
                click.echo(
                    f"  ✓ {t}: status={status}, "
                    f"size={size_mb:.2f} MB, "
                    f"path={manifest.minio_path if manifest else 'n/a'}"
                )
            except Exception as exc:
                click.echo(f"  ✗ {t}: FAILED — {exc}", err=True)

        if redis:
            await redis.aclose()

    asyncio.run(_run())


async def _run_single_backup(
    target: str, settings: Any, minio: Any, redis: Any
) -> Any:
    if target == "postgres":
        from aegis.dr.backup.postgres import PostgresBackup

        return await PostgresBackup(settings=settings, minio_client=minio).run()
    if target == "redis":
        if redis is None:
            click.echo("  ⚠ Redis client unavailable — skipping", err=True)
            return None
        from aegis.dr.backup.redis import RedisBackup

        return await RedisBackup(
            settings=settings, minio_client=minio, redis_client=redis
        ).run()
    if target == "models":
        from aegis.dr.backup.models import ModelRegistryBackup

        return await ModelRegistryBackup(settings=settings, minio_client=minio).run()
    if target == "restic":
        from aegis.dr.backup.restic import ResticBackup

        return await ResticBackup(settings=settings).run()
    return None


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


@dr_group.command("restore")
@click.option(
    "--target",
    type=click.Choice(["postgres"]),
    default="postgres",
    show_default=True,
    help="Which target to restore.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=True,
    show_default=True,
    help=(
        "Download + checksum-verify only; skip actual DB restore. "
        "Pass --no-dry-run for a real restore."
    ),
)
@click.option(
    "--target-dsn",
    default=None,
    help=(
        "Override the restore target DSN. "
        "Defaults to drill DSN from settings."
    ),
)
@click.confirmation_option(
    prompt="⚠️  This will overwrite the target database. Are you sure?",
    default=False,
)
def cmd_restore(target: str, dry_run: bool, target_dsn: str | None) -> None:
    """Restore a target from its most recent backup in MinIO."""

    async def _run() -> None:
        settings = get_dr_settings()
        minio = _minio_client(settings)

        click.echo(
            f"▶ Restoring {target} ({'dry-run' if dry_run else '⚠️  LIVE'}) …"
        )

        if target == "postgres":
            from aegis.dr.restore.postgres import PostgresRestore

            engine = PostgresRestore(
                settings=settings,
                minio_client=minio,
                target_dsn=target_dsn,
                dry_run=dry_run,
            )
            result = await engine.run()
            click.echo(
                f"  ✓ Restore complete: "
                f"status={result.status.value}, "
                f"rows={result.rows_restored}, "
                f"verified={result.verification_passed}, "
                f"duration={result.duration_s:.1f}s"
            )
        else:
            click.echo(f"Restore for target '{target}' not yet implemented.", err=True)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# drill
# ---------------------------------------------------------------------------


@dr_group.command("drill")
@click.option(
    "--dry-run",
    is_flag=True,
    default=True,
    show_default=True,
    help="Download + verify only, skip real restore. Pass --no-dry-run for full drill.",
)
def cmd_drill(dry_run: bool) -> None:
    """Run the restore drill (proves backup → restore works end-to-end)."""

    async def _run() -> None:
        settings = get_dr_settings()
        minio = _minio_client(settings)
        _ensure_bucket(minio, settings.dr_bucket)
        redis = await _redis_client_async(settings)

        from aegis.dr.drill import RestoreDrill

        drill = RestoreDrill(
            settings=settings,
            minio_client=minio,
            redis_client=redis,
        )

        click.echo(f"▶ Running restore drill ({'dry-run' if dry_run else '⚠️  LIVE'}) …")
        result = await drill.run(dry_run=dry_run)

        _print_drill_result(result)

        if redis:
            await redis.aclose()

        if not result.passed:
            sys.exit(1)  # Non-zero exit so CI fails on drill failure

    asyncio.run(_run())


def _print_drill_result(result: Any) -> None:
    outcome_sym = "✓" if result.passed else "✗"
    click.echo(
        f"\n  {outcome_sym} Drill outcome: {result.outcome.value.upper()}\n"
        f"     RTO actual: {result.rto_actual_s:.1f}s  "
        f"(target ≤ {result.rto_target_s}s, met={result.rto_met})\n"
        f"     RPO actual: {result.rpo_actual_s:.0f}s  "
        f"(target ≤ {result.rpo_target_s}s, met={result.rpo_met})"
    )
    for r in result.restore_results:
        sym = "✓" if r.verification_passed else "✗"
        click.echo(
            f"     {sym} {r.target.value}: "
            f"status={r.status.value}, "
            f"verified={r.verification_passed}, "
            f"duration={r.duration_s:.1f}s"
        )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@dr_group.command("status")
@click.option("--json-out", is_flag=True, help="Output as JSON.")
@click.option("--watch", is_flag=True, help="Re-poll every 60s (Ctrl+C to stop).")
def cmd_status(json_out: bool, watch: bool) -> None:
    """Show current DR SLA status (backup ages, last drill, alerts)."""

    async def _poll() -> None:
        settings = get_dr_settings()
        minio = _minio_client(settings)

        from aegis.dr.health import DrHealthChecker

        checker = DrHealthChecker(settings=settings, minio_client=minio)

        while True:
            snapshot = await checker.check()

            if json_out:
                import json

                click.echo(json.dumps(snapshot.model_dump(mode="json"), indent=2))
            else:
                _print_snapshot(snapshot, settings)

            if not watch:
                break
            await asyncio.sleep(60)

    asyncio.run(_poll())


def _print_snapshot(snapshot: Any, settings: Any) -> None:
    status_color = {
        "ok": "green",
        "warning": "yellow",
        "critical": "red",
    }
    color = status_color.get(snapshot.overall_status.value, "white")
    click.echo(
        "\n╔═ DR Status ══════════════════════════════╗\n"
        "  Overall:      "
        + click.style(snapshot.overall_status.value.upper(), fg=color, bold=True)
    )
    click.echo(
        f"  Captured at:  {snapshot.captured_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    click.echo(f"\n  Backup ages (RPO target ≤ {settings.rpo_target_s}s):")
    for tgt, age_s in snapshot.last_backup_ages_s.items():
        age_str = "∞" if age_s == float("inf") else f"{age_s:.0f}s"
        ok = age_s <= settings.rpo_target_s
        sym = click.style("✓", fg="green") if ok else click.style("✗", fg="red")
        click.echo(f"    {sym}  {tgt}: {age_str}")

    drill_str = (
        snapshot.last_drill_outcome.value if snapshot.last_drill_outcome else "never"
    )
    click.echo(f"\n  Last drill:   {drill_str}")
    if snapshot.next_drill_due_at:
        click.echo(
            f"  Next drill:   "
            f"{snapshot.next_drill_due_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )

    if snapshot.active_alerts:
        click.echo("\n  ⚠ Active alerts:")
        for alert in snapshot.active_alerts:
            click.echo(f"    • {alert}")

    click.echo("╚══════════════════════════════════════════╝")


# ---------------------------------------------------------------------------
# runbook
# ---------------------------------------------------------------------------


@dr_group.command("runbook")
@click.argument(
    "failure_mode",
    type=click.Choice([m.value for m in FAILURE_MODES]),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=True,
    show_default=True,
    help="Describe steps without executing. Pass --no-dry-run to execute.",
)
def cmd_runbook(failure_mode: str, dry_run: bool) -> None:
    """Execute an automated recovery runbook for a known failure mode."""

    async def _run() -> None:
        settings = get_dr_settings()
        minio = _minio_client(settings)
        redis = await _redis_client_async(settings)

        from aegis.dr.runbooks import (
            FailureModeCategory,
            run_disk_full_recovery,
            run_docker_dead_recovery,
            run_pg_corruption_recovery,
            run_redis_oom_recovery,
        )

        cat = FailureModeCategory(failure_mode)
        click.echo(
            f"▶ Running runbook: {failure_mode} "
            f"({'dry-run' if dry_run else '⚠️  LIVE'}) …"
        )

        if cat == FailureModeCategory.PG_CORRUPTION:
            plan = await run_pg_corruption_recovery(
                settings=settings, minio_client=minio, dry_run=dry_run
            )
        elif cat == FailureModeCategory.REDIS_OOM:
            plan = await run_redis_oom_recovery(redis_client=redis, dry_run=dry_run)
        elif cat == FailureModeCategory.DISK_FULL:
            plan = await run_disk_full_recovery(dry_run=dry_run)
        elif cat == FailureModeCategory.DOCKER_DEAD:
            plan = await run_docker_dead_recovery(dry_run=dry_run)
        else:
            click.echo(f"Runbook for '{failure_mode}' not yet automated.", err=True)
            return

        click.echo("\nRecovery Plan:")
        for i, step in enumerate(plan.steps, 1):
            click.echo(f"  {i}. {step}")

        if plan.requires_human:
            click.echo(
                click.style(
                    f"\n⚠  Human escalation required: {plan.human_escalation_reason}",
                    fg="red",
                    bold=True,
                )
            )

        if redis:
            await redis.aclose()

    asyncio.run(_run())
