"""
aegis.dr.orchestrator
=====================
DR Orchestrator for Phase 15 — coordinates all backup + health jobs.

Responsibilities
----------------
* Runs backup jobs on configurable intervals using asyncio tasks.
* Schedules the weekly restore drill.
* Computes and publishes RPO drift continuously.
* Handles graceful shutdown on SIGTERM (drains in-flight jobs).
* Integrates with Phase 4 notification path for failure alerts.

Design
------
* Single asyncio event loop — compatible with the existing AEGIS event loop.
* Uses wall-clock timestamps (not monotonic) for WSL sleep-resilience.
* Each backup job is idempotent — safe to run multiple times.
* Publishes ``aegis:dr:last_backup:<target>`` Redis keys for the dashboard.

Architecture
-----------
Instantiated by ``aegis.dr.cli`` or embedded in the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from aegis.dr.backup.models import ModelRegistryBackup
from aegis.dr.backup.postgres import PostgresBackup
from aegis.dr.backup.redis import RedisBackup
from aegis.dr.backup.restic import ResticBackup
from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.drill import RestoreDrill
from aegis.dr.errors import AegisDrError
from aegis.dr.health import DrHealthChecker
from aegis.dr.schemas import BackupManifest, BackupTarget

_log = structlog.get_logger("aegis.dr.orchestrator")


class DrOrchestrator:
    """
    Coordinates all DR backup, health-check, and drill jobs.

    Parameters
    ----------
    settings:
        DR settings singleton.
    minio_client:
        Pre-initialized ``minio.Minio`` client.
    redis_client:
        Optional async Redis client (Phase 1).
    """

    def __init__(
        self,
        *,
        settings: DisasterRecoverySettings,
        minio_client: Any,
        redis_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._minio = minio_client
        self._redis = redis_client
        self._tasks: list[asyncio.Task[Any]] = []
        self._shutdown = asyncio.Event()

        # Engines
        self._pg_backup = PostgresBackup(
            settings=settings, minio_client=minio_client
        )
        self._redis_backup: RedisBackup | None = None
        if redis_client is not None:
            self._redis_backup = RedisBackup(
                settings=settings,
                minio_client=minio_client,
                redis_client=redis_client,
            )
        self._model_backup = ModelRegistryBackup(
            settings=settings, minio_client=minio_client
        )
        self._restic_backup = ResticBackup(settings=settings)
        self._health = DrHealthChecker(
            settings=settings,
            minio_client=minio_client,
            redis_client=redis_client,
        )
        self._drill = RestoreDrill(
            settings=settings,
            minio_client=minio_client,
            redis_client=redis_client,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all background jobs."""
        _log.info("orchestrator.start")

        self._tasks = [
            asyncio.create_task(
                self._backup_loop(
                    name="postgres",
                    interval_s=self._settings.pg_backup_interval_s,
                    runner=self._run_pg_backup,
                ),
                name="dr-pg-backup",
            ),
            asyncio.create_task(
                self._backup_loop(
                    name="redis",
                    interval_s=self._settings.redis_backup_interval_s,
                    runner=self._run_redis_backup,
                ),
                name="dr-redis-backup",
            ),
            asyncio.create_task(
                self._backup_loop(
                    name="models",
                    interval_s=self._settings.model_backup_interval_s,
                    runner=self._run_model_backup,
                ),
                name="dr-model-backup",
            ),
            asyncio.create_task(
                self._backup_loop(
                    name="restic",
                    interval_s=86_400,  # daily
                    runner=self._run_restic_backup,
                ),
                name="dr-restic-backup",
            ),
            asyncio.create_task(
                self._health_loop(),
                name="dr-health",
            ),
        ]

        if self._settings.drill_enabled:
            self._tasks.append(
                asyncio.create_task(
                    self._drill_loop(),
                    name="dr-drill",
                )
            )

        _log.info("orchestrator.tasks_started", count=len(self._tasks))

    async def stop(self) -> None:
        """Signal graceful shutdown and await task completion."""
        _log.info("orchestrator.stop")
        self._shutdown.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        _log.info("orchestrator.stopped")

    # ------------------------------------------------------------------
    # Loop templates
    # ------------------------------------------------------------------

    async def _backup_loop(
        self,
        *,
        name: str,
        interval_s: int,
        runner: Any,
    ) -> None:
        """Generic backup scheduling loop."""
        _log.info("backup_loop.start", job=name, interval_s=interval_s)
        while not self._shutdown.is_set():
            wall_start = datetime.now(UTC)
            try:
                manifest: BackupManifest = await runner()
                await self._publish_last_backup(name, manifest)
            except AegisDrError as exc:
                _log.error(
                    "backup_loop.error",
                    job=name,
                    code=exc.machine_code,
                    msg=exc.human_message,
                    ctx=exc.context,
                )
                if self._settings.alert_on_backup_failure:
                    await self._send_alert(
                        f"[DR] {name} backup failed: {exc.human_message}",
                        priority="high",
                    )
            except Exception as exc:
                _log.error("backup_loop.unexpected_error", job=name, error=str(exc))

            elapsed = (datetime.now(UTC) - wall_start).total_seconds()
            sleep_s = max(0.0, interval_s - elapsed)
            _log.debug("backup_loop.sleep", job=name, sleep_s=sleep_s)

            try:
                await asyncio.wait_for(
                    self._shutdown.wait(), timeout=sleep_s
                )
                break  # Shutdown signalled during sleep
            except TimeoutError:
                pass  # Normal — time to run the next backup

    async def _health_loop(self) -> None:
        """Periodic DR health check."""
        from aegis.dr.constants import HEALTH_CHECK_INTERVAL_S

        while not self._shutdown.is_set():
            try:
                await self._health.check()
            except Exception as exc:
                _log.warning("health_loop.error", error=str(exc))
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(), timeout=HEALTH_CHECK_INTERVAL_S
                )
                break
            except TimeoutError:
                pass

    async def _drill_loop(self) -> None:
        """Weekly restore drill."""
        while not self._shutdown.is_set():
            try:
                result = await self._drill.run()
                if not result.passed and self._settings.alert_on_drill_failure:
                    await self._send_alert(
                        f"[DR] Weekly restore drill FAILED — outcome={result.outcome.value}",
                        priority="critical",
                    )
            except Exception as exc:
                _log.error("drill_loop.error", error=str(exc))
                if self._settings.alert_on_drill_failure:
                    await self._send_alert(
                        f"[DR] Restore drill error: {exc}",
                        priority="critical",
                    )
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=self._settings.drill_interval_s,
                )
                break
            except TimeoutError:
                pass

    # ------------------------------------------------------------------
    # Job runners
    # ------------------------------------------------------------------

    async def _run_pg_backup(self) -> BackupManifest:
        return await self._pg_backup.run()

    async def _run_redis_backup(self) -> BackupManifest:
        if self._redis_backup is None:
            _log.debug("redis_backup.no_client_configured")
            from datetime import UTC, datetime

            from aegis.dr.schemas import BackupStatus

            now = datetime.now(UTC)
            return BackupManifest(
                target=BackupTarget.REDIS,
                status=BackupStatus.SKIPPED,
                started_at=now,
                finished_at=now,
                size_bytes=0,
                checksum_sha256="",
                minio_path="",
            )
        return await self._redis_backup.run()

    async def _run_model_backup(self) -> BackupManifest:
        return await self._model_backup.run()

    async def _run_restic_backup(self) -> BackupManifest:
        return await self._restic_backup.run()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _publish_last_backup(
        self, name: str, manifest: BackupManifest
    ) -> None:
        """Publish last backup metadata to Redis for dashboard consumption."""
        if self._redis is None:
            return
        key = f"aegis:dr:last_backup:{name}"
        try:
            await self._redis.set(
                key,
                manifest.model_dump_json(),
                ex=self._settings.pg_backup_interval_s * 3,
            )
        except Exception as exc:
            _log.warning("orchestrator.publish_failed", key=key, error=str(exc))

    async def _send_alert(self, message: str, *, priority: str = "high") -> None:
        """
        Forward alert to Phase 4 notification path.

        Tries ntfy first (if configured), then logs at ERROR so the
        monitoring stack (Grafana/Alertmanager) picks it up.
        """
        _log.error("dr.alert", message=message, priority=priority)

        topic = self._settings.ntfy_topic
        if not topic:
            return

        try:
            import httpx  # already a root dep

            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://ntfy.sh/{topic}",
                    content=message.encode(),
                    headers={
                        "Priority": priority,
                        "Title": "AEGIS DR Alert",
                        "Tags": "warning,floppy_disk",
                    },
                )
        except Exception as exc:
            _log.warning("orchestrator.ntfy_failed", error=str(exc))
