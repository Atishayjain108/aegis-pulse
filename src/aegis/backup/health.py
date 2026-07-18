"""
aegis.backup.health
====================

Backup health monitor — checks staleness of pgBackRest and restic backups,
tracks consecutive failures, and dispatches alerts via Phase 4 notifiers.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING

import structlog

from aegis.backup.errors import ResticPasswordMissingError
from aegis.backup.settings import BackupSettings

if TYPE_CHECKING:
    pass

_log = structlog.get_logger("aegis.backup.health")


@lru_cache(maxsize=1)
def _settings() -> BackupSettings:
    return BackupSettings()


class BackupHealth:
    """Track backup staleness and dispatch alerts on persistent failures.

    Usage::

        health = BackupHealth()
        status = await health.check()
        # {"pgbackrest": True, "restic": True}
    """

    def __init__(self, settings: BackupSettings | None = None) -> None:
        cfg = settings or _settings()
        self._settings = cfg
        self._pg_stale_min = cfg.pgbackrest_stale_threshold_min
        self._restic_stale_min = cfg.restic_stale_threshold_min
        self._alert_threshold = cfg.consecutive_failure_alert_threshold
        self._consecutive_failures: dict[str, int] = {"pgbackrest": 0, "restic": 0}
        self._last_check: dict[str, datetime | None] = {
            "pgbackrest": None,
            "restic": None,
        }

    async def check(self) -> dict[str, bool]:
        """Return health status for each backup system.

        Keys are ``"pgbackrest"`` and ``"restic"``.  ``True`` means healthy.
        """
        results: dict[str, bool] = {}

        results["pgbackrest"] = await self._check_pgbackrest()
        results["restic"] = await self._check_restic()

        for service, healthy in results.items():
            if healthy:
                self._consecutive_failures[service] = 0
            else:
                self._consecutive_failures[service] += 1
                n = self._consecutive_failures[service]
                if n >= self._alert_threshold:
                    await self._send_alert(service, n)

        return results

    @property
    def consecutive_failures(self) -> dict[str, int]:
        return dict(self._consecutive_failures)

    # ------------------------------------------------------------------
    # pgBackRest check
    # ------------------------------------------------------------------

    async def _check_pgbackrest(self) -> bool:
        try:
            from aegis.backup.pgbackrest_manager import BackupManager

            mgr = BackupManager(settings=self._settings)
            backups = await mgr.list_backups()
            if not backups:
                _log.warning("backup.pgbackrest_no_backups", error_code="AEGIS-BACKUP-0009")
                return False

            most_recent = max(backups, key=lambda b: b.timestamp)
            age_min = (datetime.now(UTC) - most_recent.timestamp).total_seconds() / 60
            self._last_check["pgbackrest"] = datetime.now(UTC)

            if age_min > self._pg_stale_min:
                _log.warning(
                    "backup.pgbackrest_stale",
                    age_min=round(age_min, 1),
                    threshold_min=self._pg_stale_min,
                    error_code="AEGIS-BACKUP-0009",
                )
                return False

            _log.debug("backup.pgbackrest_healthy", age_min=round(age_min, 1))
            return True

        except Exception as exc:
            _log.error("backup.pgbackrest_health_check_failed", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # restic check
    # ------------------------------------------------------------------

    async def _check_restic(self) -> bool:
        try:
            from aegis.backup.restic_manager import ResticBackup

            rb = ResticBackup(settings=self._settings)
            snapshots = await rb.list_snapshots()
            if not snapshots:
                _log.warning("backup.restic_no_snapshots", error_code="AEGIS-BACKUP-0010")
                return False

            most_recent = snapshots[-1]  # sorted oldest-first
            age_min = (datetime.now(UTC) - most_recent.time).total_seconds() / 60
            self._last_check["restic"] = datetime.now(UTC)

            if age_min > self._restic_stale_min:
                _log.warning(
                    "backup.restic_stale",
                    age_min=round(age_min, 1),
                    threshold_min=self._restic_stale_min,
                    error_code="AEGIS-BACKUP-0010",
                )
                return False

            _log.debug("backup.restic_healthy", age_min=round(age_min, 1))
            return True

        except ResticPasswordMissingError:
            # restic not configured — treat as skip (not failure).
            _log.debug("backup.restic_not_configured")
            return True
        except Exception as exc:
            _log.error("backup.restic_health_check_failed", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Alerting
    # ------------------------------------------------------------------

    async def _send_alert(self, service: str, failure_count: int) -> None:
        """Dispatch a backup failure alert via available channels.

        Best-effort: never raises; logs on failure.
        """
        _log.error(
            "backup.persistent_failure",
            service=service,
            consecutive_failures=failure_count,
            error_code="AEGIS-BACKUP-0011",
        )

        message = (
            f"AEGIS backup alert: {service} has failed {failure_count} consecutive health "
            "checks. Immediate investigation required."
        )

        # Try Phase 4 Telegram notifier if available.
        try:
            from aegis.execute.notifiers.telegram import (
                TelegramNotifier,  # type: ignore[import]
            )

            notifier = TelegramNotifier()  # type: ignore[call-arg]
            if hasattr(notifier, "_send_raw"):
                await notifier._send_raw(message)  # type: ignore[attr-defined]
        except Exception as exc:
            _log.debug("backup.alert_telegram_failed", error=str(exc))

        # Try ntfy if configured.
        try:
            from aegis.config import settings as get_settings

            cfg = get_settings()
            if cfg.alerts.ntfy_topic:
                import httpx

                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{cfg.alerts.ntfy_url}/{cfg.alerts.ntfy_topic}",
                        content=message.encode(),
                        headers={"Title": "AEGIS Backup Alert", "Priority": "urgent"},
                        timeout=10,
                    )
        except Exception as exc:
            _log.debug("backup.alert_ntfy_failed", error=str(exc))


async def backup_health_loop(interval_s: int | None = None) -> None:
    """Long-running coroutine that checks backup health on a schedule.

    Runs until the process exits. Designed to be started as an asyncio task.
    """
    cfg = _settings()
    sleep_s = interval_s if interval_s is not None else cfg.health_check_interval_s
    health = BackupHealth()

    while True:
        try:
            status = await health.check()
            _log.info("backup.health_check_complete", status=status)
        except Exception as exc:
            _log.error("backup.health_loop_error", error=str(exc))
        await asyncio.sleep(sleep_s)
