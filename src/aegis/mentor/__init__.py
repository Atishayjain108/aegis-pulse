"""AEGIS Mentor — the counsel engine.

Turns AEGIS from a signal engine into a counsel engine: a MentorAgent brain
that models the user's sector + intent, then routes their need to an operator
fleet backed by AEGIS's real intelligence (geo, compliance, capital, memory,
trust). Heuristic-first, grounded-or-silent, India/INR first.

Regular subpackage of the ``aegis`` namespace — NOT a workspace member.
"""

from __future__ import annotations

from aegis.mentor.config import MentorSettings
from aegis.mentor.intent import IntentParser
from aegis.mentor.mentor_agent import MentorAgent
from aegis.mentor.operators import (
    CustomerOp,
    ExploreOp,
    FinanceOp,
    KnowledgeOp,
    KnowledgeRetriever,
    Operator,
    OperatorContext,
    OperatorResult,
    OpsOp,
    ResearchOp,
    SupplierOp,
)
from aegis.mentor.persuasion import Evidence, Objection, Persuader, PersuasionCase
from aegis.mentor.profiles import ProfileStore
from aegis.mentor.schemas import (
    AutonomyPreference,
    Channel,
    Counsel,
    ExperienceLevel,
    Intent,
    IntentParseResult,
    KnowledgeGap,
    Lesson,
    MatchResult,
    Opportunity,
    RiskTolerance,
    UserProfile,
)
from aegis.mentor.sector import BaselinePack, SectorPack, SectorRouter

__version__ = "1.0.0"

__all__ = [
    "AutonomyPreference",
    "BaselinePack",
    "Channel",
    "Counsel",
    "CustomerOp",
    "Evidence",
    "ExperienceLevel",
    "ExploreOp",
    "FinanceOp",
    "Intent",
    "IntentParseResult",
    "IntentParser",
    "KnowledgeGap",
    "KnowledgeOp",
    "KnowledgeRetriever",
    "Lesson",
    "MatchResult",
    "MentorAgent",
    "MentorSettings",
    "Objection",
    "Operator",
    "OperatorContext",
    "OperatorResult",
    "Opportunity",
    "OpsOp",
    "PersuasionCase",
    "Persuader",
    "ProfileStore",
    "ResearchOp",
    "RiskTolerance",
    "SectorPack",
    "SectorRouter",
    "SupplierOp",
    "UserProfile",
]
