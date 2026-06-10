"""
aegis.dr.restore.postgres
=========================
PostgreSQL restore engine for Phase 15 — Disaster Recovery.

Strategy
--------
* Downloads the latest (or specified) pg_dump from MinIO.
* Creates the target database if it doesn't exist.
* Runs ``pg_restore -Fc -j 4`` for parallel restore.
* Verifies restore via row count comparison against manifest.
* Supports dry-run mode (download + verify checksum only, no restore).

Use Cases
---------
1. Weekly automated drill against ``aegis_drill`` throwaway DB.
2. Manual disaster recovery against a new/wiped DB.

Architecture
-----------
Subprocess-based for pg_restore. asyncio non-blocking.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.constants import (
    DRILL_PG_ROW_TOLERANCE,
    PG_PREFIX,
    PG_RESTORE_JOBS,
    PG_VERIFY_QUERY,
)
from aegis.dr.errors import (
    ManifestCorruptError,
    NoBackupFoundError,
    PostgresRestoreError,
    VerificationError,
)
from aegis.dr.schemas import (
    BackupManifest,
    BackupTarget,
    RestoreResult,
    RestoreStatus,
)

_log = structlog.get_logger("aegis.dr.restore.postgres")


class PostgresRestore:
    """
    Restores a Postgres database from a MinIO pg_dump backup.

    Parameters
    ----------
    settings:
        DR settings singleton.
    minio_client:
        Pre-initialized ``minio.Minio`` client.
    target_dsn:
        DSN of the database to restore INTO. Defaults to ``settings.pg_dsn``.
        For drills, pass ``settings.drill_pg_dsn`` to avoid clobbering prod.
    dry_run:
        If ``True``, download + checksum-verify only; skip actual restore.
    """

    def __init__(
        self,
        *,
        settings: DisasterRecoverySettings,
        minio_client: Any,
        target_dsn: str | None = None,
        dry_run: bool = False,
    ) -> None:
        self._settings = settings
        self._minio = minio_client
        self._target_dsn = target_dsn or settings.pg_dsn
        self._dry_run = dry_run

    async def run(
        self, *, manifest: BackupManifest | None = None
    ) -> RestoreResult:
        """
        Execute a restore from the latest backup (or specified manifest).

        Parameters
        ----------
        manifest:
            If provided, restore from this specific backup.
            If ``None``, discover and use the most recent SUCCESS manifest.

        Returns
        -------
        RestoreResult

        Raises
        ------
        NoBackupFoundError
            If no suitable backup can be found.
        PostgresRestoreError
            If pg_restore fails.
        VerificationError
            If post-restore row counts diverge beyond tolerance.
        """
        started_at = datetime.now(UTC)

        if manifest is None:
            manifest = await asyncio.to_thread(self._latest_manifest)

        if manifest is None:
            raise NoBackupFoundError(
                "No successful Postgres backup found in MinIO",
                context={"bucket": self._settings.dr_bucket, "prefix": PG_PREFIX},
            )

        _log.info(
            "postgres.restore.start",
            manifest_id=str(manifest.manifest_id),
            minio_path=manifest.minio_path,
            dry_run=self._dry_run,
        )

        with tempfile.TemporaryDirectory(prefix="aegis_pg_restore_") as tmpdir:
            dump_path = Path(tmpdir) / "restore.dump"

            # Download from MinIO
            await asyncio.to_thread(
                self._minio.fget_object,
                self._settings.dr_bucket,
                manifest.minio_path,
                str(dump_path),
            )

            # Verify checksum
            actual_checksum = await asyncio.to_thread(self._sha256, dump_path)
            if actual_checksum != manifest.checksum_sha256:
                raise ManifestCorruptError(
                    "Downloaded dump checksum mismatch",
                    context={
                        "expected": manifest.checksum_sha256,
                        "actual": actual_checksum,
                    },
                )

            if self._dry_run:
                _log.info("postgres.restore.dry_run_ok", checksum=actual_checksum)
                finished_at = datetime.now(UTC)
                return RestoreResult(
                    target=BackupTarget.POSTGRES,
                    status=RestoreStatus.SUCCESS,
                    manifest_id=manifest.manifest_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    verification_passed=True,
                )

            # Create DB if needed
            await self._ensure_db_exists()

            # Run pg_restore
            await self._pg_restore(dump_path)

        # Verify row count
        rows_restored = await self._count_rows()
        verification_passed = self._verify_rows(
            rows_restored, manifest.row_count
        )

        if not verification_passed:
            raise VerificationError(
                "Post-restore row count diverges beyond tolerance",
                context={
                    "expected_approx": manifest.row_count,
                    "actual": rows_restored,
                    "tolerance": DRILL_PG_ROW_TOLERANCE,
                },
            )

        finished_at = datetime.now(UTC)
        result = RestoreResult(
            target=BackupTarget.POSTGRES,
            status=RestoreStatus.SUCCESS,
            manifest_id=manifest.manifest_id,
            started_at=started_at,
            finished_at=finished_at,
            rows_restored=rows_restored,
            verification_passed=True,
        )

        _log.info(
            "postgres.restore.success",
            rows_restored=rows_restored,
            duration_s=result.duration_s,
        )

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _latest_manifest(self) -> BackupManifest | None:
        """Scan MinIO for the most recent successful Postgres manifest."""

        objects = sorted(
            self._minio.list_objects(
                self._settings.dr_bucket,
                prefix=PG_PREFIX + "/",
                recursive=True,
            ),
            key=lambda o: o.last_modified or datetime.min,
            reverse=True,
        )

        for obj in objects:
            if not obj.object_name.endswith("_manifest.json"):
                continue
            try:
                response = self._minio.get_object(
                    self._settings.dr_bucket, obj.object_name
                )
                data = response.read()
                m = BackupManifest.model_validate_json(data)
                if m.status.value == "success":
                    return m
            except Exception as exc:
                _log.warning(
                    "postgres.restore.manifest_read_error",
                    obj=obj.object_name,
                    error=str(exc),
                )
                continue

        return None

    async def _ensure_db_exists(self) -> None:
        """Create the target database if it doesn't exist."""
        try:
            import asyncpg

            parsed = urllib.parse.urlparse(self._target_dsn)
            db_name = parsed.path.lstrip("/")
            admin_dsn = self._target_dsn.replace(f"/{db_name}", "/postgres")

            conn = await asyncpg.connect(admin_dsn, timeout=10)
            try:
                exists = await conn.fetchval(
                    "SELECT 1 FROM pg_database WHERE datname=$1", db_name
                )
                if not exists:
                    await conn.execute(
                        f'CREATE DATABASE "{db_name}" OWNER aegis_app'
                    )
                    _log.info("postgres.restore.db_created", db=db_name)
            finally:
                await conn.close()
        except Exception as exc:
            _log.warning("postgres.restore.ensure_db_failed", error=str(exc))

    async def _pg_restore(self, dump_path: Path) -> None:
        cmd = [
            "pg_restore",
            f"--jobs={PG_RESTORE_JOBS}",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            f"--dbname={self._target_dsn}",
            str(dump_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=1800)
        except TimeoutError as exc:
            proc.kill()
            raise PostgresRestoreError(
                "pg_restore timed out",
                context={"dump_path": str(dump_path)},
            ) from exc

        if proc.returncode != 0:
            raise PostgresRestoreError(
                "pg_restore exited non-zero",
                context={
                    "returncode": proc.returncode,
                    "stderr": stderr.decode(errors="replace")[:2000],
                },
            )

    async def _count_rows(self) -> int:
        try:
            import asyncpg

            conn = await asyncpg.connect(self._target_dsn, timeout=10, command_timeout=30)
            try:
                row = await conn.fetchrow(PG_VERIFY_QUERY)
                return int(row[0]) if row else 0
            finally:
                await conn.close()
        except Exception as exc:
            raise VerificationError(
                "Row-count query failed after restore",
                context={"error": str(exc)},
            ) from exc

    @staticmethod
    def _verify_rows(actual: int, expected: int | None) -> bool:
        if expected is None or expected == 0:
            return True  # No baseline to compare against
        delta = abs(actual - expected) / max(expected, 1)
        return delta <= DRILL_PG_ROW_TOLERANCE

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()


