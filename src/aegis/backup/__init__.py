"""
aegis.backup
============

Phase 15 — Disaster Recovery & Business Continuity.

Public surface
--------------
* ``BackupManager``   — pgBackRest incremental Postgres backups
* ``ResticBackup``    — encrypted filesystem snapshots
* ``BackupHealth``    — staleness monitor + alerting
* ``backup_settings`` — typed settings singleton
"""

from __future__ import annotations

from functools import lru_cache

from aegis.backup.errors import (
    AegisBackupError,
    BackupCommandError,
    BackupInitError,
    BackupTimeoutError,
    ResticCommandError,
    ResticInitError,
    RestoreError,
)
from aegis.backup.pgbackrest_manager import BackupManager, BackupMetadata
from aegis.backup.restic_manager import ResticBackup, ResticSnapshot
from aegis.backup.settings import BackupSettings


@lru_cache(maxsize=1)
def backup_settings() -> BackupSettings:
    return BackupSettings()


__all__ = [
    "AegisBackupError",
    "BackupCommandError",
    "BackupInitError",
    "BackupManager",
    "BackupMetadata",
    "BackupSettings",
    "BackupTimeoutError",
    "ResticBackup",
    "ResticCommandError",
    "ResticInitError",
    "ResticSnapshot",
    "RestoreError",
    "backup_settings",
]
