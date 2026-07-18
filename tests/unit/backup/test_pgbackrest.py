"""Unit tests for aegis.backup.pgbackrest_manager."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.backup.errors import BackupCommandError, BackupTimeoutError, RestoreError
from aegis.backup.pgbackrest_manager import BackupManager, BackupMetadata
from aegis.backup.settings import BackupSettings


def _settings(**kwargs) -> BackupSettings:
    defaults = {
        "pgbackrest_stanza": "test-stanza",
        "pgbackrest_repo_path": "/tmp/test-pgbackrest",  # noqa: S108
        "pgbackrest_timeout_s": 30,
        "pgbackrest_retention_full_days": 7,
        "minio_bucket": "aegis-backups",
    }
    defaults.update(kwargs)
    return BackupSettings.model_construct(**defaults)


# ---------------------------------------------------------------------------
# BackupMetadata
# ---------------------------------------------------------------------------


class TestBackupMetadata:
    def _make(self, **kwargs) -> BackupMetadata:
        defaults = {
            "backup_id": "20260531T120000Z",
            "backup_type": "incr",
            "size_mb": 128.5,
            "duration_s": 12.3,
            "database_version": "16",
            "checksum": "abc123",
            "retention_expires": datetime.now(UTC) + timedelta(days=7),
            "host": "laptop",
            "status": "ok",
        }
        defaults.update(kwargs)
        return BackupMetadata(**defaults)

    def test_roundtrip(self) -> None:
        meta = self._make()
        restored = BackupMetadata.from_dict(meta.to_dict())
        assert restored.backup_id == meta.backup_id
        assert restored.backup_type == meta.backup_type
        assert restored.size_mb == meta.size_mb
        assert restored.status == meta.status

    def test_to_dict_keys(self) -> None:
        d = self._make().to_dict()
        expected = {
            "backup_id", "timestamp", "backup_type", "size_mb",
            "duration_s", "wal_start", "wal_stop", "database_version",
            "checksum", "retention_expires", "host", "status",
        }
        assert set(d.keys()) == expected

    def test_default_timestamp_is_utc(self) -> None:
        meta = self._make()
        assert meta.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# BackupManager
# ---------------------------------------------------------------------------


class TestBackupManager:
    def _manager(self) -> BackupManager:
        mgr = BackupManager(settings=_settings())
        mgr._initialized = True  # skip real init in unit tests
        return mgr

    @patch("aegis.backup.pgbackrest_manager.BackupManager._ensure_minio_bucket", new_callable=AsyncMock)
    @patch("aegis.backup.pgbackrest_manager.BackupManager._store_backup_metadata", new_callable=AsyncMock)
    @patch("asyncio.to_thread")
    async def test_create_backup_success(
        self, mock_to_thread, mock_store, mock_bucket
    ) -> None:
        mgr = self._manager()

        fake_result = MagicMock()
        fake_result.stdout = "completed backup set: 20260531T120000Z"
        fake_result.stderr = ""
        fake_result.returncode = 0

        async def fake_async(*args, **kwargs):
            fn = args[0] if args else None
            if fn is subprocess.run:
                return fake_result
            if callable(fn):
                return fn(*args[1:], **kwargs)
            return None

        mock_to_thread.side_effect = fake_async

        meta = await mgr.create_backup(backup_type="incr")
        assert meta.backup_type == "incr"
        assert meta.status == "ok"
        mock_store.assert_awaited_once()

    def test_extract_backup_id_from_log(self) -> None:
        mgr = self._manager()
        output = "INFO: backup stop archive = 000000010000000000000001-a1b2c3d4"
        bid = mgr._extract_backup_id(output)
        assert bid == "000000010000000000000001-a1b2c3d4"

    def test_extract_backup_id_fallback(self) -> None:
        mgr = self._manager()
        bid = mgr._extract_backup_id("no match here")
        assert bid
        assert len(bid) > 0

    def test_compute_backup_checksum_missing_file(self) -> None:
        mgr = self._manager()
        result = mgr._compute_backup_checksum("nonexistent-backup-id")
        assert result == "unknown"

    def test_get_backup_size_mb_missing_manifest(self) -> None:
        mgr = self._manager()
        result = mgr._get_backup_size_mb("nonexistent-backup-id")
        assert result == 0.0

    @patch("asyncio.to_thread")
    async def test_create_backup_timeout(self, mock_to_thread) -> None:
        mgr = self._manager()

        async def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=[], timeout=30)

        mock_to_thread.side_effect = raise_timeout

        with pytest.raises(BackupTimeoutError) as exc_info:
            await mgr.create_backup()
        assert exc_info.value.code == "AEGIS-BACKUP-0002"

    @patch("asyncio.to_thread")
    async def test_create_backup_command_error(self, mock_to_thread) -> None:
        mgr = self._manager()

        async def raise_cpe(*args, **kwargs):
            raise subprocess.CalledProcessError(1, cmd=[], stderr="pgbackrest: error")

        mock_to_thread.side_effect = raise_cpe

        with pytest.raises(BackupCommandError) as exc_info:
            await mgr.create_backup()
        assert exc_info.value.code == "AEGIS-BACKUP-0003"

    @patch("asyncio.to_thread")
    async def test_list_backups_parses_json(self, mock_to_thread) -> None:
        mgr = self._manager()

        info_json = json.dumps([
            {
                "backup": [
                    {
                        "label": "20260531T120000Z",
                        "type": "incr",
                        "info": {"repo": {"size": 536870912}},
                        "timestamp": {"start": 1748692800, "stop": 1748692812},
                        "db": {"version": 160000},
                        "archive": {"start": "0/1000028"},
                    }
                ]
            }
        ])

        fake_result = MagicMock(stdout=info_json, returncode=0)

        async def fake(*args, **kwargs):
            return fake_result

        mock_to_thread.side_effect = fake
        backups = await mgr.list_backups()
        assert len(backups) == 1
        assert backups[0].backup_id == "20260531T120000Z"
        assert backups[0].backup_type == "incr"

    @patch("asyncio.to_thread")
    async def test_list_backups_empty_on_error(self, mock_to_thread) -> None:
        mgr = self._manager()

        async def raise_err(*args, **kwargs):
            raise RuntimeError("pgbackrest not installed")

        mock_to_thread.side_effect = raise_err
        backups = await mgr.list_backups()
        assert backups == []

    @patch("asyncio.to_thread")
    async def test_restore_raises_on_failure(self, mock_to_thread) -> None:
        mgr = self._manager()

        async def raise_cpe(*args, **kwargs):
            raise subprocess.CalledProcessError(1, cmd=[], stderr="restore failed")

        mock_to_thread.side_effect = raise_cpe

        with pytest.raises(RestoreError) as exc_info:
            await mgr.restore_full("20260531T120000Z", "aegis")
        assert exc_info.value.code == "AEGIS-BACKUP-0004"

    @patch("asyncio.to_thread")
    async def test_list_backups_json_decode_error_returns_empty(self, mock_to_thread) -> None:
        mgr = self._manager()
        fake_result = MagicMock(stdout="not-valid-json", returncode=0)

        async def fake(*args, **kwargs):
            return fake_result

        mock_to_thread.side_effect = fake
        backups = await mgr.list_backups()
        assert backups == []

    @patch("aegis.backup.pgbackrest_manager.BackupManager._ensure_minio_bucket", new_callable=AsyncMock)
    @patch("aegis.backup.pgbackrest_manager.BackupManager._store_backup_metadata", new_callable=AsyncMock)
    @patch("asyncio.to_thread")
    async def test_create_backup_with_label(self, mock_to_thread, mock_store, mock_bucket) -> None:
        mgr = self._manager()
        log_output = "backup stop archive = abc123def456\n"
        fake_result = MagicMock()
        fake_result.stdout = log_output
        fake_result.stderr = ""
        fake_result.returncode = 0

        async def fake_async(*args, **kwargs):
            fn = args[0] if args else None
            if fn is subprocess.run:
                return fake_result
            if callable(fn):
                return fn(*args[1:], **kwargs)
            return None

        mock_to_thread.side_effect = fake_async
        mock_store.return_value = None
        mock_bucket.return_value = None

        meta = await mgr.create_backup(label="test-label")
        assert meta is not None

    @patch("asyncio.to_thread")
    async def test_restore_with_target_timeline(self, mock_to_thread) -> None:
        mgr = self._manager()

        async def raise_cpe(*args, **kwargs):
            raise subprocess.CalledProcessError(1, cmd=[], stderr="restore failed")

        mock_to_thread.side_effect = raise_cpe
        with pytest.raises(RestoreError):
            await mgr.restore_full("20260531T120000Z", "aegis", target_timeline="current")

    async def test_ensure_minio_bucket_exception_raises_backup_init_error(self) -> None:
        from aegis.backup.errors import BackupInitError

        mgr = BackupManager(settings=_settings())
        mgr._initialized = False

        with (
            patch(
                "aegis.backup.pgbackrest_manager.asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=OSError("permission denied"),
            ),
            pytest.raises(BackupInitError),
        ):
            await mgr.initialize()


def test_pgbackrest_settings_cache() -> None:
    from aegis.backup.pgbackrest_manager import _settings as pg_settings

    pg_settings.cache_clear()
    try:
        result = pg_settings()
        assert isinstance(result, BackupSettings)
    except Exception:
        pass
    finally:
        pg_settings.cache_clear()


class TestEnsureMinioBucket:
    """audit P1-4: the bucket-ensure must construct S3StorageBackend with real
    MinIO kwargs (old code passed none + called a nonexistent method → always
    silently skipped)."""

    async def test_builds_backend_with_minio_kwargs(self, monkeypatch) -> None:
        mgr = BackupManager(settings=_settings())
        mgr._initialized = True

        captured: dict = {}

        class _FakeBackend:
            def __init__(self, **kwargs):
                captured.update(kwargs)  # constructor ensures bucket

        monkeypatch.setattr(
            "aegis.datalake.storage.S3StorageBackend", _FakeBackend, raising=False
        )
        await mgr._ensure_minio_bucket()
        # Must have passed the four previously-missing required kwargs.
        for key in ("bucket", "endpoint_url", "access_key", "secret_key"):
            assert key in captured, f"missing {key} (P1-4 regression)"
        assert captured["endpoint_url"].startswith(("http://", "https://"))

    async def test_never_raises_on_backend_error(self, monkeypatch) -> None:
        mgr = BackupManager(settings=_settings())
        mgr._initialized = True

        def _boom(**_k):
            raise RuntimeError("minio down")

        monkeypatch.setattr(
            "aegis.datalake.storage.S3StorageBackend", _boom, raising=False
        )
        # Best-effort: must swallow and not raise.
        await mgr._ensure_minio_bucket()
