"""Pydantic v2 frozen schemas for the AEGIS Mentor.

These are the contracts the Mentor brain and the operator fleet speak in.
Everything is immutable (``frozen=True``) — the same discipline as the rest of
AEGIS (``TrendCandidate``, ``GeoOpportunity``, …).

Grounded-or-silent: schemas that carry claims (``Counsel``, ``Opportunity``)
always carry their provenance (``sources``) so a number can be traced to a real
fact. Never populate a claim without a source.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enumerations — these mirror the CHECK-constrained columns in 0026_mentor.sql.
# ---------------------------------------------------------------------------


class Intent(str, Enum):
    """Why the user is here — what they ultimately want from AEGIS."""

    INCOME = "income"        # make money / start earning
    LEARNING = "learning"    # learn the landscape / skill up
    SCALE = "scale"          # grow an existing operation
    RESEARCH = "research"    # explore a niche, no commitment yet
    VALIDATE = "validate"    # test a specific idea before committing
    UNKNOWN = "unknown"      # not yet determined — triggers a clarifying ask


class AutonomyPreference(str, Enum):
    """How much AEGIS should do *for* the user vs *with* them.

    do-it-for-me / co-pilot / brief-me in the blueprint's words.
    """

    GUIDE_ME = "guide_me"    # do-it-for-me — run it for a novice, low altitude
    COACH_ME = "coach_me"    # co-pilot — collaborate, medium altitude
    ANSWER_ME = "answer_me"  # brief-me — sharp analysis only, high altitude


class RiskTolerance(str, Enum):
    LOW = "low"
    MEDIUM = "med"
    HIGH = "high"


class Channel(str, Enum):
    ONLINE_ONLY = "online_only"
    LOCAL = "local"
    BOTH = "both"


class ExperienceLevel(str, Enum):
    NONE = "none"
    SOME = "some"
    EXPERIENCED = "experienced"


# Sentinel sector tag used when the field could not be determined. The Mentor
# must NOT fabricate a sector — it asks a clarifying question instead.
SECTOR_UNDECIDED = "undecided"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class UserProfile(BaseModel, frozen=True):
    """Who the user is — the one thing AEGIS must learn.

    ``sector`` (the domain) decides *what* domain intelligence is pulled;
    everything else tunes *how* AEGIS helps.
    """

    profile_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "00000000-0000-0000-0000-000000000001"

    raw_description: str = ""

    # The keystone fields.
    sector: str = SECTOR_UNDECIDED       # normalized tag, e.g. "d2c_india"
    sector_raw: str = ""                 # the user's own words for their field
    intent: Intent = Intent.UNKNOWN
    autonomy_preference: AutonomyPreference = AutonomyPreference.COACH_ME

    # Tuning fields.
    capital_usd: float = Field(default=0.0, ge=0.0)
    risk_tolerance: RiskTolerance = RiskTolerance.MEDIUM
    channels: Channel = Channel.ONLINE_ONLY
    skills: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    time_per_week_hrs: float = Field(default=0.0, ge=0.0)
    experience_level: ExperienceLevel = ExperienceLevel.NONE

    region: str = "IN"
    currency: str = "INR"

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IntentParseResult(BaseModel, frozen=True):
    """Output of ``IntentParser.parse`` — a profile, or a request to clarify.

    When ``needs_clarification`` is True, ``clarifying_question`` is set and the
    partial ``profile`` carries everything parsed so far (with ``sector`` or
    ``intent`` still unknown). The Mentor must NOT proceed on a guessed sector.
    """

    profile: UserProfile
    needs_clarification: bool = False
    clarifying_question: str | None = None
    missing_fields: tuple[str, ...] = ()
    extraction_method: str = "heuristic"  # "heuristic" | "llm"


class Opportunity(BaseModel, frozen=True):
    """Thin adapter over a geo/memory opportunity — what the Mentor matches.

    Grounded: ``sources`` must point at the real artifacts (geo opportunity id,
    memory record id, signal ids) that produced the numbers.
    """

    opportunity_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = ""
    sector: str = SECTOR_UNDECIDED
    estimated_margin_pct: float | None = None
    demand_intensity: float | None = None
    capital_required_usd: float | None = None
    saturation: float | None = None
    sources: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MatchResult(BaseModel, frozen=True):
    """An opportunity scored against a profile (capital/channel/skill fit)."""

    opportunity: Opportunity
    fit_score: float = Field(ge=0.0, le=1.0)
    capital_fit: bool = True
    channel_fit: bool = True
    skill_fit: bool = True
    rationale: str = ""


class Counsel(BaseModel, frozen=True):
    """The structured advice the Mentor returns. Grounded or silent.

    ``claims`` each carry a source; ``confidence`` is calibrated, not hype.
    """

    counsel_id: str = Field(default_factory=lambda: str(uuid4()))
    summary: str = ""
    matches: list[MatchResult] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    # Evidence-first persuasion lines from the conviction engine (grounded or empty).
    persuasion: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    altitude: AutonomyPreference = AutonomyPreference.COACH_ME
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Lesson(BaseModel, frozen=True):
    """A micro-lesson delivered when a knowledge gap is detected."""

    lesson_id: str = Field(default_factory=lambda: str(uuid4()))
    topic: str = ""
    body: str = ""
    sources: list[str] = Field(default_factory=list)


class KnowledgeGap(BaseModel, frozen=True):
    """A topic the user does not yet understand — drives the teaching loop."""

    gap_id: str = Field(default_factory=lambda: str(uuid4()))
    profile_id: str = ""
    topic: str = ""
    detected_from: str = ""
    lesson_delivered: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
