"""
tests/test_dr_backup_postgres.py
=================================
Unit tests for aegis.dr.backup.postgres — Phase 15.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.dr.backup.postgres import PostgresBackup
from aegis.dr.config import DisasterRecoverySettings
from aegis.dr.errors import MinioBackupError, PostgresBackupError
from aegis.dr.schemas import BackupStatus


def _settings(**kwargs: object) -> DisasterRecoverySettings:
    base: dict[str, object] = {"pg_dump_timeout_s": 30}
    base.update(kwargs)
    return DisasterRecoverySettings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio()
async def test_backup_success() -> None:
    """Happy path: pg_dump succeeds, upload succeeds, manifest returned."""
    settings = _settings()
    minio = MagicMock()
    minio.fput_object.return_value = None
    minio.put_object.return_value = None

    # Write a fake dump file when pg_dump subprocess is called
    async def _fake_pg_dump(self_inner: object, path: Path) -> None:
        path.write_bytes(b"PGDMP" + b"\x00" * 1024)

    with (
        patch.object(PostgresBackup, "_pg_dump", new=_fake_pg_dump),
        patch.object(
            PostgresBackup, "_verify_row_count", new_callable=AsyncMock, return_value=42
        ),
        patch.object(
            PostgresBackup, "_current_lsn", new_callable=AsyncMock, return_value="0/ABC"
        ),
    ):
        backup = PostgresBackup(settings=settings, minio_client=minio)
        manifest = await backup.run()

    assert manifest.status == BackupStatus.SUCCESS
    assert manifest.row_count == 42
    assert manifest.pg_lsn == "0/ABC"
    assert manifest.size_bytes > 0
    assert len(manifest.checksum_sha256) == 64
    minio.fput_object.assert_called_once()
    minio.put_object.assert_called_once()  # manifest JSON


@pytest.mark.asyncio()
async def test_backup_fails_when_pg_dump_raises() -> None:
    """PostgresBackupError propagates when pg_dump fails."""
    settings = _settings()
    minio = MagicMock()

    async def _failing_pg_dump(self_inner: object, path: Path) -> None:
        raise PostgresBackupError("pg_dump failed with unexpected error")

    with patch.object(PostgresBackup, "_pg_dump", new=_failing_pg_dump):
        backup = PostgresBackup(settings=settings, minio_client=minio)
        with pytest.raises(PostgresBackupError, match="pg_dump failed"):
            await backup.run()

    minio.fput_object.assert_not_called()


@pytest.mark.asyncio()
async def test_backup_fails_when_minio_upload_fails() -> None:
    """MinioBackupError raised when upload to MinIO fails."""
    settings = _settings()
    minio = MagicMock()
    minio.fput_object.side_effect = Exception("connection refused")
    minio.put_object.return_value = None

    async def _fake_pg_dump2(self_inner: object, path: Path) -> None:
        path.write_bytes(b"PGDMP" + b"\x00" * 512)

    with (
        patch.object(PostgresBackup, "_pg_dump", new=_fake_pg_dump2),
        patch.object(
            PostgresBackup, "_verify_row_count", new_callable=AsyncMock, return_value=None
        ),
        patch.object(
            PostgresBackup, "_current_lsn", new_callable=AsyncMock, return_value=None
        ),
    ):
        backup = PostgresBackup(settings=settings, minio_client=minio)
        with pytest.raises(MinioBackupError):
            await backup.run()


def test_object_path_format() -> None:
    """Object path must follow expected storage layout."""
    settings = _settings()
    backup = PostgresBackup(settings=settings, minio_client=MagicMock())
    ts = datetime(2026, 5, 27, 9, 30, 0, tzinfo=UTC)
    checksum = "abcdef1234567890" * 4
    path = backup._object_path(ts, checksum)
    assert path.startswith("postgres/dt=2026-05-27/")
    assert "20260527T093000Z" in path
    assert checksum[:12] in path
    assert path.endswith(".dump")


def test_redacted_dsn_hides_password() -> None:
    import os
    old = os.environ.get("AEGIS_DR_PG_DSN")
    os.environ["AEGIS_DR_PG_DSN"] = "postgresql://user:supersecret@localhost:5433/mydb"
    # Clear the lru_cache so new env var is picked up
    from aegis.dr import config as dr_config
    dr_config.get_dr_settings.cache_clear()
    settings = dr_config.get_dr_settings()
    backup = PostgresBackup(settings=settings, minio_client=MagicMock())
    redacted = backup._redacted_dsn()
    # Restore
    if old:
        os.environ["AEGIS_DR_PG_DSN"] = old
    else:
        del os.environ["AEGIS_DR_PG_DSN"]
    dr_config.get_dr_settings.cache_clear()
    assert "supersecret" not in redacted
    assert "***" in redacted
    assert "localhost" in redacted


@pytest.mark.asyncio()
async def test_pg_dump_raises_when_binary_missing() -> None:
    """If pg_dump binary is not in PATH, raise descriptive error."""
    settings = _settings()
    backup = PostgresBackup(settings=settings, minio_client=MagicMock())

    with (
        patch("shutil.which", return_value=None),
        pytest.raises(PostgresBackupError, match="pg_dump not found"),
    ):
        await backup._pg_dump(Path("/tmp/test.dump"))  # noqa: S108
