"""
aegis.llm.instructor.schemas — Shared pydantic schemas for typed LLM outputs
=============================================================================

All agent nodes that use ``InstructorAdapter.complete()`` import their
target schema from this module.  This ensures schema consistency across
the pipeline and makes schema evolution traceable.

Every schema is:
  - ``frozen=True`` — immutable after construction
  - Fully type-annotated
  - Validated via pydantic v2

Author: AEGIS Engineering
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Shared enumerations
# ---------------------------------------------------------------------------


class Verdict(str, Enum):
    """Agent decision verdict, aligned with Phase 2 AgentVerdict."""

    PROCEED = "PROCEED"   # Strong signal — act
    HOLD = "HOLD"         # Promising but needs more data
    BLOCK = "BLOCK"       # Do not trade


class Priority(str, Enum):
    """Opportunity priority tier."""

    P0 = "P0"   # Breakout — act within 24h
    P1 = "P1"   # Strong — act within 72h
    P2 = "P2"   # Standard
    P3 = "P3"   # Weak / monitor


class RiskLevel(str, Enum):
    """Risk classification."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Scout agent schema
# ---------------------------------------------------------------------------


class ScoutOutput(BaseModel, frozen=True):
    """Structured output from the SCOUT agent."""

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    priority: Priority
    rationale: str = Field(max_length=500)
    opportunity_window_hours: int = Field(ge=0, le=720)
    primary_risk: str = Field(max_length=200)

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(v, 4)


# ---------------------------------------------------------------------------
# Sentinel agent schema
# ---------------------------------------------------------------------------


class SentinelOutput(BaseModel, frozen=True):
    """Structured output from the SENTINEL agent."""

    verdict: Verdict
    saturation_pct: float = Field(ge=0.0, le=100.0, description="0=fresh, 100=fully saturated")
    exit_recommendation: str = Field(
        description="EXIT_NOW | EXIT_SOON | HOLD_MONITOR | FALSE_ALARM"
    )
    days_until_peak_estimate: int = Field(ge=0)
    rationale: str = Field(max_length=400)


# ---------------------------------------------------------------------------
# Auditor agent schema
# ---------------------------------------------------------------------------


class AuditorOutput(BaseModel, frozen=True):
    """Structured output from the AUDITOR financial modelling agent."""

    verdict: Verdict
    estimated_margin_pct: float = Field(ge=-100.0, le=1000.0)
    break_even_units: int = Field(ge=0)
    max_recommended_inventory: int = Field(ge=0)
    risk_level: RiskLevel
    monte_carlo_p10_margin: float  # 10th percentile margin
    monte_carlo_p90_margin: float  # 90th percentile margin
    rationale: str = Field(max_length=400)


# ---------------------------------------------------------------------------
# Compliance agent schema
# ---------------------------------------------------------------------------


class ComplianceOutput(BaseModel, frozen=True):
    """Structured output from the COMPLIANCE agent."""

    verdict: Verdict
    jurisdiction_risks: list[str] = Field(default_factory=list)
    trademark_conflict: bool
    requires_certifications: list[str] = Field(default_factory=list)
    advertising_restrictions: list[str] = Field(default_factory=list)
    rationale: str = Field(max_length=400)


# ---------------------------------------------------------------------------
# Narrative agent schema
# ---------------------------------------------------------------------------


class NarrativeOutput(BaseModel, frozen=True):
    """Structured output from the NARRATIVE agent."""

    core_narrative: str = Field(max_length=300)
    narrative_fatigue_score: float = Field(ge=0.0, le=1.0, description="1=saturated narrative")
    target_demographics: list[str] = Field(default_factory=list)
    suggested_ad_angles: list[str] = Field(default_factory=list)
    days_since_narrative_peak: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Red team agent schema
# ---------------------------------------------------------------------------


class RedTeamOutput(BaseModel, frozen=True):
    """Structured output from the RED_TEAM adversarial critic."""

    thesis_survives: bool
    falsification_attempts: list[str] = Field(
        description="Each attempt at invalidating the thesis"
    )
    strongest_counter: str = Field(max_length=300)
    confidence_adjustment: float = Field(
        ge=-1.0, le=0.0,
        description="Negative adjustment to apply to scout confidence (-1 = fully invalidated)",
    )
    verdict: Verdict


# ---------------------------------------------------------------------------
# Geo-arbitrage agent schema
# ---------------------------------------------------------------------------


class GeoArbitrageOutput(BaseModel, frozen=True):
    """Structured output from the GEO-ARBITRAGE agent."""

    top_markets: list[str] = Field(description="ISO country codes, highest opportunity first")
    price_arbitrage_usd: float = Field(description="Estimated USD arbitrage per unit")
    logistics_feasibility: RiskLevel
    regulatory_barrier: RiskLevel
    recommended_entry_market: str = Field(description="ISO country code")
    rationale: str = Field(max_length=300)


# ---------------------------------------------------------------------------
# Hedge agent schema
# ---------------------------------------------------------------------------


class HedgeOutput(BaseModel, frozen=True):
    """Structured output from the HEDGE portfolio risk manager."""

    verdict: Verdict
    portfolio_correlation_score: float = Field(ge=0.0, le=1.0)
    position_size_pct: float = Field(
        ge=0.0, le=100.0,
        description="Recommended portfolio allocation as % of total capital",
    )
    kelly_fraction: float = Field(ge=0.0, le=1.0)
    stop_loss_pct: float = Field(ge=0.0, le=100.0)
    rationale: str = Field(max_length=300)


# ---------------------------------------------------------------------------
# Sourcer agent schema
# ---------------------------------------------------------------------------


class SupplierInfo(BaseModel, frozen=True):
    """A single supplier candidate."""

    name: str
    platform: str
    moq: int = Field(ge=0, description="Minimum order quantity")
    unit_cost_usd: float = Field(ge=0.0)
    lead_time_days: int = Field(ge=0)
    quality_score: float = Field(ge=0.0, le=1.0)


class SourcerOutput(BaseModel, frozen=True):
    """Structured output from the SOURCER agent."""

    verdict: Verdict
    suppliers: list[SupplierInfo] = Field(max_length=5)
    best_supplier_index: int = Field(ge=0)
    total_landed_cost_usd: float = Field(ge=0.0)
    rationale: str = Field(max_length=300)


# ---------------------------------------------------------------------------
# Historian agent schema
# ---------------------------------------------------------------------------


class HistorianOutput(BaseModel, frozen=True):
    """Structured output from the HISTORIAN agent."""

    analogous_trends: list[str] = Field(description="Past trend titles with highest similarity")
    average_lifecycle_days: int = Field(ge=0)
    peak_roi_pct: float
    success_rate: float = Field(ge=0.0, le=1.0)
    key_lessons: list[str] = Field(max_length=5)
    rationale: str = Field(max_length=300)


# ---------------------------------------------------------------------------
# Generic fallback schema
# ---------------------------------------------------------------------------


class GenericAgentOutput(BaseModel, frozen=True):
    """Fallback schema for any agent that doesn't have a specific schema."""

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
