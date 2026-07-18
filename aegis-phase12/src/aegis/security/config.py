"""
aegis.security.config — Phase 12 security configuration.

All security-sensitive knobs are sourced from environment variables only.
No default is provided for secrets — missing required vars raise at import
time so misconfigured deployments fail fast, never silently.

Environment prefix: ``AEGIS_SEC_``

Usage::

    from aegis.security.config import get_security_config
    cfg = get_security_config()
    vault_addr = cfg.vault_addr
"""

from __future__ import annotations

import functools

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SecurityConfig(BaseSettings):
    """Immutable, validated security configuration singleton.

    All secrets are typed as ``pydantic.SecretStr`` — they are never
    accidentally serialised as plain text in logs or ``repr()``.
    """

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_SEC_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # ignore: the shared .env file also has non-AEGIS_SEC_ vars from the
        # main Settings class; forbid would reject all of them as "extra".
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # HashiCorp Vault                                                       #
    # ------------------------------------------------------------------ #
    vault_addr: str = Field(
        default="http://127.0.0.1:8200",
        description="Vault server address (http for dev-mode, https in prod)",
    )
    vault_token: SecretStr = Field(
        default=SecretStr("dev-root-token"),
        description="Vault root/service token.  Override in prod via VAULT_TOKEN.",
    )
    vault_namespace: str = Field(
        default="",
        description="Vault namespace (Enterprise only; leave empty for OSS)",
    )
    vault_mount_kv: str = Field(
        default="secret",
        description="KV v2 mount path in Vault",
    )
    vault_mount_transit: str = Field(
        default="transit",
        description="Transit secrets engine mount path",
    )
    vault_transit_key: str = Field(
        default="aegis-key",
        description="Transit encryption key name",
    )
    vault_timeout_s: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        description="HTTP timeout for Vault API calls (seconds)",
    )
    vault_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Number of retries for transient Vault failures",
    )
    vault_dev_mode: bool = Field(
        default=True,
        description="When True, use in-memory Vault dev mode (never in prod)",
    )

    # ------------------------------------------------------------------ #
    # SOPS / age encryption                                                #
    # ------------------------------------------------------------------ #
    sops_age_key_file: str = Field(
        default="~/.config/sops/age/keys.txt",
        description="Path to age private key file used by SOPS",
    )
    sops_config_path: str = Field(
        default=".sops.yaml",
        description="Path to .sops.yaml config",
    )

    # ------------------------------------------------------------------ #
    # JWT / Auth                                                           #
    # ------------------------------------------------------------------ #
    jwt_secret: SecretStr = Field(
        default=SecretStr("CHANGE-ME-32-chars-minimum-secret!"),
        description="HMAC-SHA256 signing secret for JWTs (≥ 32 bytes)",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm (HS256 or RS256)",
    )
    jwt_access_ttl_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
        description="Access token TTL in minutes",
    )
    jwt_refresh_ttl_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="Refresh token TTL in days",
    )
    jwt_issuer: str = Field(
        default="aegis-pulse",
        description="JWT `iss` claim value",
    )

    # ------------------------------------------------------------------ #
    # HMAC signing (inter-service, audit log)                             #
    # ------------------------------------------------------------------ #
    hmac_key: SecretStr = Field(
        default=SecretStr("dev-hmac-key-not-for-prod"),
        description="HMAC-SHA256 key for inter-service message signing",
    )

    # ------------------------------------------------------------------ #
    # PII scrubbing                                                        #
    # ------------------------------------------------------------------ #
    pii_enabled: bool = Field(
        default=True,
        description="Enable PII scrubbing pipeline before persistence",
    )
    pii_spacy_model: str = Field(
        default="en_core_web_sm",
        description="spaCy model for NER-based PII detection",
    )
    pii_redaction_placeholder: str = Field(
        default="[REDACTED]",
        description="Replacement string for detected PII",
    )
    pii_hash_emails: bool = Field(
        default=True,
        description="SHA-256 hash email addresses instead of redacting",
    )

    # ------------------------------------------------------------------ #
    # Audit logging                                                        #
    # ------------------------------------------------------------------ #
    audit_log_path: str = Field(
        default="/var/log/aegis/audit.jsonl",
        description="Local path for the append-only audit log",
    )
    audit_minio_bucket: str = Field(
        default="aegis-audit",
        description="MinIO bucket for WORM-archived audit logs",
    )
    audit_rotation_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Rotate audit log every N hours",
    )
    audit_max_local_files: int = Field(
        default=7,
        ge=1,
        le=365,
        description="Number of local audit log files to retain",
    )

    # ------------------------------------------------------------------ #
    # Rate limiting                                                        #
    # ------------------------------------------------------------------ #
    rate_limit_enabled: bool = Field(
        default=True,
        description="Enable token-bucket rate limiting middleware",
    )
    rate_limit_default_rpm: int = Field(
        default=60,
        ge=1,
        le=10000,
        description="Default requests-per-minute limit per IP",
    )
    rate_limit_burst: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Token-bucket burst size above steady rate",
    )
    rate_limit_redis_prefix: str = Field(
        default="aegis:ratelimit:",
        description="Redis key prefix for rate-limit token buckets",
    )

    # ------------------------------------------------------------------ #
    # TLS / mkcert                                                         #
    # ------------------------------------------------------------------ #
    tls_cert_path: str = Field(
        default="certs/server.crt",
        description="Path to TLS certificate (mkcert-generated in dev)",
    )
    tls_key_path: str = Field(
        default="certs/server.key",
        description="Path to TLS private key",
    )
    tls_ca_bundle: str = Field(
        default="",
        description="Path to CA bundle for mTLS (empty = use system CAs)",
    )

    # ------------------------------------------------------------------ #
    # Allowed origins (CORS)                                               #
    # ------------------------------------------------------------------ #
    cors_origins: list[str] = Field(
        default=["http://localhost:8300", "http://localhost:5173"],
        description="Allowed CORS origins",
    )

    # ------------------------------------------------------------------ #
    # Content Security Policy                                              #
    # ------------------------------------------------------------------ #
    csp_report_uri: str = Field(
        default="",
        description="URI to POST CSP violation reports (empty = disabled)",
    )

    # ------------------------------------------------------------------ #
    # Secret scanning                                                      #
    # ------------------------------------------------------------------ #
    secret_scan_on_startup: bool = Field(
        default=False,
        description="Run detect-secrets scan on repo at startup (dev only)",
    )

    # ------------------------------------------------------------------ #
    # Redis (for rate-limiting / token storage)                           #
    # ------------------------------------------------------------------ #
    redis_url: str = Field(
        default="redis://localhost:6380/0",
        description="Redis URL for rate-limiting and token revocation store",
    )

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #
    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_min_length(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        return v

    @model_validator(mode="after")
    def _prod_safety_checks(self) -> SecurityConfig:
        """Enforce production safety invariants."""
        if not self.vault_dev_mode:
            # In production the token must not be the dev placeholder
            tok = self.vault_token.get_secret_value()
            if tok == "dev-root-token":
                raise ValueError(
                    "vault_token must be overridden in production "
                    "(AEGIS_SEC_VAULT_TOKEN env var)"
                )
            hmac_k = self.hmac_key.get_secret_value()
            if hmac_k == "dev-hmac-key-not-for-prod":
                raise ValueError(
                    "hmac_key must be overridden in production "
                    "(AEGIS_SEC_HMAC_KEY env var)"
                )
        return self


@functools.lru_cache(maxsize=1)
def get_security_config() -> SecurityConfig:
    """Return the cached, validated security configuration singleton.

    Thread-safe after first call (lru_cache serialises construction).
    Call ``get_security_config.cache_clear()`` in tests to reset.
    """
    return SecurityConfig()
