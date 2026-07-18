"""
aegis.backup.settings
=====================

Pydantic-settings for the Phase 15 Disaster Recovery subsystem.

All env vars use the ``AEGIS_BACKUP_`` prefix.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE_CANDIDATES = (".env", ".env.local")


class BackupSettings(BaseSettings):
    """Disaster-recovery backup configuration."""

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_BACKUP_",
        env_file=_ENV_FILE_CANDIDATES,
        extra="ignore",
        frozen=True,
    )

    # ── pgBackRest ────────────────────────────────────────────────────────
    pgbackrest_stanza: str = Field(default="aegis-prod")
    pgbackrest_repo_path: str = Field(default="/var/lib/pgbackrest")
    pgbackrest_retention_full_days: int = Field(default=30, ge=1, le=365)
    pgbackrest_schedule_min: int = Field(default=15, ge=1, le=1440)
    pgbackrest_timeout_s: int = Field(default=300, ge=30, le=3600)

    # ── restic ────────────────────────────────────────────────────────────
    restic_password: str = Field(default="")
    restic_repository: str = Field(default="local:/var/lib/restic")
    restic_b2_bucket: str = Field(default="")
    restic_retention_days: int = Field(default=90, ge=1, le=365)

    # ── MinIO backup bucket ───────────────────────────────────────────────
    minio_bucket: str = Field(default="aegis-backups")

    # ── health monitor ────────────────────────────────────────────────────
    pgbackrest_stale_threshold_min: int = Field(default=30, ge=1)
    restic_stale_threshold_min: int = Field(default=1440, ge=1)
    consecutive_failure_alert_threshold: int = Field(default=3, ge=1)
    health_check_interval_s: int = Field(default=1800, ge=60)
