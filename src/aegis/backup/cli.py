"""
aegis.backup.cli
================

Click CLI for Phase 15 Disaster Recovery operations.

Commands
--------
* ``aegis backup create``      — create pgBackRest or restic snapshot
* ``aegis backup list``        — list available backups / snapshots
* ``aegis backup restore``     — restore from a specific backup
* ``aegis backup prune``       — expire old backups
* ``aegis backup health``      — check staleness of both systems
* ``aegis backup health-loop`` — run the health monitor continuously
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from aegis.backup.health import BackupHealth, backup_health_loop
from aegis.backup.pgbackrest_manager import BackupManager
from aegis.backup.restic_manager import ResticBackup


@click.group("backup")
def backup_group() -> None:
    """Phase 15 — Disaster Recovery & Business Continuity."""


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@backup_group.command("create")
@click.option(
    "--system",
    type=click.Choice(["pgbackrest", "restic", "all"], case_sensitive=False),
    default="pgbackrest",
    show_default=True,
    help="Backup system to use.",
)
@click.option(
    "--type",
    "backup_type",
    type=click.Choice(["full", "incr", "diff"], case_sensitive=False),
    default="incr",
    show_default=True,
    help="pgBackRest backup type (ignored for restic).",
)
@click.option("--label", default=None, help="Optional label / tag for this backup.")
@click.option("--json-out", is_flag=True, help="Output result as JSON.")
def cmd_create(system: str, backup_type: str, label: str | None, json_out: bool) -> None:
    """Create a new backup snapshot."""

    async def _run() -> None:
        results: dict = {}

        if system in ("pgbackrest", "all"):
            try:
                mgr = BackupManager()
                meta = await mgr.create_backup(backup_type=backup_type, label=label)
                results["pgbackrest"] = meta.to_dict()
            except Exception as exc:
                click.echo(f"pgBackRest error: {exc}", err=True)
                results["pgbackrest"] = {"error": str(exc)}

        if system in ("restic", "all"):
            try:
                rb = ResticBackup()
                sid = await rb.backup(label=label)
                results["restic"] = {"snapshot_id": sid}
            except Exception as exc:
                click.echo(f"restic error: {exc}", err=True)
                results["restic"] = {"error": str(exc)}

        if json_out:
            click.echo(json.dumps(results, indent=2, default=str))
        else:
            for svc, data in results.items():
                if "error" in data:
                    click.echo(f"[{svc}] FAILED: {data['error']}")
                else:
                    bid = data.get("backup_id") or data.get("snapshot_id", "")
                    click.echo(f"[{svc}] OK — backup ID: {bid}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@backup_group.command("list")
@click.option(
    "--system",
    type=click.Choice(["pgbackrest", "restic", "all"], case_sensitive=False),
    default="all",
    show_default=True,
)
@click.option("--json-out", is_flag=True)
def cmd_list(system: str, json_out: bool) -> None:
    """List available backups and snapshots."""

    async def _run() -> None:
        rows: dict = {}

        if system in ("pgbackrest", "all"):
            try:
                mgr = BackupManager()
                backups = await mgr.list_backups()
                rows["pgbackrest"] = [b.to_dict() for b in backups]
            except Exception as exc:
                rows["pgbackrest"] = [{"error": str(exc)}]

        if system in ("restic", "all"):
            try:
                rb = ResticBackup()
                snaps = await rb.list_snapshots()
                rows["restic"] = [s.to_dict() for s in snaps]
            except Exception as exc:
                rows["restic"] = [{"error": str(exc)}]

        if json_out:
            click.echo(json.dumps(rows, indent=2, default=str))
            return

        for svc, items in rows.items():
            click.echo(f"\n{'='*60}")
            click.echo(f"  {svc.upper()}  ({len(items)} item(s))")
            click.echo(f"{'='*60}")
            for item in items:
                if "error" in item:
                    click.echo(f"  ERROR: {item['error']}")
                else:
                    bid = item.get("backup_id") or item.get("snapshot_id", "")
                    ts = item.get("timestamp") or item.get("time", "")
                    size = item.get("size_mb")
                    size_str = f"  {size:.1f} MB" if size is not None else ""
                    click.echo(f"  {bid:<30}  {ts}{size_str}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


@backup_group.command("restore")
@click.option(
    "--system",
    type=click.Choice(["pgbackrest", "restic"], case_sensitive=False),
    required=True,
    help="Which backup system to restore from.",
)
@click.option("--backup-id", required=True, help="Backup label or snapshot ID (or 'latest').")
@click.option(
    "--target",
    default="aegis",
    show_default=True,
    help="Target DB name (pgbackrest) or restore path (restic).",
)
@click.option("--timeline", default=None, help="pgBackRest WAL timeline for PITR.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def cmd_restore(
    system: str,
    backup_id: str,
    target: str,
    timeline: str | None,
    yes: bool,
) -> None:
    """Restore from a backup. Use with care — this overwrites existing data."""
    if not yes:
        click.confirm(
            f"Restore {system} backup '{backup_id}' → '{target}'? This is destructive.",
            abort=True,
        )

    async def _run() -> None:
        if system == "pgbackrest":
            mgr = BackupManager()
            ok = await mgr.restore_full(backup_id, target, target_timeline=timeline)
            click.echo(f"pgBackRest restore {'succeeded' if ok else 'FAILED'}.")
        else:  # restic
            rb = ResticBackup()
            ok = await rb.restore(backup_id, Path(target))
            click.echo(f"restic restore {'succeeded' if ok else 'FAILED'}.")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


@backup_group.command("prune")
@click.option(
    "--system",
    type=click.Choice(["pgbackrest", "restic", "all"], case_sensitive=False),
    default="all",
    show_default=True,
)
def cmd_prune(system: str) -> None:
    """Remove backups older than the retention policy."""

    async def _run() -> None:
        if system in ("pgbackrest", "all"):
            try:
                mgr = BackupManager()
                n = await mgr.prune_old_backups()
                click.echo(f"pgBackRest: pruned {n} backup(s).")
            except Exception as exc:
                click.echo(f"pgBackRest prune error: {exc}", err=True)

        if system in ("restic", "all"):
            try:
                rb = ResticBackup()
                n = await rb.prune()
                click.echo(f"restic: pruned {n} snapshot(s).")
            except Exception as exc:
                click.echo(f"restic prune error: {exc}", err=True)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@backup_group.command("health")
@click.option("--json-out", is_flag=True)
def cmd_health(json_out: bool) -> None:
    """Check whether recent backups are within staleness thresholds."""

    async def _run() -> None:
        bh = BackupHealth()
        status = await bh.check()

        if json_out:
            click.echo(json.dumps(status, indent=2))
            return

        all_ok = all(status.values())
        for svc, healthy in status.items():
            icon = click.style("OK", fg="green") if healthy else click.style("STALE", fg="red")
            click.echo(f"  {svc:<20} {icon}")

        if not all_ok:
            sys.exit(1)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# health-loop
# ---------------------------------------------------------------------------


@backup_group.command("health-loop")
@click.option("--interval", default=1800, show_default=True, help="Check interval in seconds.")
def cmd_health_loop(interval: int) -> None:
    """Run the backup health monitor continuously (blocks the terminal)."""
    click.echo(f"Starting backup health loop (interval={interval}s). Press Ctrl+C to stop.")
    try:
        asyncio.run(backup_health_loop(interval_s=interval))
    except KeyboardInterrupt:
        click.echo("\nStopped.")
