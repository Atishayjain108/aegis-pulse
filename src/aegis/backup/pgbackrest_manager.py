"""
aegis.backup.pgbackrest_manager
================================

pgBackRest wrapper — incremental Postgres backups to MinIO every 15 min.

Handles backup scheduling, WAL archiving, stanza management, PITR, and
retention policies. All subprocess calls are offloaded via asyncio.to_thread
so they never block the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from aegis.backup.errors import (
    BackupCommandError,
    BackupInitError,
    BackupMetadataStoreError,
    BackupTimeoutError,
    BackupVerifyError,
    RestoreError,
)
from aegis.backup.settings import BackupSettings

if TYPE_CHECKING:
    pass

_log = structlog.get_logger("aegis.backup.pgbackrest")


@lru_cache(maxsize=1)
def _settings() -> BackupSettings:
    return BackupSettings()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class BackupMetadata:
    """Metadata captured for every pgBackRest backup run."""

    __slots__ = (
        "backup_id",
        "backup_type",
        "checksum",
        "database_version",
        "duration_s",
        "host",
        "retention_expires",
        "size_mb",
        "status",
        "timestamp",
        "wal_start",
        "wal_stop",
    )

    def __init__(
        self,
        *,
        backup_id: str,
        backup_type: str,
        size_mb: float,
        duration_s: float,
        database_version: str,
        checksum: str,
        retention_expires: datetime,
        host: str,
        status: str,
        timestamp: datetime | None = None,
        wal_start: str | None = None,
        wal_stop: str | None = None,
    ) -> None:
        self.backup_id = backup_id
        self.timestamp = timestamp or datetime.now(UTC)
        self.backup_type = backup_type
        self.size_mb = size_mb
        self.duration_s = duration_s
        self.wal_start = wal_start
        self.wal_stop = wal_stop
        self.database_version = database_version
        self.checksum = checksum
        self.retention_expires = retention_expires
        self.host = host
        self.status = status

    def to_dict(self) -> dict:
        return {
            "backup_id": self.backup_id,
            "timestamp": self.timestamp.isoformat(),
            "backup_type": self.backup_type,
            "size_mb": self.size_mb,
            "duration_s": self.duration_s,
            "wal_start": self.wal_start,
            "wal_stop": self.wal_stop,
            "database_version": self.database_version,
            "checksum": self.checksum,
            "retention_expires": self.retention_expires.isoformat(),
            "host": self.host,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BackupMetadata:
        return cls(
            backup_id=data["backup_id"],
            backup_type=data["backup_type"],
            size_mb=data.get("size_mb", 0.0),
            duration_s=data.get("duration_s", 0.0),
            database_version=data.get("database_version", "unknown"),
            checksum=data.get("checksum", ""),
            retention_expires=datetime.fromisoformat(data["retention_expires"]),
            host=data.get("host", "unknown"),
            status=data.get("status", "ok"),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else None,
            wal_start=data.get("wal_start"),
            wal_stop=data.get("wal_stop"),
        )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class BackupManager:
    """Manages pgBackRest backups and recovery.

    Usage::

        mgr = BackupManager()
        meta = await mgr.create_backup("incr")
        backups = await mgr.list_backups()
        ok = await mgr.restore_full(meta.backup_id, "aegis")
    """

    def __init__(self, settings: BackupSettings | None = None) -> None:
        cfg = settings or _settings()
        self.stanza = cfg.pgbackrest_stanza
        self.repo_path = Path(cfg.pgbackrest_repo_path)
        self._timeout_s = cfg.pgbackrest_timeout_s
        self._retention_days = cfg.pgbackrest_retention_full_days
        self._minio_bucket = cfg.minio_bucket
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Ensure repo directory and MinIO bucket exist."""
        if self._initialized:
            return
        try:
            await asyncio.to_thread(self.repo_path.mkdir, parents=True, exist_ok=True)
            await self._ensure_minio_bucket()
            _log.info("pgbackrest.initialized", stanza=self.stanza)
            self._initialized = True
        except Exception as exc:
            raise BackupInitError(
                f"pgBackRest init failed: {exc}", code="AEGIS-BACKUP-0001"
            ) from exc

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    async def create_backup(
        self,
        backup_type: str = "incr",
        label: str | None = None,
    ) -> BackupMetadata:
        """Create a pgBackRest backup.

        Args:
            backup_type: ``"full"`` (weekly), ``"incr"`` (15-min default),
                         ``"diff"`` (hourly).
            label: Optional descriptive label stored in pgBackRest comment.

        Returns:
            ``BackupMetadata`` with backup ID and checksum.
        """
        await self.initialize()

        cmd = [
            "pgbackrest",
            f"--stanza={self.stanza}",
            f"--repo1-path={self.repo_path}",
            f"--type={backup_type}",
            "backup",
        ]
        if label:
            cmd.extend([f"--set-user-comment={label}"])

        _log.info("pgbackrest.backup_start", type=backup_type)
        start = time.monotonic()

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackupTimeoutError(
                f"pgBackRest backup timed out after {self._timeout_s}s",
                code="AEGIS-BACKUP-0002",
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise BackupCommandError(
                f"pgBackRest backup failed: {exc.stderr}",
                code="AEGIS-BACKUP-0003",
            ) from exc

        duration_s = time.monotonic() - start
        backup_id = self._extract_backup_id(result.stdout + result.stderr)
        size_mb = await asyncio.to_thread(self._get_backup_size_mb, backup_id)
        checksum = await asyncio.to_thread(self._compute_backup_checksum, backup_id)

        metadata = BackupMetadata(
            backup_id=backup_id,
            backup_type=backup_type,
            size_mb=size_mb,
            duration_s=duration_s,
            database_version="16",
            checksum=checksum,
            retention_expires=datetime.now(UTC) + timedelta(days=self._retention_days),
            host=os.getenv("HOSTNAME", "unknown"),
            status="ok",
        )

        await self._store_backup_metadata(metadata)
        _log.info(
            "pgbackrest.backup_complete",
            backup_id=backup_id,
            type=backup_type,
            size_mb=size_mb,
            duration_s=round(duration_s, 2),
        )
        return metadata

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    async def restore_full(
        self,
        backup_id: str,
        target_db: str,
        *,
        target_timeline: str | None = None,
    ) -> bool:
        """Restore database from a specific backup, optionally to PITR.

        Args:
            backup_id: pgBackRest backup label (e.g. ``"20260531T120000Z"``).
            target_db: Database name to restore.
            target_timeline: WAL timeline for PITR (e.g. ``"00000001"``).

        Returns:
            ``True`` if restore succeeded and sanity checks passed.
        """
        await self.initialize()

        cmd = [
            "pgbackrest",
            f"--stanza={self.stanza}",
            f"--repo1-path={self.repo_path}",
            f"--db-include={target_db}",
            "--delta",
            "--force",
            f"--set={backup_id}",
            "restore",
        ]
        if target_timeline:
            cmd.append(f"--target-timeline={target_timeline}")

        _log.warning("pgbackrest.restore_start", backup_id=backup_id, target_db=target_db)

        try:
            await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RestoreError(
                f"pgBackRest restore failed: {exc.stderr}",
                code="AEGIS-BACKUP-0004",
            ) from exc

        verified = await self._verify_restore(target_db)
        if not verified:
            raise BackupVerifyError(
                "Post-restore sanity check failed — database appears empty",
                code="AEGIS-BACKUP-0013",
            )

        _log.info("pgbackrest.restore_complete", backup_id=backup_id)
        return True

    # ------------------------------------------------------------------
    # Listing + pruning
    # ------------------------------------------------------------------

    async def list_backups(self) -> list[BackupMetadata]:
        """Return all available backups from pgBackRest info (JSON)."""
        await self.initialize()

        cmd = [
            "pgbackrest",
            f"--stanza={self.stanza}",
            f"--repo1-path={self.repo_path}",
            "--output=json",
            "info",
        ]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except Exception as exc:
            _log.error("pgbackrest.list_failed", error=str(exc))
            return []

        try:
            info = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        backups: list[BackupMetadata] = []
        for stanza_info in info:
            for bk in stanza_info.get("backup", []):
                try:
                    meta = BackupMetadata(
                        backup_id=bk["label"],
                        backup_type=bk["type"],
                        size_mb=bk.get("info", {}).get("repo", {}).get("size", 0) / 1024 / 1024,
                        duration_s=bk.get("timestamp", {}).get("stop", 0)
                        - bk.get("timestamp", {}).get("start", 0),
                        database_version=str(
                            bk.get("db", {}).get("version", "unknown")
                        ),
                        checksum=bk.get("checksum-page-error", {}).get("id", ""),
                        retention_expires=datetime.now(UTC) + timedelta(days=self._retention_days),
                        host=bk.get("archive", {}).get("start", "unknown"),
                        status="ok",
                        timestamp=datetime.fromtimestamp(
                            bk.get("timestamp", {}).get("start", 0), tz=UTC
                        ),
                    )
                    backups.append(meta)
                except (KeyError, TypeError, ValueError):
                    continue

        return backups

    async def prune_old_backups(self) -> int:
        """Run ``pgbackrest expire`` to remove backups beyond retention policy."""
        await self.initialize()

        cmd = [
            "pgbackrest",
            f"--stanza={self.stanza}",
            f"--repo1-path={self.repo_path}",
            "expire",
        ]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            count = (result.stdout + result.stderr).count("remove")
            _log.info("pgbackrest.pruned", count=count)
            return count
        except Exception as exc:
            _log.error("pgbackrest.prune_failed", error=str(exc))
            return 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_backup_id(self, output: str) -> str:
        """Parse backup label from pgBackRest log output."""
        match = re.search(r"backup stop archive = ([0-9A-Fa-f\-]+)", output)
        if match:
            return match.group(1)
        # Fallback: generate a timestamp-based ID.
        return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    def _get_backup_size_mb(self, backup_id: str) -> float:
        manifest = self.repo_path / self.stanza / f"backup/{backup_id}/manifest"
        if manifest.exists():
            try:
                with manifest.open() as fh:
                    data = json.load(fh)
                return data.get("backup", {}).get("repo", {}).get("size", 0) / 1024 / 1024
            except Exception:
                return 0.0
        return 0.0

    def _compute_backup_checksum(self, backup_id: str) -> str:
        catalog = self.repo_path / self.stanza / f"backup/{backup_id}/backup.manifest"
        if catalog.exists():
            sha256 = hashlib.sha256()
            with catalog.open("rb") as fh:
                sha256.update(fh.read())
            return sha256.hexdigest()
        return "unknown"

    async def _ensure_minio_bucket(self) -> None:
        """Create MinIO backup bucket if it does not exist.

        P1-4 fix (audit 2026-07-02): the old call ``S3StorageBackend()`` passed
        none of the four required kwargs and invoked a non-existent
        ``ensure_bucket`` method, so it always fell into the except and the
        bucket was never actually ensured. The backend ensures the bucket in its
        constructor, so building it correctly is sufficient.
        """
        try:
            from aegis.config import settings as _settings
            from aegis.datalake.storage import S3StorageBackend  # optional dep

            mc = _settings().minio
            scheme = "https" if mc.secure else "http"

            def _build() -> None:
                S3StorageBackend(
                    bucket=self._minio_bucket,
                    endpoint_url=f"{scheme}://{mc.endpoint}",
                    access_key=mc.access_key.get_secret_value(),
                    secret_key=mc.secret_key.get_secret_value(),
                    region=mc.region,
                    use_ssl=mc.secure,
                )  # constructor calls _ensure_bucket()

            await asyncio.to_thread(_build)
        except Exception as exc:
            _log.warning("pgbackrest.minio_bucket_check_skipped", reason=str(exc))

    async def _store_backup_metadata(self, metadata: BackupMetadata) -> None:
        """Persist backup metadata JSON to MinIO for cross-machine DR."""
        try:
            import io

            from aegis.config import settings as get_settings

            cfg = get_settings()
            try:
                import aiobotocore.session  # type: ignore[import-untyped]
            except ImportError:
                _log.debug("pgbackrest.metadata_store_skipped", reason="aiobotocore absent")
                return

            data = json.dumps(metadata.to_dict(), ensure_ascii=False).encode()
            key = f"backup-metadata/{metadata.backup_id}.json"

            session = aiobotocore.session.get_session()
            async with session.create_client(
                "s3",
                endpoint_url=f"http{'s' if cfg.minio.secure else ''}://{cfg.minio.endpoint}",
                aws_access_key_id=cfg.minio.access_key.get_secret_value(),
                aws_secret_access_key=cfg.minio.secret_key.get_secret_value(),
            ) as client:
                await client.put_object(
                    Bucket=self._minio_bucket,
                    Key=key,
                    Body=io.BytesIO(data),
                    Metadata={"checksum": metadata.checksum},
                )

        except Exception as exc:
            raise BackupMetadataStoreError(
                f"Failed to store backup metadata: {exc}",
                code="AEGIS-BACKUP-0012",
            ) from exc

    async def _verify_restore(self, target_db: str) -> bool:
        """Run a simple sanity query on the restored database."""
        try:
            import asyncpg  # type: ignore[import-untyped]

            from aegis.config import settings as get_settings

            dsn = get_settings().pg_dsn.get_secret_value()
            # Replace the DB name with target_db for the verify connection.
            if "/" in dsn:
                parts = dsn.rsplit("/", 1)
                dsn = f"{parts[0]}/{target_db}"

            conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=10)
            try:
                row = await conn.fetchval(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
                )
                return int(row) > 0
            finally:
                await conn.close()
        except Exception as exc:
            _log.error("pgbackrest.restore_verify_failed", error=str(exc))
            return False
