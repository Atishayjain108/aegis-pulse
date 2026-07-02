"""aegis.memory.schemas — frozen Pydantic v2 models for the knowledge ledger."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis.memory.taxonomy import (
    EntityKind,
    FailureCategory,
    OpportunityOutcome,
    OpportunityType,
    RelationType,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Opportunity(BaseModel):
    """A durable record of an opportunity AEGIS observed and (eventually) judged."""

    model_config = ConfigDict(frozen=True)

    opportunity_id: str = Field(default_factory=_new_uuid)
    opportunity_type: OpportunityType = OpportunityType.INFO_ARB
    category: str = "general"
    region: str | None = None

    trend_key: str
    prediction_id: str
    source_signal_ids: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)

    # Verification scores — populated by the Reality layer in a later stage.
    trust_score: float | None = None
    reality_score: float | None = None
    evidence_score: float | None = None
    unknowns_score: float | None = None

    prediction_direction: str | None = None
    prediction_score: float | None = None
    prediction_confidence: float | None = None

    outcome: OpportunityOutcome = OpportunityOutcome.PENDING
    failure_id: str | None = None
    realization_path: dict[str, Any] = Field(default_factory=dict)
    decay_horizon_hours: int = 72

    created_at: datetime = Field(default_factory=_utcnow)
    settled_at: datetime | None = None
    settlement_timestamp: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceProfile(BaseModel):
    """Per-platform source reliability derived from settled outcomes (Rule 4)."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    trust: float = 0.3
    reliability_score: float | None = None  # settled-correct rate
    freshness_score: float | None = None    # recency of useful signals, [0,1]
    manipulation_risk: float | None = None  # None = not yet measured (never faked)
    per_category_accuracy: dict[str, float] = Field(default_factory=dict)
    n_signals: int = 0
    n_outcomes: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)


class RealityAssessment(BaseModel):
    """Pre-recommendation verification scores (Rule 8). All in [0, 1].

    * evidence_score — how strong is the underlying signal evidence?
    * trust_score    — how reliable are the sources backing it?
    * reality_score  — does history say this kind of opportunity realises?
    * unknowns_score — how complete are the inputs (1.0 = nothing unknown)?
    ``passed`` is an ADVISORY gate, not a hard block.
    """

    model_config = ConfigDict(frozen=True)

    trend_key: str
    opportunity_type: OpportunityType = OpportunityType.INFO_ARB
    category: str = "general"
    evidence_score: float = 0.0
    trust_score: float = 0.0
    reality_score: float = 0.0
    unknowns_score: float = 0.0
    passed: bool = False
    notes: str = ""
    assessed_at: datetime = Field(default_factory=_utcnow)


class Entity(BaseModel):
    """A long-lived entity AEGIS remembers across time (Rule 3)."""

    model_config = ConfigDict(frozen=True)

    entity_id: str = Field(default_factory=_new_uuid)
    entity_kind: EntityKind
    canonical_name: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    trust: float = 0.5
    n_observations: int = 0
    first_seen: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MarketEpoch(BaseModel):
    """A compressed snapshot of market conditions for a period (Rule 6)."""

    model_config = ConfigDict(frozen=True)

    epoch_id: str = Field(default_factory=_new_uuid)
    period_start: datetime
    period_end: datetime
    category: str = "general"
    region: str | None = None
    trend_summary: dict[str, Any] = Field(default_factory=dict)
    demand_index: float | None = None
    seasonality_tag: str | None = None
    recurring_pattern_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


class GraphEdge(BaseModel):
    """One relationship in the knowledge graph (Rule 7)."""

    model_config = ConfigDict(frozen=True)

    src_kind: str
    src_id: str
    dst_kind: str
    dst_id: str
    relation: RelationType
    weight: float = 1.0
    evidence_count: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class Failure(BaseModel):
    """Reusable knowledge about why one opportunity failed (Rule 5)."""

    model_config = ConfigDict(frozen=True)

    failure_id: str = Field(default_factory=_new_uuid)
    opportunity_id: str
    trend_key: str
    prediction_id: str

    failure_category: FailureCategory = FailureCategory.UNCLASSIFIED
    root_cause: str = ""
    evidence_quality: float | None = None
    missing_information: list[str] = Field(default_factory=list)
    confidence_error: float | None = None

    detected_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
