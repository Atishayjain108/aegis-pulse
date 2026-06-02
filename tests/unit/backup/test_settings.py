"""Unit tests for aegis.backup.settings."""

from __future__ import annotations

import pytest

from aegis.backup.settings import BackupSettings


def test_defaults() -> None:
    s = BackupSettings()
    assert s.pgbackrest_stanza == "aegis-prod"
    assert s.pgbackrest_retention_full_days == 30
    assert s.pgbackrest_schedule_min == 15
    assert s.pgbackrest_timeout_s == 300
    assert s.restic_retention_days == 90
    assert s.minio_bucket == "aegis-backups"
    assert s.health_check_interval_s == 1800


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEGIS_BACKUP_PGBACKREST_STANZA", "my-stanza")
    monkeypatch.setenv("AEGIS_BACKUP_RESTIC_PASSWORD", "hunter2")
    monkeypatch.setenv("AEGIS_BACKUP_RESTIC_RETENTION_DAYS", "7")
    s = BackupSettings()
    assert s.pgbackrest_stanza == "my-stanza"
    assert s.restic_password == "hunter2"
    assert s.restic_retention_days == 7
