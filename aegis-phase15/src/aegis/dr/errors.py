"""
aegis.dr.errors
===============
Typed error hierarchy for Phase 15 — Disaster Recovery.

Every exception carries:
  - machine_code  → links to docs/errors/AEGIS-DR-NNNN.md
  - human_message → shown in dashboard / alerts
  - context       → structured dict for structured logging

Architecture position: imported by all DR modules.
"""

from __future__ import annotations

from typing import Any


class AegisDrError(Exception):
    """Base class for all Phase-15 DR errors."""

    machine_code: str = "AEGIS-DR-0000"

    def __init__(
        self,
        human_message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(human_message)
        self.human_message = human_message
        self.context: dict[str, Any] = context or {}

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"{self.__class__.__name__}("
            f"code={self.machine_code!r}, "
            f"msg={self.human_message!r}, "
            f"ctx={self.context!r})"
        )


# ---------------------------------------------------------------------------
# Backup errors
# ---------------------------------------------------------------------------


class BackupError(AegisDrError):
    """Generic backup failure."""

    machine_code = "AEGIS-DR-0001"


class PostgresBackupError(BackupError):
    """pg_dump or WAL archival failed."""

    machine_code = "AEGIS-DR-0001"


class RedisBackupError(BackupError):
    """Redis BGSAVE or upload failed."""

    machine_code = "AEGIS-DR-0002"


class MinioBackupError(BackupError):
    """MinIO replication or object-lock failure."""

    machine_code = "AEGIS-DR-0003"


class ChromaDBBackupError(BackupError):
    """ChromaDB export to Parquet failed."""

    machine_code = "AEGIS-DR-0004"


class ModelBackupError(BackupError):
    """Model registry snapshot failed."""

    machine_code = "AEGIS-DR-0005"


class ResticBackupError(BackupError):
    """restic backup operation failed."""

    machine_code = "AEGIS-DR-0006"


# ---------------------------------------------------------------------------
# Restore errors
# ---------------------------------------------------------------------------


class RestoreError(AegisDrError):
    """Generic restore failure."""

    machine_code = "AEGIS-DR-0010"


class PostgresRestoreError(RestoreError):
    """pg_restore or verification failed."""

    machine_code = "AEGIS-DR-0010"


class RedisRestoreError(RestoreError):
    """Redis RDB load or verification failed."""

    machine_code = "AEGIS-DR-0011"


class ChromaDBRestoreError(RestoreError):
    """ChromaDB restore from Parquet failed."""

    machine_code = "AEGIS-DR-0012"


class ModelRestoreError(RestoreError):
    """Model registry restore failed."""

    machine_code = "AEGIS-DR-0013"


class VerificationError(RestoreError):
    """Post-restore data integrity check failed."""

    machine_code = "AEGIS-DR-0014"


# ---------------------------------------------------------------------------
# Drill errors
# ---------------------------------------------------------------------------


class DrillError(AegisDrError):
    """Restore drill failure."""

    machine_code = "AEGIS-DR-0020"


class DrillTimeoutError(DrillError):
    """Drill did not complete within SLA."""

    machine_code = "AEGIS-DR-0020"


class DrillVerificationError(DrillError):
    """Post-drill data check failed."""

    machine_code = "AEGIS-DR-0021"


class NoBackupFoundError(DrillError):
    """No suitable backup found to restore."""

    machine_code = "AEGIS-DR-0022"


# ---------------------------------------------------------------------------
# SLA breach errors
# ---------------------------------------------------------------------------


class RpoBreachError(AegisDrError):
    """Backup age exceeds RPO target."""

    machine_code = "AEGIS-DR-0025"


class RtoBreachError(AegisDrError):
    """Recovery duration exceeds RTO target."""

    machine_code = "AEGIS-DR-0026"


# ---------------------------------------------------------------------------
# Manifest errors
# ---------------------------------------------------------------------------


class ManifestCorruptError(AegisDrError):
    """Backup manifest failed integrity check."""

    machine_code = "AEGIS-DR-0030"
