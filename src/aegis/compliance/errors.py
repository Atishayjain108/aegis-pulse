"""
aegis.compliance.errors
=======================

Typed error hierarchy for Phase 8 Regulatory & Compliance Engine.

Error codes: AEGIS-COMPLY-0001 .. AEGIS-COMPLY-0099
"""

from __future__ import annotations


class AegisComplianceError(Exception):
    """Base class for all compliance subsystem errors."""

    code: str = "AEGIS-COMPLY-0000"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class TrademarkRiskError(AegisComplianceError):
    """Trademark infringement risk detected — BLOCK recommended."""

    code = "AEGIS-COMPLY-0001"


class PatentRiskError(AegisComplianceError):
    """Active patent found in product scope — BLOCK recommended."""

    code = "AEGIS-COMPLY-0002"


class FDAViolationError(AegisComplianceError):
    """Product matches FDA import ban or active enforcement action."""

    code = "AEGIS-COMPLY-0003"


class CounterfeitRiskError(AegisComplianceError):
    """High counterfeit probability detected (text or image similarity)."""

    code = "AEGIS-COMPLY-0004"


class FTCViolationError(AegisComplianceError):
    """FTC advertising rule violation found in product description."""

    code = "AEGIS-COMPLY-0005"


class PrivacyRiskError(AegisComplianceError):
    """GDPR / DPDP / DSA data-privacy compliance risk detected."""

    code = "AEGIS-COMPLY-0006"


class AMLRiskError(AegisComplianceError):
    """Anti-money-laundering or KYC risk flag raised."""

    code = "AEGIS-COMPLY-0007"


class SanctionViolationError(AegisComplianceError):
    """Transaction involves an OFAC-sanctioned country or entity."""

    code = "AEGIS-COMPLY-0008"


class ComplianceBlockError(AegisComplianceError):
    """Overall risk score exceeds BLOCK threshold — execution rejected."""

    code = "AEGIS-COMPLY-0010"


class ComplianceAPIError(AegisComplianceError):
    """External compliance API (USPTO, FDA, OFAC) returned an error."""

    code = "AEGIS-COMPLY-0020"


class ComplianceCacheError(AegisComplianceError):
    """Compliance result cache read/write failure (non-fatal)."""

    code = "AEGIS-COMPLY-0030"
