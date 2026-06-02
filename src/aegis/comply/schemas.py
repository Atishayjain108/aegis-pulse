"""Canonical, frozen Pydantic v2 schemas for the compliance engine.

All models are immutable after construction (``frozen=True``) to match the
project's ``TrendCandidate`` / ``AgentDecision`` invariant.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Jurisdiction(str, Enum):
    """Supported regulatory jurisdictions."""

    US = "US"
    EU = "EU"
    IN = "IN"  # India (DPDP Act)
    UK = "UK"
    GLOBAL = "GLOBAL"


class Severity(str, Enum):
    """Rule-hit severity. ``BLOCK`` is fail-closed and cannot be downgraded."""

    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class RiskCategory(str, Enum):
    """Compliance risk families fed into the risk matrix."""

    TRADEMARK = "trademark"
    COUNTERFEIT = "counterfeit"
    ADVERTISING = "advertising"
    PRIVACY = "privacy"
    PRODUCT_SAFETY = "product_safety"
    PLATFORM = "platform"


class ComplianceVerdict(str, Enum):
    """Engine output verdict. Maps to Phase 2 vocabulary in the agents bridge."""

    CLEAR = "clear"
    FLAG = "flag"
    BLOCK = "block"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ComplianceRequest(_Frozen):
    """A product/trend candidate to be screened for compliance.

    Fields mirror what the Phase 2 ``TrendCandidate`` and the scrape layer can
    supply. All fields have safe defaults so a minimal ``{"trend_id","title"}``
    request is valid.
    """

    trend_id: str
    title: str
    description: str = ""
    category: str = "general"
    audience: str = "general"  # general | children | adult
    price: float | None = None
    currency: str = "USD"
    claims: tuple[str, ...] = ()  # marketing copy / claim snippets
    brand_mentions: tuple[str, ...] = ()
    target_jurisdictions: tuple[Jurisdiction, ...] = (Jurisdiction.US,)
    endorsement_present: bool = False
    disclosure_present: bool = False
    collects_personal_data: bool = False
    has_privacy_policy: bool = False
    has_age_gate: bool = False
    image_refs: tuple[str, ...] = ()
    price_baseline: tuple[float, float] | None = None  # (mean, std) override
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_jurisdictions", mode="before")
    @classmethod
    def _default_jurisdiction(cls, v: Any) -> Any:
        if not v:
            return (Jurisdiction.US,)
        return v

    @property
    def text(self) -> str:
        """Lowercased haystack used by ``contains_*`` rule conditions."""
        parts = [self.title, self.description, *self.claims, *self.brand_mentions]
        return " ".join(p for p in parts if p).lower()


class RuleHit(_Frozen):
    """A single matched rule."""

    rule_id: str
    name: str
    severity: Severity
    category: RiskCategory
    jurisdiction: Jurisdiction
    citation: str = ""
    remediation: str = ""
    detail: str = ""


class TrademarkMatch(_Frozen):
    """A protected-mark match against the candidate's name/brand mentions."""

    mark: str
    owner: str | None = None
    jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    similarity: float = 0.0
    source: str = "local"  # local | cache | uspto | euipo | wipo
    matched_token: str = ""


class CounterfeitSignal(_Frozen):
    """A counterfeit-risk signal (price anomaly and/or typosquat)."""

    brand: str
    similarity: float
    price_zscore: float | None = None
    reason: str = ""
    risk: float = 0.0  # 0..1


class CategoryScore(_Frozen):
    """Per-category aggregated risk contribution."""

    category: RiskCategory
    score: float  # 0..1
    weight: float
    hits: tuple[RuleHit, ...] = ()


class ComplianceVerdictResult(_Frozen):
    """The complete, deterministic compliance verdict for one request."""

    trend_id: str
    verdict: ComplianceVerdict
    risk_score: float  # 0..1 weighted aggregate
    confidence: float  # 0..1
    category_scores: tuple[CategoryScore, ...] = ()
    rule_hits: tuple[RuleHit, ...] = ()
    trademark_matches: tuple[TrademarkMatch, ...] = ()
    counterfeit_signals: tuple[CounterfeitSignal, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    remediation: tuple[str, ...] = ()
    reasoning: str = ""
    jurisdictions: tuple[Jurisdiction, ...] = ()
    engine_version: str = ""
    augmented: bool = False
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def content_id(self) -> str:
        """Content-addressable SHA-256 id for dedup + audit.

        Hashes the *decision-relevant* fields (not the timestamp) so re-running
        the same request yields a stable id, mirroring the project-wide
        content-addressable id pattern.
        """
        payload = {
            "trend_id": self.trend_id,
            "verdict": self.verdict.value,
            "risk_bucket": round(self.risk_score, 2),
            "rules": sorted(h.rule_id for h in self.rule_hits),
            "tm": sorted(m.mark for m in self.trademark_matches),
            "cf": sorted(s.brand for s in self.counterfeit_signals),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
