"""
Pydantic v2 schemas for the multi-agent intelligence layer.

Defines:
    AgentDecision    — the canonical output shape every agent emits.
    AgentMessage     — the on-the-wire envelope for Redis Streams traffic.
    TrendCandidate   — input bundle: a representative ProductSignal plus
                       co-occurring signals that share a `trend_id`.
    GraphResult      — final output of one full graph run.

All datetimes are timezone-aware UTC (enforced by pydantic validators).
All ids are UUID4 strings. All payloads are JSON-serializable.

These schemas are the *contract* between agents — they MUST stay
backwards-compatible across minor versions. Schema_version is bumped
on any breaking change and the supervisor checks compatibility on
every message.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# Schema version — bump on any backwards-incompatible change.
SCHEMA_VERSION: str = "1.0.0"


class Priority(int, Enum):
    """Routing priority. Lower number = higher priority."""

    P0_BREAKOUT = 0  # confirmed breakout, RED_TEAM-passed → execute now
    P1_EXIT = 1  # saturation / exit signal on an existing position
    P2_OPPORTUNITY = 2  # standard candidate
    P3_HOUSEKEEPING = 3  # background maintenance


class AgentVerdict(str, Enum):
    """The discrete decision an agent emits."""

    PROCEED = "proceed"  # green-light; advance to next stage
    HOLD = "hold"  # need more data; re-queue with backoff
    BLOCK = "block"  # hard stop; do not execute
    ESCALATE = "escalate"  # human review required


# Bounded string aliases used throughout the schemas.
ShortStr = Annotated[str, StringConstraints(min_length=1, max_length=128)]
ReasonStr = Annotated[str, StringConstraints(min_length=0, max_length=4096)]


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _ensure_utc(value: datetime) -> datetime:
    """Coerce naive datetimes to UTC; reject non-UTC tz-aware datetimes."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    if value.utcoffset() != UTC.utcoffset(value):
        # Convert to UTC instead of rejecting — pragmatic for cross-tz callers.
        return value.astimezone(UTC)
    return value


class AgentDecision(BaseModel):
    """Canonical output of every agent node.

    The supervisor inspects only the typed fields below; everything
    free-form goes into `details`. This keeps the routing logic
    deterministic and easy to test.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    schema_version: str = SCHEMA_VERSION
    agent: ShortStr
    trend_id: ShortStr
    correlation_id: ShortStr

    verdict: AgentVerdict
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Calibrated [0,1] score; 1.0 = strongest support for verdict.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Self-reported confidence in `score` and `verdict`.",
    )

    reasoning: ReasonStr = ""
    details: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

    used_llm: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_tokens_input: int = 0
    llm_tokens_output: int = 0
    llm_latency_ms: float | None = None

    duration_ms: float = Field(0.0, ge=0.0)
    timestamp: datetime = Field(default_factory=_utcnow)

    @field_validator("timestamp")
    @classmethod
    def _utc_only(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    @model_validator(mode="after")
    def _verdict_score_consistency(self) -> AgentDecision:
        """A BLOCK with score=1.0 and a PROCEED with score=0.0 are
        almost certainly bugs. Catch these at construction time."""
        if self.verdict is AgentVerdict.BLOCK and self.score >= 0.95:
            # Allow it but flag in details so audit catches the inversion.
            object.__setattr__(
                self,
                "details",
                {**self.details, "_warning": "high score with BLOCK verdict"},
            )
        if self.verdict is AgentVerdict.PROCEED and self.score <= 0.05:
            object.__setattr__(
                self,
                "details",
                {**self.details, "_warning": "low score with PROCEED verdict"},
            )
        return self


class AgentMessage(BaseModel):
    """Wire-level envelope sent over Redis Streams between agents.

    Includes an HMAC field that is populated and verified by the
    `messaging.streams` layer — the schema itself does not compute it.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = SCHEMA_VERSION
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: ShortStr

    from_agent: ShortStr
    to_agent: ShortStr  # may be the literal string "broadcast"
    priority: Priority = Priority.P2_OPPORTUNITY

    payload: dict[str, Any] = Field(default_factory=dict)
    context_window: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

    timestamp: datetime = Field(default_factory=_utcnow)
    ttl_seconds: int = Field(300, ge=1, le=86_400)

    hmac_signature: str | None = None  # populated by signer, not the model

    @field_validator("timestamp")
    @classmethod
    def _utc_only(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Wall-clock TTL check. Survives WSL clock drift better than
        monotonic time because the producer's wall clock is the
        authoritative reference."""
        ref = now if now is not None else _utcnow()
        delta = (ref - self.timestamp).total_seconds()
        return delta > self.ttl_seconds


class TrendCandidate(BaseModel):
    """Input to the agent graph. A bundle of signals that the upstream
    feature pipeline has clustered as belonging to the same emerging
    trend."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = SCHEMA_VERSION
    trend_id: ShortStr
    correlation_id: ShortStr = Field(default_factory=lambda: str(uuid.uuid4()))

    title: ShortStr
    summary: ReasonStr = ""

    # Numeric features are the sole inputs the heuristic path needs.
    # The LLM augmentation layer adds qualitative refinement on top.
    velocity_1h: float = Field(0.0, ge=-1e9, le=1e9)
    velocity_6h: float = Field(0.0, ge=-1e9, le=1e9)
    velocity_24h: float = Field(0.0, ge=-1e9, le=1e9)

    sentiment: float = Field(0.0, ge=-1.0, le=1.0)
    commercial_intent: float = Field(0.0, ge=0.0, le=1.0)
    novelty: float = Field(0.0, ge=0.0, le=1.0)
    coordination_risk: float = Field(0.0, ge=0.0, le=1.0)

    signal_count: int = Field(0, ge=0)
    unique_authors: int = Field(0, ge=0)
    platforms: list[ShortStr] = Field(default_factory=list)

    sample_signal_ids: list[ShortStr] = Field(default_factory=list, max_length=50)
    representative_text: ReasonStr = ""
    representative_url: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("created_at")
    @classmethod
    def _utc_only(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    @field_validator("platforms")
    @classmethod
    def _dedup_platforms(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for p in v:
            key = p.lower()
            if key not in seen:
                seen.add(key)
                out.append(p)
        return out


class GraphResult(BaseModel):
    """Final, immutable output of one full graph traversal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SCHEMA_VERSION
    trend_id: ShortStr
    correlation_id: ShortStr

    final_verdict: AgentVerdict
    final_priority: Priority
    final_score: float = Field(..., ge=0.0, le=1.0)
    final_confidence: float = Field(..., ge=0.0, le=1.0)

    decisions: list[AgentDecision] = Field(default_factory=list)
    blocked_by: list[ShortStr] = Field(default_factory=list)

    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(..., ge=0.0)

    halt_reason: Literal[
        "completed",
        "vetoed_by_red_team",
        "vetoed_by_hedge",
        "blocked_by_compliance",
        "scout_below_threshold",
        "no_supplier",
        "exception",
        "timeout",
    ]

    @field_validator("started_at", "finished_at")
    @classmethod
    def _utc_only(cls, v: datetime) -> datetime:
        return _ensure_utc(v)
