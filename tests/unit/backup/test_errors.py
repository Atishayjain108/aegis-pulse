"""Unit tests for aegis.backup.errors."""

from __future__ import annotations

import pytest

from aegis.backup.errors import (
    AegisBackupError,
    BackupCommandError,
    BackupInitError,
    BackupMetadataStoreError,
    BackupParseError,
    BackupStalenessError,
    BackupTimeoutError,
    BackupVerifyError,
    ResticCommandError,
    ResticInitError,
    ResticPasswordMissingError,
    ResticRestoreError,
    ResticStalenessError,
    ResticTimeoutError,
    RestoreError,
)


@pytest.mark.parametrize(
    "cls, expected_code",
    [
        (AegisBackupError, "AEGIS-BACKUP-0000"),
        (BackupInitError, "AEGIS-BACKUP-0001"),
        (BackupTimeoutError, "AEGIS-BACKUP-0002"),
        (BackupCommandError, "AEGIS-BACKUP-0003"),
        (RestoreError, "AEGIS-BACKUP-0004"),
        (ResticInitError, "AEGIS-BACKUP-0005"),
        (ResticTimeoutError, "AEGIS-BACKUP-0006"),
        (ResticCommandError, "AEGIS-BACKUP-0007"),
        (ResticRestoreError, "AEGIS-BACKUP-0008"),
        (BackupStalenessError, "AEGIS-BACKUP-0009"),
        (ResticStalenessError, "AEGIS-BACKUP-0010"),
        (BackupMetadataStoreError, "AEGIS-BACKUP-0012"),
        (BackupVerifyError, "AEGIS-BACKUP-0013"),
        (ResticPasswordMissingError, "AEGIS-BACKUP-0014"),
        (BackupParseError, "AEGIS-BACKUP-0015"),
    ],
)
def test_error_codes(cls, expected_code: str) -> None:
    err = cls("test message")
    assert err.code == expected_code
    assert "test message" in str(err)


def test_custom_code_override() -> None:
    err = AegisBackupError("custom", code="AEGIS-BACKUP-0999")
    assert err.code == "AEGIS-BACKUP-0999"


def test_inheritance() -> None:
    err = BackupCommandError("cmd failed")
    assert isinstance(err, AegisBackupError)
    assert isinstance(err, Exception)
