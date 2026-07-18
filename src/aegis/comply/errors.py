"""Typed error hierarchy for the compliance engine.

Every error carries a stable machine code (``AEGIS-COMPLY-NNNN``) so operators
can look up diagnosis + remediation. Codes are never reused.
"""

from __future__ import annotations


class AegisComplyError(Exception):
    """Base class for all Phase 8 compliance errors.

    Attributes
    ----------
    code:
        Stable ``AEGIS-COMPLY-NNNN`` identifier.
    message:
        Human-readable description.
    """

    code: str = "AEGIS-COMPLY-0000"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        self.message = message or self.__doc__ or self.__class__.__name__
        if code is not None:
            self.code = code
        super().__init__(f"[{self.code}] {self.message}")


class RuleLoadError(AegisComplyError):
    """A ruleset YAML file is missing, malformed, or fails schema validation."""

    code = "AEGIS-COMPLY-0001"


class ConditionEvalError(AegisComplyError):
    """A rule condition tree is structurally invalid or references an unknown op."""

    code = "AEGIS-COMPLY-0002"


class InvalidRequestError(AegisComplyError):
    """A compliance request could not be coerced into the canonical schema."""

    code = "AEGIS-COMPLY-0003"


class TrademarkLookupError(AegisComplyError):
    """A live trademark lookup failed (network/circuit). Falls back to local screen."""

    code = "AEGIS-COMPLY-0010"


class TrademarkCircuitOpenError(TrademarkLookupError):
    """The live trademark lookup circuit is open after repeated failures."""

    code = "AEGIS-COMPLY-0011"


class CounterfeitConfigError(AegisComplyError):
    """Counterfeit detector configuration (baselines/brands) is invalid."""

    code = "AEGIS-COMPLY-0020"


class AuditWriteError(AegisComplyError):
    """Writing a compliance audit row to Postgres failed (best-effort, non-fatal)."""

    code = "AEGIS-COMPLY-0030"


class AugmentationError(AegisComplyError):
    """LLM augmentation failed; the deterministic verdict is returned unchanged."""

    code = "AEGIS-COMPLY-0040"
