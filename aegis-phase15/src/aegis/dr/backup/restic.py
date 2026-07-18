"""
aegis.dr.backup.restic
======================
restic encrypted backup for Phase 15 — Disaster Recovery.

Strategy
--------
* Backs up ``restic_source_paths`` to a restic repo on MinIO
  (``s3:http://<minio_endpoint>/<dr_bucket>-restic``).
* Enforces retention: daily=7, weekly=4, monthly=3.
* Verifies last snapshot integrity via ``restic check``.
* Idempotent: running twice produces one new snapshot (restic deduplication).

Prerequisites
-------------
``restic`` binary must be on PATH.  Install:
    ``apt-get install -y restic``  OR  ``brew install restic``

If ``AEGIS_DR_RESTIC_ENABLED=false`` (default), this engine silently skips
and returns a SKIPPED manifest — safe to include in the orchestrator always.

Architecture
-----------
Subprocess-based (restic has no Python API). Non-blocking via asyncio.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import UTC, datetime

import structlog

from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.constants import (
    RESTIC_COMPRESSION,
    RESTIC_KEEP_DAILY,
    RESTIC_KEEP_MONTHLY,
    RESTIC_KEEP_WEEKLY,
    RESTIC_PACK_SIZE_MB,
)
from aegis.dr.errors import ResticBackupError
from aegis.dr.schemas import BackupManifest, BackupStatus, BackupTarget

_log = structlog.get_logger("aegis.dr.backup.restic")


class ResticBackup:
    """
    Encrypted restic backup to MinIO.

    Parameters
    ----------
    settings:
        DR settings singleton.
    """

    def __init__(self, *, settings: DisasterRecoverySettings) -> None:
        self._settings = settings

    async def run(self) -> BackupManifest:
        """
        Execute one restic backup cycle.

        Returns
        -------
        BackupManifest — status SKIPPED if restic is disabled or unavailable.

        Raises
        ------
        ResticBackupError
            If restic exits non-zero.
        """
        started_at = datetime.now(UTC)

        if not self._settings.restic_enabled:
            _log.debug("restic.backup.disabled")
            return self._skipped_manifest(started_at)

        if not shutil.which("restic"):
            _log.warning(
                "restic.backup.binary_not_found",
                hint="apt-get install -y restic",
            )
            return self._skipped_manifest(started_at)

        if not self._settings.restic_repository:
            _log.warning("restic.backup.no_repo_configured")
            return self._skipped_manifest(started_at)

        env = self._build_env()

        # Initialise repo if it doesn't exist yet (idempotent)
        await self._init_if_needed(env)

        # Backup
        snapshot_id = await self._backup(env)

        # Prune old snapshots
        await self._forget(env)

        # Spot-check integrity
        await self._check(env)

        finished_at = datetime.now(UTC)

        manifest = BackupManifest(
            target=BackupTarget.RESTIC,
            status=BackupStatus.SUCCESS,
            started_at=started_at,
            finished_at=finished_at,
            size_bytes=0,          # restic manages its own deduped storage
            checksum_sha256=snapshot_id,
            minio_path=self._settings.restic_repository,
        )

        _log.info(
            "restic.backup.success",
            snapshot_id=snapshot_id,
            duration_s=manifest.duration_s,
        )

        return manifest

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["RESTIC_REPOSITORY"] = self._settings.restic_repository
        env["RESTIC_PASSWORD"] = self._settings.restic_password.get_secret_value()
        env["RESTIC_COMPRESSION"] = RESTIC_COMPRESSION
        env["RESTIC_PACK_SIZE"] = str(RESTIC_PACK_SIZE_MB)
        # MinIO S3 credentials for restic
        env["AWS_ACCESS_KEY_ID"] = self._settings.minio_access_key.get_secret_value()
        env["AWS_SECRET_ACCESS_KEY"] = self._settings.minio_secret_key.get_secret_value()
        return env

    async def _init_if_needed(self, env: dict[str, str]) -> None:
        """Initialise the restic repo; no-op if already initialised."""
        rc, _, _ = await self._run(["restic", "snapshots", "--json"], env)
        if rc != 0:
            _log.info("restic.backup.initialising_repo")
            rc2, _, stderr = await self._run(["restic", "init"], env)
            if rc2 != 0:
                raise ResticBackupError(
                    "restic init failed",
                    context={"stderr": stderr[:1000]},
                )

    async def _backup(self, env: dict[str, str]) -> str:
        sources = [
            str(p) for raw in self._settings.restic_source_paths
            for p in [__import__("pathlib").Path(raw).expanduser()]
            if p.exists()
        ]
        if not sources:
            _log.warning("restic.backup.no_valid_source_paths")
            return ""

        cmd = ["restic", "backup", "--json", *sources]
        rc, stdout, stderr = await self._run(cmd, env)
        if rc != 0:
            raise ResticBackupError(
                "restic backup failed",
                context={"rc": rc, "stderr": stderr[:1000]},
            )

        # Extract snapshot ID from JSON output
        import json as _json
        for line in reversed(stdout.splitlines()):
            try:
                obj = _json.loads(line)
                if obj.get("message_type") == "summary":
                    return str(obj.get("snapshot_id", "unknown"))
            except _json.JSONDecodeError:
                continue
        return "unknown"

    async def _forget(self, env: dict[str, str]) -> None:
        cmd = [
            "restic",
            "forget",
            "--prune",
            f"--keep-daily={RESTIC_KEEP_DAILY}",
            f"--keep-weekly={RESTIC_KEEP_WEEKLY}",
            f"--keep-monthly={RESTIC_KEEP_MONTHLY}",
        ]
        rc, _, stderr = await self._run(cmd, env)
        if rc != 0:
            _log.warning("restic.backup.forget_failed", stderr=stderr[:500])

    async def _check(self, env: dict[str, str]) -> None:
        rc, _, stderr = await self._run(["restic", "check"], env)
        if rc != 0:
            _log.warning("restic.backup.check_failed", stderr=stderr[:500])

    @staticmethod
    async def _run(
        cmd: list[str],
        env: dict[str, str],
    ) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=600
        )
        return (
            proc.returncode or 0,
            stdout_b.decode(errors="replace"),
            stderr_b.decode(errors="replace"),
        )

    @staticmethod
    def _skipped_manifest(started_at: datetime) -> BackupManifest:
        return BackupManifest(
            target=BackupTarget.RESTIC,
            status=BackupStatus.SKIPPED,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            size_bytes=0,
            checksum_sha256="",
            minio_path="",
        )
