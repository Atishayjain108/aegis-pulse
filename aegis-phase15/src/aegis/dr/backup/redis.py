"""
aegis.dr.backup.redis
=====================
Redis backup engine for Phase 15 — Disaster Recovery.

Strategy
--------
* Issues ``BGSAVE`` command; polls ``LASTSAVE`` until it increments.
* Copies the resulting RDB file from the Redis container (or local path) to MinIO.
* Falls back to ``SAVE`` (blocking) if ``BGSAVE`` fails.
* Uploads to ``aegis-dr/redis/dt=YYYY-MM-DD/<timestamp>_<sha256>.rdb``.

Failure modes handled
---------------------
* BGSAVE timeout    → RedisBackupError (AEGIS-DR-0002)
* RDB not readable  → RedisBackupError
* Upload failure    → MinioBackupError (AEGIS-DR-0003)

Architecture
-----------
Integrates with Phase 1 Redis (``aegis-redis`` on port 6380 in Docker Compose).
Uses ``redis.asyncio`` client, same library already in aegis root deps.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.constants import (
    REDIS_BGSAVE_POLL_INTERVAL_S,
    REDIS_BGSAVE_TIMEOUT_S,
    REDIS_PREFIX,
)
from aegis.dr.errors import MinioBackupError, RedisBackupError
from aegis.dr.schemas import BackupManifest, BackupStatus, BackupTarget

_log = structlog.get_logger("aegis.dr.backup.redis")


class RedisBackup:
    """
    Backs up Redis RDB snapshot to MinIO.

    Parameters
    ----------
    settings:
        DR settings singleton.
    minio_client:
        A pre-initialized ``minio.Minio`` client.
    redis_client:
        An async redis client (``redis.asyncio.Redis``).
    """

    def __init__(
        self,
        *,
        settings: DisasterRecoverySettings,
        minio_client: Any,
        redis_client: Any,
    ) -> None:
        self._settings = settings
        self._minio = minio_client
        self._redis = redis_client

    async def run(self) -> BackupManifest:
        """
        Execute one Redis backup cycle: BGSAVE → wait → upload.

        Returns
        -------
        BackupManifest

        Raises
        ------
        RedisBackupError
            If BGSAVE fails or times out.
        MinioBackupError
            If the MinIO upload fails.
        """
        started_at = datetime.now(UTC)

        _log.info("redis.backup.start")

        # Trigger background save
        await self._trigger_bgsave()

        # Wait for save to complete
        rdb_path = await self._wait_for_rdb()

        # Checksum + upload
        checksum = await asyncio.to_thread(self._sha256, rdb_path)
        size_bytes = rdb_path.stat().st_size
        minio_path = self._object_path(started_at, checksum)

        try:
            await asyncio.to_thread(
                self._upload_to_minio,
                rdb_path,
                minio_path,
                size_bytes,
            )
        except Exception as exc:
            raise MinioBackupError(
                "MinIO upload of Redis RDB failed",
                context={"path": minio_path, "error": str(exc)},
            ) from exc

        finished_at = datetime.now(UTC)
        manifest = BackupManifest(
            target=BackupTarget.REDIS,
            status=BackupStatus.SUCCESS,
            started_at=started_at,
            finished_at=finished_at,
            size_bytes=size_bytes,
            checksum_sha256=checksum,
            minio_path=minio_path,
        )

        await asyncio.to_thread(self._write_manifest, manifest)

        _log.info(
            "redis.backup.success",
            duration_s=manifest.duration_s,
            size_bytes=size_bytes,
            minio_path=minio_path,
        )

        return manifest

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _trigger_bgsave(self) -> None:
        """Send BGSAVE command; falls back to blocking SAVE on error."""
        try:
            result = await self._redis.bgsave()
            _log.debug("redis.bgsave.triggered", result=str(result))
        except Exception as exc:
            _log.warning("redis.bgsave.failed_falling_back_to_save", error=str(exc))
            try:
                await self._redis.save()
            except Exception as exc2:
                raise RedisBackupError(
                    "Both BGSAVE and SAVE failed",
                    context={"bgsave_error": str(exc), "save_error": str(exc2)},
                ) from exc2

    async def _wait_for_rdb(self) -> Path:
        """
        Poll LASTSAVE until it advances past the pre-backup timestamp.
        Returns the local path to the RDB file.
        """
        pre_save = await self._redis.lastsave()
        deadline = time.monotonic() + REDIS_BGSAVE_TIMEOUT_S

        while time.monotonic() < deadline:
            await asyncio.sleep(REDIS_BGSAVE_POLL_INTERVAL_S)
            last_save = await self._redis.lastsave()
            if last_save > pre_save:
                break
        else:
            raise RedisBackupError(
                f"BGSAVE did not complete within {REDIS_BGSAVE_TIMEOUT_S}s",
                context={"pre_save": str(pre_save)},
            )

        rdb_path = Path(self._settings.redis_rdb_path).expanduser()
        if not rdb_path.exists():
            raise RedisBackupError(
                f"RDB file not found at {rdb_path}",
                context={"rdb_path": str(rdb_path)},
            )

        return rdb_path

    def _upload_to_minio(
        self,
        local_path: Path,
        object_name: str,
        size_bytes: int,
    ) -> None:
        self._minio.fput_object(
            self._settings.dr_bucket,
            object_name,
            str(local_path),
            content_type="application/octet-stream",
            metadata={"x-aegis-target": "redis"},
        )

    def _write_manifest(self, manifest: BackupManifest) -> None:
        key = manifest.minio_path.rsplit(".", 1)[0] + "_manifest.json"
        data = manifest.model_dump_json().encode()
        self._minio.put_object(
            self._settings.dr_bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/json",
        )

    def _object_path(self, ts: datetime, checksum: str) -> str:
        date_str = ts.strftime("%Y-%m-%d")
        ts_str = ts.strftime("%Y%m%dT%H%M%SZ")
        short_hash = checksum[:12]
        return f"{REDIS_PREFIX}/dt={date_str}/{ts_str}_{short_hash}.rdb"

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
