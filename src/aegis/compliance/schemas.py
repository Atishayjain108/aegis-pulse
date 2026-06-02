"""Pydantic v2 frozen models for Phase 8 Compliance Engine."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class Recommendation(str, Enum):
    PROCEED = "PROCEED"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"


# ---------------------------------------------------------------------------
# IPR
# ---------------------------------------------------------------------------

class TrademarkMatch(BaseModel, frozen=True):
    """Trademark registration match from USPTO / EUIPO / WIPO."""

    registered_mark: str
    registration_number: str
    owner: str
    status: str                 # "registered" | "pending" | "expired"
    goods_services: str
    jurisdiction: str           # "US" | "EU" | "WO" etc.
    confidence_score: float = Field(ge=0.0, le=1.0)
    source: str = "euipo_tmview"


class PatentMatch(BaseModel, frozen=True):
    """Patent record from USPTO PatentsView or WIPO PatentScope."""

    patent_number: str
    patent_title: str
    abstract: str = ""
    inventor: str = ""
    grant_date: str = ""
    status: str                 # "granted" | "pending" | "expired"
    similarity_score: float = Field(ge=0.0, le=1.0)
    source: str = "patentsview"


# ---------------------------------------------------------------------------
# FDA
# ---------------------------------------------------------------------------

class FDAEnforcement(BaseModel, frozen=True):
    """FDA enforcement action (recall / import alert)."""

    recall_number: str
    reason: str
    product_description: str
    classification: str = ""    # "Class I" | "Class II" | "Class III"
    status: str = "Ongoing"
    distribution: str = ""
    source: str = "openfda"


# ---------------------------------------------------------------------------
# AML / Sanctions
# ---------------------------------------------------------------------------

class SanctionMatch(BaseModel, frozen=True):
    """Match against OFAC SDN list or FATF high-risk list."""

    match_type: str             # "country_sanction" | "fatf_high_risk" | "entity_match"
    matched_value: str          # country code or entity name
    program: str = ""           # OFAC sanctions program (e.g. "IRAN", "UKRAINE-EO13685")
    source: str                 # "ofac_sdn" | "fatf_grey_list" | "trade_gov_csl"
    risk_score: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Counterfeit
# ---------------------------------------------------------------------------

class CounterfeitSignal(BaseModel, frozen=True):
    """Individual counterfeit indicator."""

    signal_type: str            # "brand_match" | "fuzzy_brand" | "price_anomaly" | "clip_similarity"
    description: str
    matched_brand: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# FTC
# ---------------------------------------------------------------------------

class FTCViolation(BaseModel, frozen=True):
    """FTC advertising rule violation found in product text."""

    violation_type: str
    matched_text: str
    rule_reference: str = ""
    severity: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------

class PrivacyRisk(BaseModel, frozen=True):
    """Privacy regulation risk assessment."""

    regulations_triggered: list[str]   # e.g. ["GDPR", "DSA", "DPDP"]
    jurisdiction: str
    risk_level: str                     # "low" | "medium" | "high"
    details: str = ""


# ---------------------------------------------------------------------------
# Main request / response
# ---------------------------------------------------------------------------

class ComplianceRequest(BaseModel, frozen=True):
    """Input to ComplianceEngine.assess()."""

    product_sku: str
    product_title: str
    product_description: str = ""
    product_image_url: str | None = None
    category: str = "general"
    origin_country: str = "US"
    destination_country: str = "US"
    price_usd: float | None = None
    tenant_id: str = "00000000-0000-0000-0000-000000000001"
    extra: dict[str, Any] = Field(default_factory=dict)


class RiskBreakdown(BaseModel, frozen=True):
    """Per-dimension risk scores."""

    trademark: float = 0.0
    patent: float = 0.0
    fda: float = 0.0
    counterfeit: float = 0.0
    ftc: float = 0.0
    privacy: float = 0.0
    aml: float = 0.0


class ComplianceRiskAssessment(BaseModel, frozen=True):
    """Full compliance risk assessment result."""

    assessment_id: str = Field(default_factory=lambda: str(uuid4()))
    product_sku: str
    overall_risk_score: float = Field(ge=0.0, le=1.0)
    risk_breakdown: RiskBreakdown
    recommendation: Recommendation
    reasons: list[str] = Field(default_factory=list)

    trademark_matches: list[TrademarkMatch] = Field(default_factory=list)
    patent_matches: list[PatentMatch] = Field(default_factory=list)
    fda_enforcements: list[FDAEnforcement] = Field(default_factory=list)
    sanction_matches: list[SanctionMatch] = Field(default_factory=list)
    counterfeit_signals: list[CounterfeitSignal] = Field(default_factory=list)
    ftc_violations: list[FTCViolation] = Field(default_factory=list)
    privacy_risk: PrivacyRisk | None = None

    assessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float = 0.0
    cached: bool = False

    error_code: str | None = None
