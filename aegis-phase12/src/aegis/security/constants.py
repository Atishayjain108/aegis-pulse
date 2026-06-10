"""
aegis.security.constants — All magic numbers as named constants.

Every constant has a rationale comment explaining the value choice.
Import from here; never hardcode numbers in production code.

Usage::

    from aegis.security.constants import JWT_MIN_SECRET_LEN, VAULT_DEFAULT_RETRIES
"""

from __future__ import annotations

# ── Vault ─────────────────────────────────────────────────────────────────── #

VAULT_DEFAULT_TIMEOUT_S: float = 5.0
"""HTTP timeout for Vault API calls.
Rationale: 5s balances latency SLA vs slow-network tolerance.
Vault health p99 latency on LAN is <50ms; 5s allows for transient load spikes."""

VAULT_DEFAULT_RETRIES: int = 3
"""Retry attempts for transient Vault failures.
Rationale: 3 retries with exponential backoff (0.5, 1.0, 2.0s) gives ~4s total
wait before hard failure — acceptable for startup paths, not hot paths."""

VAULT_RETRY_BASE_S: float = 0.5
"""Base wait for first Vault retry (seconds).
Rationale: 500ms avoids thundering herd while being fast enough for human UX."""

VAULT_RETRY_MAX_S: float = 16.0
"""Maximum wait between Vault retries (seconds).
Rationale: caps at 16s to prevent requests queuing indefinitely."""

VAULT_TOKEN_RENEW_BEFORE_S: int = 60
"""Seconds before token expiry to trigger renewal.
Rationale: 60s buffer covers 1 renewal attempt + 2 retries at max backoff."""

VAULT_CB_ERROR_RATE_THRESHOLD: float = 0.30
"""Circuit breaker trips when error rate exceeds this fraction.
Rationale: 30% error rate signals a systemic problem, not random transients."""

VAULT_CB_WINDOW_S: int = 60
"""Sliding window width for circuit breaker error rate calculation (seconds)."""

VAULT_CB_HALF_OPEN_AFTER_S: int = 120
"""Seconds after circuit trips before a probe request is allowed.
Rationale: 120s gives Vault time to recover from a restart or leader election."""

VAULT_CB_MIN_REQUESTS: int = 5
"""Minimum requests before circuit breaker can trip.
Rationale: prevents false trips during startup when sample size is tiny."""

# ── JWT ───────────────────────────────────────────────────────────────────── #

JWT_MIN_SECRET_LEN: int = 32
"""Minimum length of the JWT HMAC-SHA256 secret (bytes).
Rationale: NIST SP 800-107 recommends key length ≥ hash output length (32 bytes for SHA-256)."""

JWT_DEFAULT_ACCESS_TTL_MINUTES: int = 15
"""Default access token lifetime in minutes.
Rationale: Short TTL limits window of opportunity for stolen tokens.
15 min is standard (OAuth 2.0 best practice)."""

JWT_DEFAULT_REFRESH_TTL_DAYS: int = 7
"""Default refresh token lifetime in days.
Rationale: 7 days matches typical session length for developer tools.
Refresh tokens are rotated on each use."""

JWT_ALGORITHM: str = "HS256"
"""Default JWT signing algorithm.
Rationale: HS256 is sufficient for single-service JWT verification.
Migrate to RS256 when tokens are verified by multiple independent services."""

# ── Rate limiting ─────────────────────────────────────────────────────────── #

RATE_LIMIT_DEFAULT_RPM: int = 60
"""Default rate limit: requests per minute per IP.
Rationale: 60 rpm = 1 rps is generous for human users, blocks naive scrapers."""

RATE_LIMIT_DEFAULT_BURST: int = 10
"""Token bucket burst capacity above steady rate.
Rationale: allows legitimate bursts (page load with multiple API calls)
without granting sustained high-rate access."""

RATE_LIMIT_REDIS_PREFIX: str = "aegis:ratelimit:"
"""Redis key prefix for rate-limit token buckets."""

RATE_LIMIT_KEY_TTL_BUFFER_S: int = 10
"""Extra TTL beyond bucket-drain time to allow for clock skew."""

# ── PII scrubbing ─────────────────────────────────────────────────────────── #

PII_DEFAULT_PLACEHOLDER: str = "[REDACTED]"
"""Replacement string for detected PII values."""

PII_HASH_PREFIX: str = "h:"
"""Prefix added to hashed PII values so consumers can identify them."""

PII_EMAIL_HASH_LEN: int = 16
"""Truncated SHA-256 length (hex chars) for hashed email addresses.
Rationale: 16 hex chars = 64-bit collision resistance, sufficient for dedup."""

# ── Audit logging ─────────────────────────────────────────────────────────── #

AUDIT_UPLOAD_BATCH_MAX: int = 100
"""Maximum entries per MinIO upload batch.
Rationale: 100 entries ≈ 50KB JSON; well within S3 PutObject size limits."""

AUDIT_UPLOAD_TIMEOUT_S: float = 5.0
"""Timeout waiting for a batch to accumulate before uploading."""

AUDIT_QUEUE_MAX_SIZE: int = 1000
"""Maximum in-memory audit upload queue depth.
Rationale: 1000 entries provide ~10s of buffering at 100 events/s."""

AUDIT_DRAIN_TIMEOUT_S: float = 10.0
"""Seconds to wait for audit queue drain on shutdown."""

AUDIT_GENESIS_HASH: str = "0" * 64
"""SHA-256 placeholder for the first log entry's prev_hash field."""

# ── HMAC signing ─────────────────────────────────────────────────────────── #

HMAC_ALGORITHM: str = "sha256"
"""HMAC algorithm for inter-service message signing."""

HMAC_SIGNATURE_HEX_LEN: int = 64
"""Expected hex length of a valid HMAC-SHA256 signature."""

HMAC_MAX_MESSAGE_AGE_S: int = 300
"""Maximum age of a timestamp-bound signed message (seconds).
Rationale: 5-minute window tolerates NTP drift while blocking replay attacks."""

# ── SOPS / age ────────────────────────────────────────────────────────────── #

SOPS_DEFAULT_AGE_KEY_PATH: str = "~/.config/sops/age/keys.txt"
"""Default path for the age private key file."""

SOPS_ENV_FILE_NAME: str = ".env.sops.yaml"
"""Conventional filename for SOPS-encrypted environment variables."""

AGE_KEY_FILE_PERMISSIONS: int = 0o600
"""Required file permissions for the age private key (owner read/write only)."""

# ── TLS / mkcert ─────────────────────────────────────────────────────────── #

TLS_CERT_WARN_DAYS: int = 30
"""Warn when a certificate expires within this many days."""

TLS_DEFAULT_CERT_NAME: str = "server"
"""Default base filename for generated TLS cert/key pairs."""

HSTS_MAX_AGE_S: int = 31_536_000
"""HSTS max-age in seconds (1 year).
Rationale: Chrome and Firefox enforce preloading at 1 year minimum."""

# ── Secret scanning ───────────────────────────────────────────────────────── #

SECRET_SCAN_BASELINE_FILE: str = ".secrets.baseline"
"""Path to the detect-secrets baseline file."""

SECRET_SCAN_EXCLUDE_PATTERNS: list[str] = [
    r"\.sops\.yaml$",
    r"\.env\.example$",
    r"tests/.*",
    r"docs/.*",
]
"""Glob patterns excluded from secret scanning.
Rationale: SOPS files are intentionally encrypted; examples are placeholders."""

# ── Error code ranges ─────────────────────────────────────────────────────── #

ERROR_PREFIX: str = "AEGIS-SEC"

ERROR_VAULT_MIN: int = 1
ERROR_VAULT_MAX: int = 20

ERROR_SECRETS_MIN: int = 21
ERROR_SECRETS_MAX: int = 30

ERROR_PII_MIN: int = 31
ERROR_PII_MAX: int = 40

ERROR_AUDIT_MIN: int = 41
ERROR_AUDIT_MAX: int = 50

ERROR_RBAC_MIN: int = 51
ERROR_RBAC_MAX: int = 60

ERROR_JWT_MIN: int = 61
ERROR_JWT_MAX: int = 70

ERROR_TLS_MIN: int = 71
ERROR_TLS_MAX: int = 80

ERROR_RATELIMIT_MIN: int = 81
ERROR_RATELIMIT_MAX: int = 90

ERROR_SOPS_MIN: int = 91
ERROR_SOPS_MAX: int = 100

ERROR_API_MIN: int = 101
ERROR_API_MAX: int = 120
