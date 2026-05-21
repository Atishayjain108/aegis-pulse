"""
LangGraph shared state for the AEGIS multi-agent pipeline.

LangGraph is built around a single mutable state dict that flows
through every node. Each node returns a partial dict; LangGraph
merges it into the running state using per-key reducer functions.

We use `Annotated[T, reducer]` to declare deterministic merge
semantics. This is critical because in parallel branches multiple
nodes write to the same state at the same time — without a reducer,
the last write wins (race condition).

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import operator  # required at runtime — used as a reducer in Annotated[int, operator.add]
from typing import Annotated, Any, TypedDict

# All types used in GraphState field annotations must be runtime imports.
# LangGraph calls get_type_hints(GraphState) at StateGraph construction time,
# which evaluates forward references; TYPE_CHECKING-only imports cause NameError.
from aegis.scrape.swarm_result import SwarmResult

from .schemas import AgentDecision, AgentVerdict, Priority, TrendCandidate


def _merge_decisions(left: list[AgentDecision], right: list[AgentDecision]) -> list[AgentDecision]:
    """Concatenate decision lists, dedupe by (agent, correlation_id).

    LangGraph parallel branches each return a list with one decision.
    Concatenation preserves order; the dedupe pass guards against the
    case where a node fires twice (e.g., on retry) — keep the latest
    decision (rightmost) for each agent.
    """
    out: list[AgentDecision] = []
    seen: dict[tuple[str, str], int] = {}
    for d in [*left, *right]:
        key = (d.agent, d.correlation_id)
        if key in seen:
            out[seen[key]] = d  # latest wins
        else:
            seen[key] = len(out)
            out.append(d)
    return out


def _merge_blockers(left: list[str], right: list[str]) -> list[str]:
    """Union of blocker lists, preserving order of first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for item in [*left, *right]:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Right-biased shallow merge."""
    return {**left, **right}


class GraphState(TypedDict, total=False):
    """The full state object that flows through the graph.

    Marked `total=False` so each node only declares the keys it writes.
    LangGraph respects this: missing keys are not None, they are absent.
    """

    # ------------------------------------------------------------------
    # Inputs (set once at graph entry; never mutated)
    # ------------------------------------------------------------------
    candidate: TrendCandidate
    correlation_id: str
    trend_id: str
    tenant_id: str

    # ------------------------------------------------------------------
    # Cumulative agent decisions (parallel-safe via _merge_decisions)
    # ------------------------------------------------------------------
    decisions: Annotated[list[AgentDecision], _merge_decisions]
    blocked_by: Annotated[list[str], _merge_blockers]

    # ------------------------------------------------------------------
    # Sequential pipeline outputs (one writer at a time)
    # ------------------------------------------------------------------
    scout_score: float
    scout_verdict: AgentVerdict

    geo_arbitrage_score: float
    narrative_score: float
    historian_analogues: list[dict[str, Any]]

    sourcer_supplier: dict[str, Any] | None
    auditor_margin_p10: float
    auditor_margin_p50: float
    auditor_margin_p90: float

    sentinel_saturation: float
    sentinel_recommended_exit: bool

    compliance_passed: bool
    compliance_flags: list[str]

    red_team_passed: bool
    red_team_falsifiers: list[str]

    hedge_passed: bool
    hedge_correlation_excess: float

    # ------------------------------------------------------------------
    # Final aggregates (written by the supervisor's terminal node)
    # ------------------------------------------------------------------
    final_verdict: AgentVerdict
    final_priority: Priority
    final_score: float
    final_confidence: float
    halt_reason: str

    # ------------------------------------------------------------------
    # Phase 3 enrichment inputs (optional — supplied by CLI/API callers)
    # ------------------------------------------------------------------
    # Raw signal dicts from Phase 1 DB. When present, SCOUT and SENTINEL
    # pass them to the Phase 3 bridge so the temporal/relational models
    # can build real feature windows instead of returning empty heuristics.
    signals: list[dict[str, Any]]

    # ------------------------------------------------------------------
    # Phase 6 — Swarm context (optional cross-platform market intelligence)
    # ------------------------------------------------------------------
    # Latest SwarmResult fetched from Redis by the runner before graph
    # invocation. None when Redis is unavailable or no swarm has run yet.
    swarm_context: SwarmResult | None

    # ------------------------------------------------------------------
    # Operational
    # ------------------------------------------------------------------
    metadata: Annotated[dict[str, Any], _merge_dicts]
    error_count: Annotated[int, operator.add]


def initial_state(
    candidate: TrendCandidate,
    *,
    tenant_id: str = "default",
    signals: list[dict[str, Any]] | None = None,
    swarm_context: SwarmResult | None = None,
) -> GraphState:
    """Build a fresh state dict for a new graph invocation."""
    state = GraphState(
        candidate=candidate,
        correlation_id=candidate.correlation_id,
        trend_id=candidate.trend_id,
        tenant_id=tenant_id,
        decisions=[],
        blocked_by=[],
        metadata={},
        error_count=0,
    )
    if signals:
        state["signals"] = signals
    if swarm_context is not None:
        state["swarm_context"] = swarm_context
    return state
