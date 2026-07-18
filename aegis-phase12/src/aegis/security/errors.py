"""
aegis.security.errors — Centralised error catalogue for Phase 12.

Every error has:
    - A machine code  (AEGIS-SEC-NNNN)
    - A human message template
    - A docs URL
    - HTTP status hint for API responses

Usage::

    from aegis.security.errors import SecurityError, Codes

    raise SecurityError(Codes.SECRET_NOT_FOUND, path="aegis/db/password")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DOCS_BASE = "https://aegis.internal/docs/errors"


@dataclass(frozen=True)
class ErrorCode:
    """Immutable error code descriptor."""

    code: str          # e.g. "AEGIS-SEC-0001"
    message: str       # human message template (use .format(**kwargs))
    http_status: int   # suggested HTTP status code
    category: str      # vault / secrets / pii / audit / rbac / jwt / tls / ratelimit / sops / api

    @property
    def docs_url(self) -> str:
        return f"{DOCS_BASE}/{self.code}"

    def format(self, **kwargs: Any) -> str:  # noqa: ANN401
        """Return the formatted message with context substituted."""
        try:
            return self.message.format(**kwargs)
        except KeyError:
            return self.message

    def to_dict(self, **context: Any) -> dict[str, Any]:  # noqa: ANN401
        """Serialise to a dict suitable for API error responses."""
        return {
            "error_code": self.code,
            "message": self.format(**context),
            "http_status": self.http_status,
            "docs": self.docs_url,
        }


class Codes:
    """All Phase 12 error codes as class attributes."""

    # -- Vault (0001-0020) ──────────────────────────────────────────────── #
    SECRET_NOT_FOUND = ErrorCode(
        "AEGIS-SEC-0001", "Secret not found: {path}", 404, "vault"
    )
    PERMISSION_DENIED = ErrorCode(
        "AEGIS-SEC-0002", "Vault permission denied on path: {path}", 403, "vault"
    )
    NETWORK_ERROR = ErrorCode(
        "AEGIS-SEC-0003", "Vault unreachable after {retries} retries: {detail}", 503, "vault"
    )
    API_ERROR = ErrorCode(
        "AEGIS-SEC-0004", "Vault returned HTTP {status}: {body}", 502, "vault"
    )
    CIRCUIT_OPEN = ErrorCode(
        "AEGIS-SEC-0005", "Vault circuit breaker is OPEN for {addr}", 503, "vault"
    )
    TOKEN_EXPIRED = ErrorCode(
        "AEGIS-SEC-0006", "Vault token expired — renew or re-authenticate", 401, "vault"
    )
    SEAL_ERROR = ErrorCode(
        "AEGIS-SEC-0007", "Vault is sealed — unseal before use", 503, "vault"
    )
    TRANSIT_KEY_MISSING = ErrorCode(
        "AEGIS-SEC-0008", "Transit key not found: {key}", 404, "vault"
    )
    DECODE_ERROR = ErrorCode(
        "AEGIS-SEC-0009", "Failed to decode Vault response: {detail}", 502, "vault"
    )
    TIMEOUT = ErrorCode(
        "AEGIS-SEC-0010", "Vault request timed out after {timeout_s}s", 504, "vault"
    )

    # -- Secrets Manager (0021-0030) ────────────────────────────────────── #
    SECRET_KEY_NOT_FOUND = ErrorCode(
        "AEGIS-SEC-0021",
        "Secret not found: key='{key}', env='{env}'. "
        "Check Vault path, .env.sops.yaml, or export {env}=<value>.",
        404, "secrets"
    )
    VAULT_WRITE_UNAVAILABLE = ErrorCode(
        "AEGIS-SEC-0022",
        "Cannot write secret: Vault is not connected. "
        "Start Vault or run 'aegis doctor'.",
        503, "secrets"
    )
    VAULT_CIPHERTEXT_NO_VAULT = ErrorCode(
        "AEGIS-SEC-0023",
        "Received Vault ciphertext but Vault is unavailable. "
        "Start Vault to decrypt this value.",
        503, "secrets"
    )
    UNKNOWN_CIPHER_FORMAT = ErrorCode(
        "AEGIS-SEC-0024", "Unknown ciphertext format: {prefix}", 400, "secrets"
    )

    # -- PII Scrubber (0031-0040) ───────────────────────────────────────── #
    PII_SPACY_MISSING = ErrorCode(
        "AEGIS-SEC-0031",
        "spaCy not installed — NER pass disabled. "
        "Install with: pip install spacy && python -m spacy download {model}",
        503, "pii"
    )
    PII_SPACY_MODEL_MISSING = ErrorCode(
        "AEGIS-SEC-0032",
        "spaCy model '{model}' not found. "
        "Run: python -m spacy download {model}",
        503, "pii"
    )

    # -- Audit Logger (0041-0050) ───────────────────────────────────────── #
    AUDIT_WRITE_FAILED = ErrorCode(
        "AEGIS-SEC-0041", "Audit log write failed: {detail}", 500, "audit"
    )
    AUDIT_CHAIN_BROKEN = ErrorCode(
        "AEGIS-SEC-0042", "Audit chain broken at seq={seq}: {detail}", 500, "audit"
    )
    AUDIT_HMAC_MISMATCH = ErrorCode(
        "AEGIS-SEC-0043", "Audit HMAC mismatch at seq={seq} — log may be tampered", 500, "audit"
    )
    AUDIT_MINIO_FAILED = ErrorCode(
        "AEGIS-SEC-0044", "Audit MinIO upload failed: {detail}", 503, "audit"
    )

    # -- RBAC (0051-0060) ──────────────────────────────────────────────── #
    MISSING_AUTH_HEADER = ErrorCode(
        "AEGIS-SEC-0051", "Missing Authorization header (expected 'Bearer <token>')", 401, "rbac"
    )
    INVALID_TOKEN = ErrorCode(
        "AEGIS-SEC-0052", "Invalid or expired token: {detail}", 401, "rbac"
    )
    PERMISSION_DENIED_RBAC = ErrorCode(
        "AEGIS-SEC-0053",
        "Role '{role}' lacks permission '{permission}'",
        403, "rbac"
    )
    UNKNOWN_ROLE = ErrorCode(
        "AEGIS-SEC-0054", "Unknown role: '{role}'", 400, "rbac"
    )

    # -- JWT (0061-0070) ───────────────────────────────────────────────── #
    WRONG_TOKEN_TYPE_ACCESS = ErrorCode(
        "AEGIS-SEC-0061", "Expected access token, got refresh token", 401, "jwt"
    )
    WRONG_TOKEN_TYPE_REFRESH = ErrorCode(
        "AEGIS-SEC-0062", "Expected refresh token, got access token", 401, "jwt"
    )
    TOKEN_EXPIRED_JWT = ErrorCode(
        "AEGIS-SEC-0063", "JWT token expired at {exp}", 401, "jwt"
    )
    TOKEN_INVALID_JWT = ErrorCode(
        "AEGIS-SEC-0064", "JWT token invalid: {detail}", 401, "jwt"
    )

    # -- TLS / mkcert (0071-0080) ──────────────────────────────────────── #
    MKCERT_NOT_FOUND = ErrorCode(
        "AEGIS-SEC-0071",
        "mkcert not found. Install with: apt install mkcert OR brew install mkcert",
        503, "tls"
    )
    MKCERT_CERT_GEN_FAILED = ErrorCode(
        "AEGIS-SEC-0072", "mkcert failed to generate certificate: {detail}", 500, "tls"
    )
    CERT_NEAR_EXPIRY = ErrorCode(
        "AEGIS-SEC-0073", "TLS certificate expires in {days} days — regenerate soon", 200, "tls"
    )

    # -- Rate Limiting (0081-0090) ─────────────────────────────────────── #
    RATE_LIMIT_EXCEEDED = ErrorCode(
        "AEGIS-SEC-0081",
        "Rate limit exceeded. Retry after {retry_after_s}s.",
        429, "ratelimit"
    )
    RATE_LIMIT_REDIS_ERROR = ErrorCode(
        "AEGIS-SEC-0082", "Rate limiter Redis error (fail-open): {detail}", 200, "ratelimit"
    )

    # -- SOPS / age (0091-0100) ────────────────────────────────────────── #
    AGE_KEY_EXISTS = ErrorCode(
        "AEGIS-SEC-0091",
        "Age key already exists at {path}. Use force=True to overwrite.",
        409, "sops"
    )
    AGE_KEYGEN_NOT_FOUND = ErrorCode(
        "AEGIS-SEC-0092",
        "age-keygen not found. Install with: apt install age OR brew install age",
        503, "sops"
    )
    AGE_KEY_NOT_FOUND = ErrorCode(
        "AEGIS-SEC-0093",
        "Age key not found at {path}. Run generate_age_key() first.",
        404, "sops"
    )
    AGE_KEY_PARSE_FAILED = ErrorCode(
        "AEGIS-SEC-0094", "Could not parse public key from {path}", 500, "sops"
    )
    SOPS_FILE_NOT_FOUND = ErrorCode(
        "AEGIS-SEC-0095", "SOPS encrypted file not found: {path}", 404, "sops"
    )

    # -- API / Auth (0101-0120) ────────────────────────────────────────── #
    INVALID_CREDENTIALS = ErrorCode(
        "AEGIS-SEC-0101", "Invalid username or password", 401, "api"
    )
    TOKEN_ROTATION_FAILED = ErrorCode(
        "AEGIS-SEC-0102", "Token rotation failed: {detail}", 401, "api"
    )


class SecurityError(Exception):
    """Typed security exception wrapping an ``ErrorCode``.

    Usage::

        raise SecurityError(Codes.SECRET_NOT_FOUND, path="aegis/db")

        try:
            ...
        except SecurityError as exc:
            return JSONResponse(exc.to_dict(), status_code=exc.http_status)
    """

    def __init__(self, code: ErrorCode, **context: Any) -> None:  # noqa: ANN401
        self.code = code
        self.context = context
        super().__init__(f"[{code.code}] {code.format(**context)}")

    @property
    def http_status(self) -> int:
        return self.code.http_status

    def to_dict(self) -> dict[str, Any]:
        return self.code.to_dict(**self.context)
