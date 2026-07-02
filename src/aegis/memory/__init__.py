"""
aegis.memory — PROJECT OMEGA Phase C (Knowledge Expansion)
==========================================================

Turns AEGIS from a stateless signal→predict engine into one that *accumulates
verified knowledge*. Stage 1 ships the durable Opportunity ledger (Rule 2) and
Failure intelligence (Rule 5), both derived strictly from SETTLED ground-truth
outcomes — never from unsettled predictions (Rule 12 / falsifiability).

Regular subpackage of ``aegis`` (same convention as ``aegis.evolve`` /
``aegis.geo``). All persistence is best-effort: write failures log, never raise,
so the prediction path is never blocked.
"""

from __future__ import annotations

from aegis.memory.backfill import BackfillSummary, MemoryBackfill, map_settled_row
from aegis.memory.backtest import RealityBacktester
from aegis.memory.capture import MemoryCapture
from aegis.memory.entity import EntityMemory
from aegis.memory.failure import FailureMemory, classify_failure
from aegis.memory.graph import KnowledgeGraph
from aegis.memory.market import MarketMemory
from aegis.memory.opportunity import OpportunityMemory
from aegis.memory.report import SelfAudit
from aegis.memory.schemas import (
    Entity,
    Failure,
    GraphEdge,
    MarketEpoch,
    Opportunity,
    RealityAssessment,
    SourceProfile,
)
from aegis.memory.source import SourceMemory
from aegis.memory.taxonomy import (
    EntityKind,
    FailureCategory,
    OpportunityOutcome,
    OpportunityType,
    RelationType,
)
from aegis.memory.verify import RealityVerifier, score_evidence, score_unknowns

__version__ = "18.0.0"  # Phase C

__all__ = [
    "BackfillSummary",
    "Entity",
    "EntityKind",
    "EntityMemory",
    "Failure",
    "FailureCategory",
    "FailureMemory",
    "GraphEdge",
    "KnowledgeGraph",
    "MarketEpoch",
    "MarketMemory",
    "MemoryBackfill",
    "MemoryCapture",
    "Opportunity",
    "OpportunityMemory",
    "OpportunityOutcome",
    "OpportunityType",
    "RealityAssessment",
    "RealityBacktester",
    "RealityVerifier",
    "RelationType",
    "SelfAudit",
    "SourceMemory",
    "SourceProfile",
    "classify_failure",
    "map_settled_row",
    "score_evidence",
    "score_unknowns",
]
