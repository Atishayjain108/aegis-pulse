"""
aegis.security.vault._errors — Typed Vault error hierarchy.

Every error has a machine-readable code (AEGIS-SEC-0001 .. AEGIS-SEC-0020)
that maps to a remediation document in ``docs/errors/``.
"""

from __future__ import annotations

from enum import Enum


class VaultErrorCode(str, Enum):
    """Machine-readable Vault error codes."""

    NOT_FOUND = "AEGIS-SEC-0001"
    PERMISSION_DENIED = "AEGIS-SEC-0002"
    NETWORK_ERROR = "AEGIS-SEC-0003"
    API_ERROR = "AEGIS-SEC-0004"
    CIRCUIT_OPEN = "AEGIS-SEC-0005"
    TOKEN_EXPIRED = "AEGIS-SEC-0006"  # noqa: S105
    SEAL_ERROR = "AEGIS-SEC-0007"
    TRANSIT_KEY_MISSING = "AEGIS-SEC-0008"
    DECODE_ERROR = "AEGIS-SEC-0009"
    TIMEOUT = "AEGIS-SEC-0010"


class VaultError(Exception):
    """Raised by ``VaultClient`` on any Vault-related failure.

    Attributes
    ----------
    code:
        Machine-readable error code from ``VaultErrorCode``.
    message:
        Human-readable description.
    status:
        HTTP status code returned by Vault (0 if not applicable).
    """

    def __init__(
        self,
        code: VaultErrorCode,
        message: str,
        *,
        status: int = 0,
    ) -> None:
        super().__init__(f"[{code.value}] {message}")
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict[str, object]:
        """Serialise for structured logging / API error responses."""
        return {
            "error_code": self.code.value,
            "message": self.message,
            "http_status": self.status,
            "docs": f"https://aegis.internal/docs/errors/{self.code.value}",
        }
