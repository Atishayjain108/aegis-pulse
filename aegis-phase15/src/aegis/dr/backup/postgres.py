"""
aegis.dr.backup.postgres
========================
PostgreSQL backup engine for Phase 15 — Disaster Recovery.

Strategy
--------
* Uses ``pg_dump -Fc`` (custom binary format) — supports parallel restore,
  is smaller than plain SQL, and preserves all object metadata.
* Uploads to MinIO under ``aegis-dr/postgres/dt=YYYY-MM-DD/<timestamp>_<sha256>.dump``.
* Writes a ``BackupManifest`` to MinIO as a companion ``.json`` file.
* RPO target: 15 minutes — caller (orchestrator) is responsible for scheduling.

Failure modes handled
---------------------
* pg_dump non-zero exit        → PostgresBackupError (AEGIS-DR-0001)
* MinIO upload failure         → MinioBackupError (AEGIS-DR-0003)
* Timeout exceeded             → PostgresBackupError with context
* Row-count verification fail  → logs WARNING (non-fatal; manifest still written)

Architecture
-----------
No LangGraph / agent imports. Integrates with Phase 1 TimescaleDB via pg_dump.
Uses asyncio subprocess for non-blocking execution.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.constants import (
    PG_DUMP_FORMAT,
    PG_DUMP_TIMEOUT_S,
    PG_PREFIX,
    PG_VERIFY_QUERY,
)
from aegis.dr.errors import MinioBackupError, PostgresBackupError
from aegis.dr.schemas import BackupManifest, BackupStatus, BackupTarget

_log = structlog.get_logger("aegis.dr.backup.postgres")


class PostgresBackup:
    """
    Backs up TimescaleDB/Postgres to MinIO using pg_dump.

    Parameters
    ----------
    settings:
        DR settings singleton.
    minio_client:
        A pre-initialized ``minio.Minio`` client (injected to allow testing).
    """

    def __init__(
        self,
        *,
        settings: DisasterRecoverySettings,
        minio_client: Any,
    ) -> None:
        self._settings = settings
        self._minio = minio_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> BackupManifest:
        """
        Execute one full pg_dump backup cycle.

        Returns
        -------
        BackupManifest
            Immutable record of the completed (or failed) backup.

        Raises
        ------
        PostgresBackupError
            If pg_dump fails or times out.
        MinioBackupError
            If the upload to MinIO fails.
        """
        started_at = datetime.now(UTC)
        run_id = uuid4().hex[:8]

        _log.info(
            "pg.backup.start",
            run_id=run_id,
            pg_dsn=self._redacted_dsn(),
        )

        with tempfile.TemporaryDirectory(prefix="aegis_pg_backup_") as tmpdir:
            dump_path = Path(tmpdir) / f"aegis_{run_id}.dump"

            try:
                await self._pg_dump(dump_path)
            except PostgresBackupError:
                raise
            except Exception as exc:
                raise PostgresBackupError(
                    "pg_dump failed with unexpected error",
                    context={"run_id": run_id, "error": str(exc)},
                ) from exc

            checksum = self._sha256(dump_path)
            size_bytes = dump_path.stat().st_size
            minio_path = self._object_path(started_at, checksum)

            try:
                await asyncio.to_thread(
                    self._upload_to_minio,
                    dump_path,
                    minio_path,
                    size_bytes,
                )
            except Exception as exc:
                raise MinioBackupError(
                    "MinIO upload failed",
                    context={"run_id": run_id, "path": minio_path, "error": str(exc)},
                ) from exc

            row_count = await self._verify_row_count()
            lsn = await self._current_lsn()

            finished_at = datetime.now(UTC)
            manifest = BackupManifest(
                target=BackupTarget.POSTGRES,
                status=BackupStatus.SUCCESS,
                started_at=started_at,
                finished_at=finished_at,
                size_bytes=size_bytes,
                checksum_sha256=checksum,
                minio_path=minio_path,
                pg_lsn=lsn,
                row_count=row_count,
            )

            await asyncio.to_thread(self._write_manifest, manifest)

            _log.info(
                "pg.backup.success",
                run_id=run_id,
                duration_s=manifest.duration_s,
                size_bytes=size_bytes,
                minio_path=minio_path,
                row_count=row_count,
                lsn=lsn,
            )

            return manifest

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _pg_dump(self, dest: Path) -> None:
        """Run pg_dump as an async subprocess."""
        if not shutil.which("pg_dump"):
            raise PostgresBackupError(
                "pg_dump not found in PATH — install postgresql-client",
                context={"hint": "apt-get install -y postgresql-client"},
            )

        cmd = [
            "pg_dump",
            f"--format={PG_DUMP_FORMAT}",
            f"--compress={self._settings.pg_dump_timeout_s}",
            "--no-password",
            f"--file={dest}",
            self._settings.pg_dsn,
        ]
        # Compress flag is actually an integer level, not timeout — fix:
        cmd = [
            "pg_dump",
            f"--format={PG_DUMP_FORMAT}",
            "--no-password",
            f"--file={dest}",
            self._settings.pg_dsn,
        ]

        timeout = self._settings.pg_dump_timeout_s or PG_DUMP_TIMEOUT_S

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            raise PostgresBackupError(
                f"pg_dump timed out after {timeout}s",
                context={"timeout_s": timeout},
            ) from exc

        if proc.returncode != 0:
            raise PostgresBackupError(
                "pg_dump exited non-zero",
                context={
                    "returncode": proc.returncode,
                    "stderr": stderr.decode(errors="replace")[:2000],
                },
            )

    def _upload_to_minio(
        self,
        local_path: Path,
        object_name: str,
        size_bytes: int,
    ) -> None:
        """Upload dump file to MinIO (blocking — run in thread executor)."""
        self._minio.fput_object(
            self._settings.dr_bucket,
            object_name,
            str(local_path),
            content_type="application/octet-stream",
            metadata={"x-aegis-target": "postgres", "x-aegis-checksum": "sha256"},
        )

    def _write_manifest(self, manifest: BackupManifest) -> None:
        """Write manifest JSON to MinIO alongside the dump."""
        manifest_key = (
            manifest.minio_path.rsplit(".", 1)[0] + "_manifest.json"
        )
        data = manifest.model_dump_json().encode()
        self._minio.put_object(
            self._settings.dr_bucket,
            manifest_key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/json",
        )

    def _object_path(self, ts: datetime, checksum: str) -> str:
        date_str = ts.strftime("%Y-%m-%d")
        ts_str = ts.strftime("%Y%m%dT%H%M%SZ")
        short_hash = checksum[:12]
        return f"{PG_PREFIX}/dt={date_str}/{ts_str}_{short_hash}.dump"

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def _redacted_dsn(self) -> str:
        import re
        return re.sub(r"://([^:@]+):([^@]+)@", r"://\1:***@", self._settings.pg_dsn)

    async def _verify_row_count(self) -> int | None:
        """Count rows in `signals` table — non-fatal, best-effort."""
        try:
            import asyncpg  # type: ignore[import-untyped]

            conn = await asyncpg.connect(
                self._settings.pg_dsn,
                timeout=10,
                command_timeout=10,
            )
            try:
                row = await conn.fetchrow(PG_VERIFY_QUERY)
                return int(row[0]) if row else None
            finally:
                await conn.close()
        except Exception as exc:
            _log.warning("pg.backup.verify_count.failed", error=str(exc))
            return None

    async def _current_lsn(self) -> str | None:
        """Fetch current WAL LSN — useful for point-in-time recovery planning."""
        try:
            import asyncpg  # type: ignore[import-untyped]

            conn = await asyncpg.connect(self._settings.pg_dsn, timeout=10)
            try:
                row = await conn.fetchrow("SELECT pg_current_wal_lsn()::text")
                return str(row[0]) if row else None
            finally:
                await conn.close()
        except Exception as exc:
            _log.warning("pg.backup.lsn.failed", error=str(exc))
            return None
