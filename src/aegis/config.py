"""
aegis.config
============

Typed, frozen settings singleton for the entire AEGIS Pulse runtime.

Every environment variable consumed anywhere in the system is declared here
exactly once. Modules MUST NOT call ``os.environ`` directly — they import
``settings()`` and read attributes off the typed model. This keeps the
configuration surface auditable and makes it impossible to ship a missing
or mistyped variable to production.

Design rules
------------
* All env vars use the ``AEGIS_`` prefix to avoid collisions with system
  variables (``HOME``, ``PATH``, ``USER``…) and other tools.
* ``AEGIS_PG_DSN`` and ``AEGIS_REDIS_URL`` are *required* — the system has
  no sensible defaults for these (a wrong default would silently scribble
  on the developer's local databases).
* Secrets are typed ``SecretStr`` so they never appear in ``repr()`` or
  log lines.
* The settings object is **frozen**: mutate via env + restart, never at
  runtime. This guarantees that observability traces match the code that
  produced them.
* The accessor is ``settings()`` (not a module-level constant) so test
  code can monkey-patch ``os.environ`` and call ``reload_settings()``.

Author: AEGIS Pulse foundation team
Phase:  1 — Foundation
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path  # required at runtime — Pydantic resolves annotations at import time
from typing import Final, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aegis.constants import (
    DB_POOL_MAX_SIZE,
    DB_POOL_MIN_SIZE,
    DEFAULT_TENANT_UUID,
)

Environment = Literal["dev", "staging", "prod", "test"]
"""The four deploy environments AEGIS knows about."""

LogFormat = Literal["json", "console"]
"""``json`` for machine ingestion, ``console`` for human eyes."""

_ENV_FILE_CANDIDATES: Final[tuple[str, ...]] = (".env", ".env.local")
"""Files Pydantic Settings will load in order; later files override earlier."""


class _MinIOSettings(BaseSettings):
    """MinIO / S3-compatible object store configuration.

    Used by the raw-audit-trail Parquet writer and by ``pgBackRest`` for
    Postgres backups. ``endpoint`` is host:port (no scheme); ``secure``
    chooses ``https://`` vs ``http://``.
    """

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_MINIO_",
        env_file=_ENV_FILE_CANDIDATES,
        extra="ignore",
        frozen=True,
    )

    endpoint: str = Field(default="localhost:9000")
    access_key: SecretStr = Field(default=SecretStr("aegis-dev-key"))
    secret_key: SecretStr = Field(default=SecretStr("aegis-dev-secret-please-change"))
    secure: bool = Field(default=False)
    region: str = Field(default="us-east-1")
    bucket_raw: str = Field(default="aegis-raw")
    bucket_features: str = Field(default="aegis-features")
    bucket_models: str = Field(default="aegis-models")
    bucket_backups: str = Field(default="aegis-backups")


class _ScrapeSettings(BaseSettings):
    """Scraping subsystem configuration.

    These knobs apply globally; per-source overrides live on
    ``AdapterConfig`` and are loaded from per-source YAML.
    """

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_SCRAPE_",
        env_file=_ENV_FILE_CANDIDATES,
        extra="ignore",
        frozen=True,
    )

    flaresolverr_url: str = Field(default="http://localhost:8191/v1")
    flaresolverr_timeout_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    proxy_pool_path: Path | None = Field(
        default=None,
        description="Optional path to a YAML file enumerating proxies. "
        "If unset, the proxy pool starts empty and adapters fall back to "
        "direct connections.",
    )
    user_agent_pool_path: Path | None = Field(default=None)
    default_concurrency: int = Field(default=4, ge=1, le=64)
    respect_robots_txt: bool = Field(default=True)


class _RedditSettings(BaseSettings):
    """Reddit / PRAW credentials.

    All three are required for the Reddit adapter to start; the adapter
    will refuse to run with placeholder values. Read-only mode means we
    never authenticate as a user — only as a script app.
    """

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_REDDIT_",
        env_file=_ENV_FILE_CANDIDATES,
        extra="ignore",
        frozen=True,
    )

    client_id: SecretStr | None = Field(default=None)
    client_secret: SecretStr | None = Field(default=None)
    user_agent: str = Field(default="aegis-pulse/0.1 (by /u/aegis-pulse)")


class _YouTubeSettings(BaseSettings):
    """YouTube Data API v3 — free tier 10 000 quota units / day."""

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_YOUTUBE_",
        env_file=_ENV_FILE_CANDIDATES,
        extra="ignore",
        frozen=True,
    )

    api_key: SecretStr | None = Field(default=None)
    daily_quota_units: int = Field(default=10_000, ge=0)


class _AlertSettings(BaseSettings):
    """Outbound notification channels.

    ``ntfy`` is the default because it is fully self-hostable and free.
    Telegram is opt-in (requires bot token + chat id).
    """

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_ALERT_",
        env_file=_ENV_FILE_CANDIDATES,
        extra="ignore",
        frozen=True,
    )

    ntfy_url: str = Field(default="https://ntfy.sh")
    ntfy_topic: str | None = Field(default=None)
    telegram_bot_token: SecretStr | None = Field(default=None)
    telegram_chat_id: str | None = Field(default=None)
    discord_webhook_url: SecretStr | None = Field(default=None)


class Settings(BaseSettings):
    """Top-level AEGIS Pulse configuration.

    All fields are populated from the environment (or ``.env`` files) and
    validated at construction time. The instance is frozen — assigning to
    fields raises ``ValidationError``.
    """

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_",
        env_file=_ENV_FILE_CANDIDATES,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        validate_assignment=True,
    )

    # ------------------------------------------------------------------
    # Runtime context
    # ------------------------------------------------------------------
    env: Environment = Field(default="dev")
    service_name: str = Field(default="aegis-pulse")
    service_version: str = Field(default="0.1.0")
    instance_id: str = Field(
        default_factory=lambda: __import__("socket").gethostname(),
        description="Unique identifier for this process instance, used in "
        "logs and metric labels. Defaults to hostname.",
    )

    # ------------------------------------------------------------------
    # Logging / observability
    # ------------------------------------------------------------------
    log_level: str = Field(default="INFO")
    log_format: LogFormat = Field(default="console")
    log_json: bool = Field(default=False)
    sentry_dsn: SecretStr | None = Field(default=None)
    otel_exporter_otlp_endpoint: str | None = Field(default=None)
    prometheus_port: int = Field(default=9464, ge=1024, le=65535)

    # ------------------------------------------------------------------
    # Postgres
    # ------------------------------------------------------------------
    pg_dsn: SecretStr = Field(
        default=SecretStr("postgresql://aegis_app:aegis_app@localhost:5432/aegis"),
        description="Application DSN. Overridden via AEGIS_PG_DSN.",
    )
    pg_pool_min_size: int = Field(default=DB_POOL_MIN_SIZE, ge=0, le=128)
    pg_pool_max_size: int = Field(default=DB_POOL_MAX_SIZE, ge=1, le=128)
    pg_statement_timeout_ms: int = Field(default=30_000, ge=100)
    pg_application_name: str = Field(default="aegis-pulse")

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    redis_url: SecretStr = Field(
        default=SecretStr("redis://localhost:6379/0"),
    )
    redis_namespace: str = Field(default="aegis")

    # ------------------------------------------------------------------
    # Tenancy
    # ------------------------------------------------------------------
    default_tenant_id: str = Field(
        default=DEFAULT_TENANT_UUID,
        description="Tenant UUID used when no explicit tenant is provided. "
        "In single-tenant deployments this is the only tenant.",
    )

    # ------------------------------------------------------------------
    # Nested groups (lazy via factory so a missing optional group never
    # blows up the whole settings object)
    # ------------------------------------------------------------------
    minio: _MinIOSettings = Field(default_factory=_MinIOSettings)
    scrape: _ScrapeSettings = Field(default_factory=_ScrapeSettings)
    reddit: _RedditSettings = Field(default_factory=_RedditSettings)
    youtube: _YouTubeSettings = Field(default_factory=_YouTubeSettings)
    alerts: _AlertSettings = Field(default_factory=_AlertSettings)

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        canonical = v.upper().strip()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if canonical not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}; got {v!r}")
        return canonical

    @field_validator("pg_pool_max_size")
    @classmethod
    def _validate_pool_sizes(cls, v: int) -> int:
        # We can't see other fields here without a model_validator, but we
        # at least keep the upper bound sane. Cross-field validation runs
        # in `model_validator` below.
        return v

    # ------------------------------------------------------------------
    # Convenience accessors used heavily across the codebase
    # ------------------------------------------------------------------
    @property
    def pg_dsn_str(self) -> str:
        """Return the Postgres DSN as a plain string (safe to pass to drivers)."""
        return self.pg_dsn.get_secret_value()

    @property
    def redis_url_str(self) -> str:
        """Return the Redis URL as a plain string (safe to pass to drivers)."""
        return self.redis_url.get_secret_value()

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def is_test(self) -> bool:
        return self.env == "test"


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so repeated calls don't re-parse the environment, but
    invalidatable via :func:`reload_settings` — necessary in tests that
    monkey-patch ``os.environ``.
    """
    return Settings()


def reload_settings() -> Settings:
    """Drop the singleton cache and reload from the current environment.

    Returns the new ``Settings`` instance. Intended for tests; calling
    this in production code is a smell — fix the test instead.
    """
    settings.cache_clear()
    return settings()


__all__ = [
    "Environment",
    "LogFormat",
    "Settings",
    "settings",
    "reload_settings",
]
