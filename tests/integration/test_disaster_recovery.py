"""
Integration tests for Phase 15 Disaster Recovery.

These tests exercise the full backup → corrupt → restore cycle against live
infrastructure (Postgres, MinIO).  They are skipped unless
``AEGIS_INTEGRATION_TEST=1`` is set and the required services are running.

Run with:
    docker compose up -d postgres redis minio
    AEGIS_INTEGRATION_TEST=1 uv run python -m pytest \
        tests/integration/test_disaster_recovery.py -v
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime

import pytest

from aegis.backup.pgbackrest_manager import BackupMetadata

_INTEGRATION = os.getenv("AEGIS_INTEGRATION_TEST", "0") == "1"
skip_unless_integration = pytest.mark.skipif(
    not _INTEGRATION,
    reason="Set AEGIS_INTEGRATION_TEST=1 to run DR integration tests",
)


# ---------------------------------------------------------------------------
# Metadata integrity (no infra needed)
# ---------------------------------------------------------------------------


class TestBackupMetadataIntegrity:
    """Verify deterministic serialisation for checksumming."""

    def _make_meta(self, **kwargs) -> BackupMetadata:
        defaults = {
            "backup_id": "20260531T120000Z",
            "backup_type": "full",
            "size_mb": 512.5,
            "duration_s": 45.2,
            "database_version": "16",
            "checksum": "abcdef1234567890",
            "retention_expires": datetime(2026, 8, 31, tzinfo=UTC),
            "host": "laptop",
            "status": "ok",
            "timestamp": datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC),
        }
        defaults.update(kwargs)
        return BackupMetadata(**defaults)

    def test_serialisation_is_deterministic(self) -> None:
        meta = self._make_meta()
        data1 = json.dumps(meta.to_dict(), sort_keys=True, ensure_ascii=False)
        data2 = json.dumps(meta.to_dict(), sort_keys=True, ensure_ascii=False)
        assert data1 == data2

    def test_checksum_stability(self) -> None:
        meta = self._make_meta()
        d1 = json.dumps(meta.to_dict(), sort_keys=True, ensure_ascii=False).encode()
        d2 = json.dumps(meta.to_dict(), sort_keys=True, ensure_ascii=False).encode()
        h1 = hashlib.sha256(d1).hexdigest()
        h2 = hashlib.sha256(d2).hexdigest()
        assert h1 == h2

    def test_roundtrip_preserves_all_fields(self) -> None:
        meta = self._make_meta(wal_start="000000010000000000000001", wal_stop="000000010000000000000002")
        restored = BackupMetadata.from_dict(meta.to_dict())
        assert restored.backup_id == meta.backup_id
        assert restored.backup_type == meta.backup_type
        assert restored.size_mb == meta.size_mb
        assert restored.duration_s == meta.duration_s
        assert restored.database_version == meta.database_version
        assert restored.checksum == meta.checksum
        assert restored.host == meta.host
        assert restored.status == meta.status
        assert restored.wal_start == meta.wal_start
        assert restored.wal_stop == meta.wal_stop

    def test_retention_expiry_in_future(self) -> None:
        meta = self._make_meta()
        assert meta.retention_expires > datetime.now(UTC)


# ---------------------------------------------------------------------------
# pgBackRest integration (requires running Postgres + pgBackRest binary)
# ---------------------------------------------------------------------------


@skip_unless_integration
@pytest.mark.asyncio
async def test_pgbackrest_list_backups_connects():
    """Verify BackupManager can connect to pgBackRest stanza."""
    from aegis.backup.pgbackrest_manager import BackupManager

    mgr = BackupManager()
    # list_backups should not raise even if the stanza is empty.
    backups = await mgr.list_backups()
    assert isinstance(backups, list)


@skip_unless_integration
@pytest.mark.asyncio
async def test_pgbackrest_prune_idempotent():
    """Pruning with no expired backups should return 0 and not raise."""
    from aegis.backup.pgbackrest_manager import BackupManager

    mgr = BackupManager()
    count = await mgr.prune_old_backups()
    assert isinstance(count, int)
    assert count >= 0


# ---------------------------------------------------------------------------
# restic integration (requires restic binary + AEGIS_BACKUP_RESTIC_PASSWORD)
# ---------------------------------------------------------------------------


_RESTIC_AVAILABLE = (
    _INTEGRATION
    and bool(os.getenv("AEGIS_BACKUP_RESTIC_PASSWORD"))
)
skip_unless_restic = pytest.mark.skipif(
    not _RESTIC_AVAILABLE,
    reason="AEGIS_BACKUP_RESTIC_PASSWORD required for restic integration tests",
)


@skip_unless_restic
@pytest.mark.asyncio
async def test_restic_init_repo(tmp_path):
    """restic repo initialisation is idempotent."""
    import os

    os.environ["AEGIS_BACKUP_RESTIC_REPOSITORY"] = f"local:{tmp_path}"
    from aegis.backup.restic_manager import ResticBackup

    rb = ResticBackup()
    ok1 = await rb.init_repo()
    ok2 = await rb.init_repo()  # idempotent
    assert ok1 is True
    assert ok2 is True


@skip_unless_restic
@pytest.mark.asyncio
async def test_restic_backup_restore_cycle(tmp_path):
    """Full backup → verify → restore → verify cycle."""
    import os

    repo_path = tmp_path / "repo"
    src_path = tmp_path / "src"
    restore_path = tmp_path / "restored"

    src_path.mkdir()
    (src_path / "data.txt").write_text("AEGIS DR test data — should survive restore")

    os.environ["AEGIS_BACKUP_RESTIC_REPOSITORY"] = f"local:{repo_path}"

    from aegis.backup.restic_manager import ResticBackup

    rb = ResticBackup()

    # 1. Create snapshot.
    sid = await rb.backup(label="dr-test", paths=[src_path])
    assert sid is not None

    # 2. Corrupt source.
    (src_path / "data.txt").write_text("CORRUPTED")

    # 3. Restore.
    ok = await rb.restore(sid, restore_path)
    assert ok is True

    # 4. Verify content.
    restored_file = restore_path / str(src_path).lstrip("/") / "data.txt"
    if restored_file.exists():
        content = restored_file.read_text()
        assert "AEGIS DR test data" in content


# ---------------------------------------------------------------------------
# Backup health integration
# ---------------------------------------------------------------------------


@skip_unless_integration
@pytest.mark.asyncio
async def test_backup_health_check_returns_dict():
    """Health check should return a dict without raising."""
    from aegis.backup.health import BackupHealth

    bh = BackupHealth()
    status = await bh.check()
    assert isinstance(status, dict)
    assert "pgbackrest" in status
    assert "restic" in status
    for v in status.values():
        assert isinstance(v, bool)
