"""
aegis.dr.config
===============
Configuration for Phase 15 — Disaster Recovery.

All settings are loaded from environment variables with the prefix ``AEGIS_DR_``.
Defaults allow the system to run without any configuration on a clean install.

Architecture position: singleton, imported by orchestrator + all backup/restore modules.
"""

from __future__ import annotations

import functools

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DisasterRecoverySettings(BaseSettings):
    """
    Phase 15 DR settings.

    All variables are prefixed ``AEGIS_DR_`` in the environment.
    """

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_DR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # MinIO / S3
    # ------------------------------------------------------------------
    minio_endpoint: str = Field(
        default="localhost:9002",
        description="MinIO host:port (no scheme)",
    )
    minio_access_key: SecretStr = Field(
        default=SecretStr("aegis-dev-key"),
        description="MinIO access key",
    )
    minio_secret_key: SecretStr = Field(
        default=SecretStr("aegis-dev-secret-please-change"),
        description="MinIO secret key",
    )
    minio_secure: bool = Field(
        default=False,
        description="Use TLS for MinIO connection",
    )
    dr_bucket: str = Field(
        default="aegis-dr",
        description="Primary DR bucket name",
    )
    secondary_bucket: str = Field(
        default="",
        description="Secondary/offsite bucket for cold copies (leave empty to skip)",
    )

    # ------------------------------------------------------------------
    # PostgreSQL
    # ------------------------------------------------------------------
    pg_dsn: str = Field(
        default="postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis",
        description="PostgreSQL DSN for backup target",
    )
    pg_backup_interval_s: int = Field(
        default=900,
        description="Seconds between pg_dump runs",
    )
    pg_dump_timeout_s: int = Field(
        default=600,
        description="Timeout for a single pg_dump in seconds",
    )

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    redis_url: str = Field(
        default="redis://localhost:6380/0",
        description="Redis URL for backup target",
    )
    redis_backup_interval_s: int = Field(
        default=300,
        description="Seconds between Redis BGSAVE + upload runs",
    )
    redis_rdb_path: str = Field(
        default="/data/dump.rdb",
        description="Path to Redis RDB file inside the Redis container (or local path)",
    )

    # ------------------------------------------------------------------
    # ChromaDB
    # ------------------------------------------------------------------
    chromadb_host: str = Field(
        default="localhost",
        description="ChromaDB host",
    )
    chromadb_port: int = Field(
        default=8000,
        description="ChromaDB HTTP port",
    )
    chromadb_backup_interval_s: int = Field(
        default=3600,
        description="Seconds between ChromaDB export runs",
    )

    # ------------------------------------------------------------------
    # Model registry
    # ------------------------------------------------------------------
    model_registry_path: str = Field(
        default="~/.aegis/models",
        description="Local path to Phase 3 model registry",
    )
    model_backup_interval_s: int = Field(
        default=3600,
        description="Seconds between model registry snapshots",
    )
    max_model_versions_hot: int = Field(
        default=5,
        description="Number of model versions to keep in hot DR storage",
    )

    # ------------------------------------------------------------------
    # restic
    # ------------------------------------------------------------------
    restic_enabled: bool = Field(
        default=False,
        description="Enable restic encrypted repo backups (requires restic binary)",
    )
    restic_repository: str = Field(
        default="",
        description="restic repo URL (e.g. s3:http://localhost:9002/aegis-restic)",
    )
    restic_password: SecretStr = Field(
        default=SecretStr(""),
        description="restic repository password",
    )
    restic_source_paths: list[str] = Field(
        default_factory=lambda: ["~/code/aegis-pulse"],
        description="Paths restic should back up",
    )

    # ------------------------------------------------------------------
    # Drill
    # ------------------------------------------------------------------
    drill_enabled: bool = Field(
        default=True,
        description="Enable automated weekly restore drill",
    )
    drill_interval_s: int = Field(
        default=604_800,
        description="Seconds between drill runs (default: 7 days)",
    )
    drill_targets: list[str] = Field(
        default_factory=lambda: ["postgres", "redis"],
        description="Which targets to include in the drill",
    )
    drill_pg_dsn: str = Field(
        default="postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis_drill",
        description="DSN for the throwaway drill database (must differ from prod!)",
    )

    # ------------------------------------------------------------------
    # SLA
    # ------------------------------------------------------------------
    rpo_target_s: int = Field(
        default=900,
        description="Recovery Point Objective in seconds",
    )
    rto_target_s: int = Field(
        default=3600,
        description="Recovery Time Objective in seconds",
    )

    # ------------------------------------------------------------------
    # Notifications (integrates with Phase 4 notifiers)
    # ------------------------------------------------------------------
    alert_on_backup_failure: bool = Field(
        default=True,
        description="Send alert when a backup job fails",
    )
    alert_on_rpo_breach: bool = Field(
        default=True,
        description="Send alert when RPO target is breached",
    )
    alert_on_drill_failure: bool = Field(
        default=True,
        description="Send alert when the weekly drill fails",
    )
    ntfy_topic: str = Field(
        default="",
        description="ntfy topic for DR alerts (empty = disabled)",
    )


@functools.lru_cache(maxsize=1)
def get_dr_settings() -> DisasterRecoverySettings:
    """Return the process-singleton DR settings (lazy, cached)."""
    return DisasterRecoverySettings()
