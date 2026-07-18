"""Pydantic-settings configuration for the Phase 10 data lake.

All knobs are environment-driven (prefix ``AEGIS_DATALAKE_``).
Defaults match the docker-compose stack from Phases 1–4.

The Settings object is *not* a global singleton — each component receives
its own instance via dependency injection, matching the Phase 3 pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

try:
    from pydantic import Field, SecretStr, field_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError as exc:  # pragma: no cover — required for runtime
    raise ImportError(
        "pydantic and pydantic-settings are required for aegis.datalake.settings"
    ) from exc

from aegis.datalake import constants as C


class DataLakeSettings(BaseSettings):
    """Top-level configuration for the data lake.

    Configure via environment variables prefixed ``AEGIS_DATALAKE_``.
    Example:
        AEGIS_DATALAKE_BUCKET=aegis-prod
        AEGIS_DATALAKE_S3_ENDPOINT=https://s3.amazonaws.com
        AEGIS_DATALAKE_DUCKDB_MEMORY_LIMIT_MB=4096
    """

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_DATALAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- storage --------------------------------------------------------
    bucket: str = Field(default=C.DEFAULT_BUCKET, description="MinIO/S3 bucket name")
    s3_endpoint: str = Field(default=C.DEFAULT_S3_ENDPOINT)
    s3_region: str = Field(default=C.DEFAULT_S3_REGION)
    s3_access_key: SecretStr = Field(default=SecretStr("aegis-dev-key"))
    s3_secret_key: SecretStr = Field(default=SecretStr("aegis-dev-secret-please-change"))
    s3_use_ssl: bool = Field(default=False)

    # When True, the local filesystem under `local_root` is used instead
    # of S3 — invaluable for unit tests and offline development.
    use_local_filesystem: bool = Field(
        default=False, description="Bypass S3 and store under local_root"
    )
    local_root: Path = Field(
        default=Path.home() / ".aegis" / "datalake" / "local",
        description="Root for the local filesystem backend",
    )

    # ----- catalog --------------------------------------------------------
    catalog_db_path: Path = Field(
        default=Path.home() / ".aegis" / "datalake" / "catalog.sqlite3",
        description="SQLite path for the local table catalog",
    )

    # ----- duckdb ---------------------------------------------------------
    duckdb_memory_limit_mb: int = Field(
        default=C.DUCKDB_MEMORY_LIMIT_MB, ge=128, le=131_072
    )
    duckdb_thread_count: int = Field(default=C.DUCKDB_THREAD_COUNT, ge=1, le=64)
    duckdb_temp_dir: Path = Field(
        default=Path.home() / ".aegis" / "datalake" / "duckdb_tmp",
    )
    query_timeout_s: float = Field(default=C.DEFAULT_QUERY_TIMEOUT_S, gt=0)

    # ----- batching / retention ------------------------------------------
    bronze_batch_max_rows: int = Field(default=C.BRONZE_BATCH_MAX_ROWS, gt=0)
    bronze_batch_max_age_s: float = Field(default=C.BRONZE_BATCH_MAX_AGE_S, gt=0)
    silver_retention_days: int = Field(default=C.DEFAULT_SILVER_RETENTION_DAYS, ge=1)
    gold_retention_days: int = Field(default=C.DEFAULT_GOLD_RETENTION_DAYS, ge=1)

    # ----- quality --------------------------------------------------------
    quality_max_reject_fraction: float = Field(
        default=C.QUALITY_MAX_REJECT_FRACTION, ge=0.0, le=1.0
    )

    # ----- upstream integration -------------------------------------------
    postgres_dsn: SecretStr = Field(
        default=SecretStr("postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis"),
        description="DSN for the Phase 1 Postgres (read-only ingest)",
    )
    redis_url: SecretStr = Field(
        default=SecretStr("redis://localhost:6380/0"),
        description="URL for the Phase 2 Redis Streams",
    )
    tenant_id: str = Field(default=C.DEFAULT_TENANT_ID)

    # ----- ergonomics -----------------------------------------------------
    enable_structured_logs: bool = Field(default=True)
    log_level: str = Field(default="INFO")

    # ---------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------
    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}; got {v!r}")
        return upper

    @field_validator("bucket")
    @classmethod
    def _validate_bucket(cls, v: str) -> str:
        # S3 bucket name rules — 3-63 chars, lower, digit, dot, hyphen
        if not (3 <= len(v) <= 63):
            raise ValueError(f"bucket must be 3-63 chars; got {len(v)}")
        if not all(ch.islower() or ch.isdigit() or ch in ".-" for ch in v):
            raise ValueError("bucket must be lowercase + digits + . / -")
        return v

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------
    def storage_root(self, layer: str) -> str:
        """Return the URI prefix for a medallion layer.

        Layout (S3 example):
            s3://{bucket}/bronze/...
            s3://{bucket}/silver/...
            s3://{bucket}/gold/...

        Local example::
            {local_root}/bronze/...
        """
        if layer not in C.VALID_LAYERS:
            raise ValueError(f"layer must be one of {sorted(C.VALID_LAYERS)}; got {layer!r}")
        if self.use_local_filesystem:
            return str(self.local_root / layer)
        return f"s3://{self.bucket}/{layer}"

    def ensure_local_dirs(self) -> None:
        """Pre-create local dirs (idempotent). No-op when using S3."""
        if not self.use_local_filesystem:
            self.duckdb_temp_dir.mkdir(parents=True, exist_ok=True)
            self.catalog_db_path.parent.mkdir(parents=True, exist_ok=True)
            return
        for layer in C.VALID_LAYERS:
            (self.local_root / layer).mkdir(parents=True, exist_ok=True)
        self.duckdb_temp_dir.mkdir(parents=True, exist_ok=True)
        self.catalog_db_path.parent.mkdir(parents=True, exist_ok=True)


# Type aliases for clarity in downstream signatures
SettingsLike = Annotated[DataLakeSettings, "DataLake configuration"]


__all__ = ["DataLakeSettings", "SettingsLike"]
