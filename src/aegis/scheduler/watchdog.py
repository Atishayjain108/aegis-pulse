"""
aegis.scheduler.watchdog — Stage-2 data-machine watchdog.
=========================================================

Recovery protocol, Stage 2 ("let it run, touch nothing"). The stage's single
biggest risk is silent failure of the data-collection machine itself: a
sleeping laptop voids 72h claim windows quietly, the clean-claim count stays
near zero, and GATE BETA would read "the thesis failed" when the truth is
"the machine failed".

Checked once per day (and on demand via ``aegis watchdog``):

    (a) zero new rows in ``signals`` in the last 24h        → page operator
    (b) zero new settled claims with window_scraper_alive
        = TRUE in the last 24h                              → page operator

Pages go through the existing 1.7 ntfy ops channel (``_notify_ops`` — one
sender, never two). Doctrine followed here:

* ``datetime.now(UTC)`` everywhere.
* RLS: ``SET app.current_tenant`` before every query.
* Never raises into the scheduler — any internal failure logs at WARNING and
  the function returns (Phase 4 SEC-013 best-effort pattern). A failure of
  the watchdog ITSELF is paged with a distinct title: an unverifiable
  watchdog silently passing is the exact failure mode this stage exists to
  prevent (1.2 doctrine: unverifiable == unhealthy).
* No secrets in log lines — the ntfy topic/URL are never logged here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

log = structlog.get_logger("aegis.scheduler.watchdog")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# Alarm identifiers — stable strings, used in tests and in the page body.
ALARM_NO_INGEST = "no_signals_ingested_24h"
ALARM_NO_CLEAN_SETTLE = "no_clean_settlements_24h"
ALARM_UNVERIFIABLE = "watchdog_unverifiable"


@dataclass(frozen=True)
class WatchdogReport:
    """Outcome of one watchdog pass. ``paged`` is False on --dry-run even
    when alarms are present."""

    checked_at: datetime
    signals_24h: int = -1  # -1 = count could not be read
    clean_settled_24h: int = -1
    alarms: tuple[str, ...] = field(default_factory=tuple)
    paged: bool = False

    @property
    def healthy(self) -> bool:
        return not self.alarms


async def _count_signals_24h(conn: Any, tenant_id: str) -> int:
    await conn.execute(
        "SELECT set_config('app.current_tenant', $1, false)", tenant_id
    )
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS n FROM signals "
        "WHERE scraped_at > NOW() - INTERVAL '24 hours'"
    )
    return int(row["n"]) if row else 0


async def _count_clean_settlements_24h(conn: Any, tenant_id: str) -> int:
    await conn.execute(
        "SELECT set_config('app.current_tenant', $1, false)", tenant_id
    )
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS n FROM signal_outcomes "
        "WHERE resolution_status IN ('correct', 'incorrect') "
        "AND window_scraper_alive = TRUE "
        "AND settled_at > NOW() - INTERVAL '24 hours'"
    )
    return int(row["n"]) if row else 0


def _page_body(report: WatchdogReport) -> tuple[str, str]:
    """(title, body) for the ntfy page. ASCII title (latin-1 header spec)."""
    parts: list[str] = []
    if ALARM_NO_INGEST in report.alarms:
        parts.append(
            f"(a) ZERO new signals in 24h (count={report.signals_24h}). "
            "Ingestion is dead or the machine slept through every cycle."
        )
    if ALARM_NO_CLEAN_SETTLE in report.alarms:
        parts.append(
            f"(b) ZERO clean settled claims in 24h (count={report.clean_settled_24h}). "
            "Windows are voiding or settlement is stalled - Stage 2 is not accruing."
        )
    if ALARM_UNVERIFIABLE in report.alarms:
        parts.append(
            "Watchdog could not read the database - machine state UNKNOWN, "
            "which counts as unhealthy."
        )
    title = "AEGIS Stage-2 watchdog: data machine needs attention"
    body = "\n".join(parts) + f"\nchecked_at={report.checked_at.isoformat()}"
    return title, body


async def _send_page(report: WatchdogReport) -> None:
    """Deliver via the existing 1.7 ops channel. Lazy import avoids a module
    cycle (autonomous.py imports this module for job registration)."""
    from aegis.scheduler.autonomous import _notify_ops

    title, body = _page_body(report)
    await _notify_ops(title, body, priority="5")


async def run_watchdog(
    pool: Any | None = None,
    *,
    tenant_id: str = _DEFAULT_TENANT,
    dry_run: bool = False,
) -> WatchdogReport:
    """One watchdog pass. Never raises; always returns a report.

    ``pool`` may be injected (tests); otherwise a short-lived PgPool is
    opened and closed here.
    """
    checked_at = datetime.now(UTC)
    owns_pool = pool is None
    signals_n = -1
    settled_n = -1
    alarms: list[str] = []

    try:
        if owns_pool:
            from aegis.config import settings as _settings
            from aegis.db.pool import PgPool

            pool = PgPool(dsn=_settings().pg_dsn_str)
            await pool.connect()
        try:
            async with pool.acquire() as conn:
                signals_n = await _count_signals_24h(conn, tenant_id)
                settled_n = await _count_clean_settlements_24h(conn, tenant_id)
        finally:
            if owns_pool:
                await pool.aclose()

        if signals_n == 0:
            alarms.append(ALARM_NO_INGEST)
        if settled_n == 0:
            alarms.append(ALARM_NO_CLEAN_SETTLE)
    except Exception as exc:
        # Unverifiable == unhealthy. Log (no secrets) and page distinctly.
        log.warning("watchdog.check_failed", error=str(exc)[:160])
        alarms.append(ALARM_UNVERIFIABLE)

    report = WatchdogReport(
        checked_at=checked_at,
        signals_24h=signals_n,
        clean_settled_24h=settled_n,
        alarms=tuple(alarms),
        paged=False,
    )

    if report.alarms and not dry_run:
        try:
            await _send_page(report)
            report = WatchdogReport(
                checked_at=report.checked_at,
                signals_24h=report.signals_24h,
                clean_settled_24h=report.clean_settled_24h,
                alarms=report.alarms,
                paged=True,
            )
        except Exception as exc:  # _notify_ops is itself best-effort; belt only
            log.warning("watchdog.page_failed", error=str(exc)[:120])

    log.info(
        "watchdog.pass",
        signals_24h=report.signals_24h,
        clean_settled_24h=report.clean_settled_24h,
        alarms=list(report.alarms),
        paged=report.paged,
        dry_run=dry_run,
    )
    return report


# ---------------------------------------------------------------------------
# CLI — `aegis watchdog [--dry-run] [--health]`
# ---------------------------------------------------------------------------

try:  # click is a hard dep of the main CLI; guarded per module rules anyway
    import asyncio as _asyncio

    import click

    @click.command("watchdog")
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Run both checks and print the verdict; never send a page.",
    )
    @click.option(
        "--health",
        is_flag=True,
        help="Print machine-readable status; exit 0 healthy, 2 alarming. Implies no page.",
    )
    def watchdog_cmd(dry_run: bool, health: bool) -> None:
        """Stage-2 data-machine watchdog (ingestion + clean settlements, 24h)."""
        report = _asyncio.run(run_watchdog(dry_run=dry_run or health))
        if health:
            click.echo(
                f'{{"healthy": {str(report.healthy).lower()}, '
                f'"signals_24h": {report.signals_24h}, '
                f'"clean_settled_24h": {report.clean_settled_24h}, '
                f'"alarms": {list(report.alarms)}}}'
            )
            raise SystemExit(0 if report.healthy else 2)
        state = "HEALTHY" if report.healthy else f"ALARM: {', '.join(report.alarms)}"
        sent = "page sent" if report.paged else ("dry-run, no page" if dry_run else "no page needed")
        click.echo(
            f"{state} | signals_24h={report.signals_24h} "
            f"clean_settled_24h={report.clean_settled_24h} | {sent}"
        )
except Exception:  # pragma: no cover — CLI absence never breaks the scheduler
    watchdog_cmd = None  # type: ignore[assignment]


__all__ = [
    "ALARM_NO_CLEAN_SETTLE",
    "ALARM_NO_INGEST",
    "ALARM_UNVERIFIABLE",
    "WatchdogReport",
    "run_watchdog",
]
