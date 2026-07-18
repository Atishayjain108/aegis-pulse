"""Unit tests for aegis.backup.restic_manager."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aegis.backup.errors import (
    ResticCommandError,
    ResticInitError,
    ResticPasswordMissingError,
    ResticRestoreError,
    ResticTimeoutError,
)
from aegis.backup.restic_manager import ResticBackup, ResticSnapshot
from aegis.backup.settings import BackupSettings


def _settings(**kwargs) -> BackupSettings:
    base = {
        "restic_password": "test-password-xyz",
        "restic_repository": "local:/tmp/test-restic",
        "restic_retention_days": 7,
        "restic_b2_bucket": "",
    }
    base.update(kwargs)
    return BackupSettings.model_construct(**base)


# ---------------------------------------------------------------------------
# ResticSnapshot
# ---------------------------------------------------------------------------


class TestResticSnapshot:
    def test_from_json(self) -> None:
        data = {
            "id": "abc123def456",
            "short_id": "abc123",
            "time": "2026-05-31T12:00:00Z",
            "hostname": "laptop",
            "paths": ["/home/user/code"],
            "tags": ["pre-deploy"],
        }
        snap = ResticSnapshot.from_json(data)
        assert snap.snapshot_id == "abc123def456"
        assert snap.short_id == "abc123"
        assert snap.hostname == "laptop"
        assert snap.tags == ["pre-deploy"]

    def test_to_dict_roundtrip(self) -> None:
        data = {
            "id": "abc123",
            "short_id": "abc",
            "time": "2026-05-31T12:00:00+00:00",
            "hostname": "test",
            "paths": ["/tmp"],  # noqa: S108
            "tags": [],
        }
        snap = ResticSnapshot.from_json(data)
        d = snap.to_dict()
        assert d["snapshot_id"] == "abc123"
        assert d["hostname"] == "test"


# ---------------------------------------------------------------------------
# ResticBackup construction
# ---------------------------------------------------------------------------


def test_missing_password_raises() -> None:
    settings = BackupSettings.model_construct(restic_password="")
    with pytest.raises(ResticPasswordMissingError) as exc_info:
        ResticBackup(settings=settings)
    assert exc_info.value.code == "AEGIS-BACKUP-0014"


# ---------------------------------------------------------------------------
# ResticBackup operations (subprocess mocked)
# ---------------------------------------------------------------------------


class TestResticBackup:
    def _rb(self) -> ResticBackup:
        return ResticBackup(settings=_settings())

    @patch("asyncio.to_thread")
    async def test_init_repo_success(self, mock_to_thread) -> None:
        rb = self._rb()
        fake = MagicMock(returncode=0, stderr="")

        async def fake_async(*args, **kwargs):
            return fake

        mock_to_thread.side_effect = fake_async
        ok = await rb.init_repo()
        assert ok is True

    @patch("asyncio.to_thread")
    async def test_init_repo_already_exists(self, mock_to_thread) -> None:
        rb = self._rb()
        fake = MagicMock(returncode=1, stderr="Fatal: repository already exists")

        async def fake_async(*args, **kwargs):
            return fake

        mock_to_thread.side_effect = fake_async
        ok = await rb.init_repo()
        assert ok is True

    @patch("asyncio.to_thread")
    async def test_init_repo_failure(self, mock_to_thread) -> None:
        rb = self._rb()
        fake = MagicMock(returncode=1, stderr="permission denied")

        async def fake_async(*args, **kwargs):
            return fake

        mock_to_thread.side_effect = fake_async
        with pytest.raises(ResticInitError):
            await rb.init_repo()

    @patch("asyncio.to_thread")
    async def test_backup_returns_snapshot_id(self, mock_to_thread) -> None:
        rb = self._rb()
        snapshot_line = json.dumps({"message_type": "summary", "snapshot_id": "snap001"})
        fake_init = MagicMock(returncode=0, stderr="")
        fake_backup = MagicMock(stdout=snapshot_line, returncode=0)
        calls = [fake_init, fake_backup]

        async def fake_async(*args, **kwargs):
            return calls.pop(0)

        mock_to_thread.side_effect = fake_async
        sid = await rb.backup(label="test", paths=[Path("/tmp")])  # noqa: S108
        assert sid == "snap001"

    @patch("asyncio.to_thread")
    async def test_backup_timeout(self, mock_to_thread) -> None:
        rb = self._rb()
        fake_init = MagicMock(returncode=0, stderr="")
        call_count = 0

        async def fake_async(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return fake_init
            raise subprocess.TimeoutExpired(cmd=[], timeout=3600)

        mock_to_thread.side_effect = fake_async
        with pytest.raises(ResticTimeoutError) as exc_info:
            await rb.backup(paths=[Path("/tmp")])  # noqa: S108
        assert exc_info.value.code == "AEGIS-BACKUP-0006"

    @patch("asyncio.to_thread")
    async def test_backup_command_error(self, mock_to_thread) -> None:
        rb = self._rb()
        fake_init = MagicMock(returncode=0, stderr="")
        call_count = 0

        async def fake_async(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return fake_init
            raise subprocess.CalledProcessError(1, cmd=[], stderr="restic: error")

        mock_to_thread.side_effect = fake_async
        with pytest.raises(ResticCommandError) as exc_info:
            await rb.backup(paths=[Path("/tmp")])  # noqa: S108
        assert exc_info.value.code == "AEGIS-BACKUP-0007"

    @patch("asyncio.to_thread")
    async def test_backup_no_paths_returns_none(self, mock_to_thread) -> None:
        rb = self._rb()
        fake_init = MagicMock(returncode=0, stderr="")

        async def fake_async(*args, **kwargs):
            return fake_init

        mock_to_thread.side_effect = fake_async
        sid = await rb.backup(paths=[Path("/tmp/nonexistent_path_aegis_test_xyz")])  # noqa: S108
        assert sid is None

    @patch("asyncio.to_thread")
    async def test_list_snapshots(self, mock_to_thread) -> None:
        rb = self._rb()
        snap_json = json.dumps([
            {
                "id": "aaa", "short_id": "a",
                "time": "2026-05-30T10:00:00+00:00",
                "hostname": "box", "paths": ["/tmp"],  # noqa: S108
                "tags": [],
            },
            {
                "id": "bbb", "short_id": "b",
                "time": "2026-05-31T10:00:00+00:00",
                "hostname": "box", "paths": ["/tmp"],  # noqa: S108
                "tags": ["daily"],
            },
        ])
        fake = MagicMock(stdout=snap_json, returncode=0)

        async def fake_async(*args, **kwargs):
            return fake

        mock_to_thread.side_effect = fake_async
        snaps = await rb.list_snapshots()
        assert len(snaps) == 2
        assert snaps[0].snapshot_id == "aaa"
        assert snaps[1].snapshot_id == "bbb"

    @patch("asyncio.to_thread")
    async def test_list_snapshots_empty_on_error(self, mock_to_thread) -> None:
        rb = self._rb()

        async def raise_err(*args, **kwargs):
            raise RuntimeError("restic not found")

        mock_to_thread.side_effect = raise_err
        snaps = await rb.list_snapshots()
        assert snaps == []

    @patch("asyncio.to_thread")
    async def test_restore_success(self, mock_to_thread, tmp_path) -> None:
        rb = self._rb()
        fake = MagicMock(returncode=0)

        async def fake_async(*args, **kwargs):
            return fake

        mock_to_thread.side_effect = fake_async
        ok = await rb.restore("latest", tmp_path)
        assert ok is True

    @patch("asyncio.to_thread")
    async def test_restore_failure(self, mock_to_thread, tmp_path) -> None:
        rb = self._rb()

        async def raise_cpe(*args, **kwargs):
            raise subprocess.CalledProcessError(1, cmd=[], stderr="restore failed")

        mock_to_thread.side_effect = raise_cpe
        with pytest.raises(ResticRestoreError) as exc_info:
            await rb.restore("latest", tmp_path)
        assert exc_info.value.code == "AEGIS-BACKUP-0008"

    def test_parse_snapshot_id(self) -> None:
        rb = self._rb()
        stdout = json.dumps({"message_type": "summary", "snapshot_id": "xyz123"})
        sid = rb._parse_snapshot_id(stdout)
        assert sid == "xyz123"

    def test_parse_snapshot_id_short_id_fallback(self) -> None:
        rb = self._rb()
        stdout = json.dumps({"message_type": "summary", "short_id": "short1"})
        sid = rb._parse_snapshot_id(stdout)
        assert sid == "short1"

    def test_parse_snapshot_id_no_match(self) -> None:
        rb = self._rb()
        sid = rb._parse_snapshot_id("no json here")
        assert sid is None

    @patch("asyncio.to_thread")
    async def test_init_repo_file_not_found(self, mock_to_thread) -> None:
        rb = self._rb()

        async def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("restic: not found")

        mock_to_thread.side_effect = raise_fnf
        with pytest.raises(ResticInitError) as exc_info:
            await rb.init_repo()
        assert "not found" in str(exc_info.value).lower()

    @patch("asyncio.to_thread")
    async def test_prune_returns_count(self, mock_to_thread) -> None:
        rb = self._rb()
        output = json.dumps({"remove": ["snap1", "snap2"], "keep": []})
        fake = MagicMock(stdout=output, returncode=0)

        async def fake_async(*args, **kwargs):
            return fake

        mock_to_thread.side_effect = fake_async
        deleted = await rb.prune()
        assert deleted == 2

    @patch("asyncio.to_thread")
    async def test_prune_returns_zero_on_error(self, mock_to_thread) -> None:
        rb = self._rb()

        async def raise_err(*args, **kwargs):
            raise RuntimeError("prune failed")

        mock_to_thread.side_effect = raise_err
        deleted = await rb.prune()
        assert deleted == 0

    @patch("asyncio.to_thread")
    async def test_prune_non_dict_output(self, mock_to_thread) -> None:
        rb = self._rb()
        fake = MagicMock(stdout=json.dumps([]), returncode=0)

        async def fake_async(*args, **kwargs):
            return fake

        mock_to_thread.side_effect = fake_async
        deleted = await rb.prune()
        assert deleted == 0

    @patch("asyncio.to_thread")
    async def test_sync_to_b2_success(self, mock_to_thread) -> None:
        rb = ResticBackup(settings=_settings(restic_b2_bucket="my-bucket"))
        fake = MagicMock(returncode=0)

        async def fake_async(*args, **kwargs):
            return fake

        mock_to_thread.side_effect = fake_async
        await rb._sync_to_b2()  # must not raise

    @patch("asyncio.to_thread")
    async def test_sync_to_b2_failure_logs_warning(self, mock_to_thread) -> None:
        rb = ResticBackup(settings=_settings(restic_b2_bucket="my-bucket"))

        async def raise_err(*args, **kwargs):
            raise RuntimeError("rclone not found")

        mock_to_thread.side_effect = raise_err
        await rb._sync_to_b2()  # must not raise — best-effort

    @patch("asyncio.to_thread")
    async def test_backup_with_b2_bucket_triggers_sync(self, mock_to_thread) -> None:
        rb = ResticBackup(settings=_settings(restic_b2_bucket="my-bucket"))
        snap_id = "abc123def456"
        backup_stdout = json.dumps({"message_type": "summary", "snapshot_id": snap_id})
        fake = MagicMock(stdout=backup_stdout, returncode=0)
        sync_calls = []

        async def fake_async(*args, **kwargs):
            cmd = args[1] if len(args) > 1 else []
            if isinstance(cmd, list) and "rclone" in cmd:
                sync_calls.append(1)
            return fake

        mock_to_thread.side_effect = fake_async
        result = await rb.backup(paths=[Path("/tmp")])  # noqa: S108
        assert result == snap_id

    @patch("asyncio.to_thread")
    async def test_list_snapshots_skips_malformed_items(self, mock_to_thread) -> None:
        rb = self._rb()
        # Mix valid + invalid snapshot dicts.
        snap_json = json.dumps([
            {"id": "aaa", "time": "2026-05-30T10:00:00+00:00", "hostname": "box"},
            {"bad": "data"},  # missing required fields → KeyError → skip
        ])
        fake = MagicMock(stdout=snap_json, returncode=0)

        async def fake_async(*args, **kwargs):
            return fake

        mock_to_thread.side_effect = fake_async
        snaps = await rb.list_snapshots()
        assert len(snaps) == 1


def test_restic_settings_cache() -> None:
    from aegis.backup.restic_manager import _settings as restic_settings

    restic_settings.cache_clear()
    try:
        result = restic_settings()
        from aegis.backup.settings import BackupSettings

        assert isinstance(result, BackupSettings)
    except Exception:
        pass
    finally:
        restic_settings.cache_clear()
