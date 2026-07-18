"""
aegis.backup.errors
===================

Typed error hierarchy for Phase 15 Disaster Recovery.

Error codes: AEGIS-BACKUP-0001 .. AEGIS-BACKUP-0015
"""

from __future__ import annotations


class AegisBackupError(Exception):
    """Base class for all backup subsystem errors."""

    code: str = "AEGIS-BACKUP-0000"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class BackupInitError(AegisBackupError):
    """Backup subsystem failed to initialise (stanza / bucket)."""

    code = "AEGIS-BACKUP-0001"


class BackupTimeoutError(AegisBackupError):
    """Backup process exceeded the configured timeout."""

    code = "AEGIS-BACKUP-0002"


class BackupCommandError(AegisBackupError):
    """External backup command returned non-zero exit status."""

    code = "AEGIS-BACKUP-0003"


class RestoreError(AegisBackupError):
    """Restore operation failed."""

    code = "AEGIS-BACKUP-0004"


class ResticInitError(AegisBackupError):
    """restic repository initialisation failed."""

    code = "AEGIS-BACKUP-0005"


class ResticTimeoutError(AegisBackupError):
    """restic backup process exceeded timeout."""

    code = "AEGIS-BACKUP-0006"


class ResticCommandError(AegisBackupError):
    """restic command returned non-zero exit status."""

    code = "AEGIS-BACKUP-0007"


class ResticRestoreError(AegisBackupError):
    """restic restore operation failed."""

    code = "AEGIS-BACKUP-0008"


class BackupStalenessError(AegisBackupError):
    """Most recent backup is older than the stale threshold."""

    code = "AEGIS-BACKUP-0009"


class ResticStalenessError(AegisBackupError):
    """Most recent restic snapshot is older than the stale threshold."""

    code = "AEGIS-BACKUP-0010"


class BackupAlertError(AegisBackupError):
    """Failed to dispatch a backup failure alert."""

    code = "AEGIS-BACKUP-0011"


class BackupMetadataStoreError(AegisBackupError):
    """Failed to persist backup metadata to MinIO."""

    code = "AEGIS-BACKUP-0012"


class BackupVerifyError(AegisBackupError):
    """Post-restore database sanity check failed."""

    code = "AEGIS-BACKUP-0013"


class ResticPasswordMissingError(AegisBackupError):
    """RESTIC_PASSWORD / AEGIS_BACKUP_RESTIC_PASSWORD not set."""

    code = "AEGIS-BACKUP-0014"


class BackupParseError(AegisBackupError):
    """Could not parse backup ID or metadata from command output."""

    code = "AEGIS-BACKUP-0015"
