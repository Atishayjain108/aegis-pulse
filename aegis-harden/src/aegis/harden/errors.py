"""
Phase 5 typed error codes — AEGIS-HARDEN-NNNN.

Following the same pattern as `aegis.predict.errors`. Every error has:
  * a stable machine code,
  * a human-readable message,
  * an optional `context` dict that is safe to log (no PII).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class HardenError(Exception):
    """Base class for all Phase 5 errors."""

    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover — trivial
        return f"[{self.code}] {self.message}"


# --- Configuration / loading -------------------------------------------------
class PlaybookValidationError(HardenError):
    """Raised when a per-source playbook fails schema or bound checks."""


class FingerprintPoolError(HardenError):
    """Raised when no usable fingerprint is available."""


# --- Honeypot detection ------------------------------------------------------
class HoneypotBlocked(HardenError):
    """Raised when a candidate URL/DOM element is blocked as a honeypot."""


# --- ML defense --------------------------------------------------------------
class SmoothingError(HardenError):
    """Raised by randomized smoothing on invalid inputs."""


class PoisoningDetected(HardenError):
    """Raised when a training batch is rejected by a poisoning detector."""


# --- Codebook ----------------------------------------------------------------
# Use these as `raise PlaybookValidationError(*CODES["AEGIS-HARDEN-0001"])`
CODES: dict[str, tuple[str, str]] = {
    "AEGIS-HARDEN-0001": ("AEGIS-HARDEN-0001", "Playbook missing required key"),
    "AEGIS-HARDEN-0002": ("AEGIS-HARDEN-0002", "Playbook value out of bounds"),
    "AEGIS-HARDEN-0003": ("AEGIS-HARDEN-0003", "Unknown hardening profile"),
    "AEGIS-HARDEN-0004": ("AEGIS-HARDEN-0004", "No matching playbook for URL"),
    "AEGIS-HARDEN-0010": ("AEGIS-HARDEN-0010", "JA3 pool empty or corrupt"),
    "AEGIS-HARDEN-0011": ("AEGIS-HARDEN-0011", "JA4 pool empty or corrupt"),
    "AEGIS-HARDEN-0012": ("AEGIS-HARDEN-0012", "HTTP/2 settings out of bounds"),
    "AEGIS-HARDEN-0020": ("AEGIS-HARDEN-0020", "Honeypot detected pre-click"),
    "AEGIS-HARDEN-0021": ("AEGIS-HARDEN-0021", "Honeypot detected in URL pattern"),
    "AEGIS-HARDEN-0030": ("AEGIS-HARDEN-0030", "Smoothing input has wrong shape"),
    "AEGIS-HARDEN-0031": ("AEGIS-HARDEN-0031", "Smoothing parameters out of bounds"),
    "AEGIS-HARDEN-0032": ("AEGIS-HARDEN-0032", "Smoothing sampler returned NaN"),
    "AEGIS-HARDEN-0040": ("AEGIS-HARDEN-0040", "Label-flip rate exceeds threshold"),
    "AEGIS-HARDEN-0041": ("AEGIS-HARDEN-0041", "Feature shift exceeds threshold"),
    "AEGIS-HARDEN-0042": ("AEGIS-HARDEN-0042", "Sample count below detection floor"),
}


def make(code_id: str, **context: Any) -> tuple[str, str, dict[str, Any]]:
    """Factory: returns the (code, message, context) tuple for an error ctor."""
    code, msg = CODES[code_id]
    return code, msg, context
