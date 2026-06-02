"""
LangGraph wiring for the AEGIS multi-agent pipeline.

Topology
--------

                       ┌──────────────┐
                       │   START      │
                       └──────┬───────┘
                              │
              ┌───────────────┼─────────────────┐
              │               │                 │
              ▼               ▼                 ▼
         ┌────────┐    ┌──────────────┐   ┌────────────┐
         │ SCOUT  │    │ GEO_ARBITRAGE│   │ NARRATIVE  │
         └───┬────┘    └──────┬───────┘   └─────┬──────┘
             └────────────────┼─────────────────┘
                              │  (join — parallel barrier)
                              ▼
                        ┌──────────┐
                        │HISTORIAN │
                        └────┬─────┘
                             │
                             ▼
                       ┌────────────┐
                       │  SOURCER   │   (only if scout_score >= threshold)
                       └────┬───────┘
                            │
                            ▼
                       ┌────────────┐
                       │  AUDITOR   │   (only if sourcer produced supplier)
                       └────┬───────┘
                            │
                            ▼
                       ┌────────────┐
                       │ SENTINEL   │
                       └────┬───────┘
                            │
                            ▼
                       ┌────────────┐
                       │ COMPLIANCE │   (mandatory; BLOCK halts pipeline)
                       └────┬───────┘
                            │
                            ▼
                       ┌────────────┐
                       │ RED_TEAM   │   (adversarial veto)
                       └────┬───────┘
                            │
                            ▼
                       ┌────────────┐
                       │   HEDGE    │   (portfolio-level final veto)
                       └────┬───────┘
                            │
                            ▼
                       ┌────────────┐
                       │ FINALIZE   │
                       └────┬───────┘
                            │
                            ▼
                          END

The conditional edges are decided by `_post_scout_route` and similar
helpers. We never raise inside a routing function — if state is
unexpected, we route directly to FINALIZE so the pipeline always
terminates with a `GraphResult`.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from .nodes import (
    AuditorAgent,
    ComplianceAgent,
    GeoArbitrageAgent,
    HedgeAgent,
    HistorianAgent,
    NarrativeAgent,
    RedTeamAgent,
    ScoutAgent,
    SentinelAgent,
    SourcerAgent,
)
from .schemas import AgentVerdict
from .state import GraphState
from .supervisor import finalize

if TYPE_CHECKING:  # pragma: no cover
    from .llm.router import LLMRouter
    from .memory.chroma_store import ChromaMemoryStore
    from .memory.shared_memory import SharedWorkingMemory

_log = structlog.get_logger("aegis.agents.graph")


# Threshold below which we skip SOURCER + AUDITOR. The candidate
# still reaches COMPLIANCE / RED_TEAM / HEDGE so we always have a
# complete decision trail and a final verdict.
_SOURCER_GATE = 0.55


# ----------------------------------------------------------------------
# Routing functions
# ----------------------------------------------------------------------


def _post_discovery_join(state: GraphState) -> str:
    """After SCOUT/GEO/NARRATIVE complete in parallel, always go to
    HISTORIAN. (Existence check kept for future extension.)"""
    return "historian"


def _post_historian_route(state: GraphState) -> str:
    """If SCOUT was strong enough, proceed to sourcer. Otherwise bypass
    sourcer+auditor but still run SENTINEL (saturation check) before
    COMPLIANCE, so exit signals are never silently skipped."""
    scout_score = float(state.get("scout_score", 0.0) or 0.0)
    scout_verdict = state.get("scout_verdict")

    if scout_verdict is AgentVerdict.BLOCK:
        return "sentinel"
    if scout_score >= _SOURCER_GATE:
        return "sourcer"
    return "sentinel"


def _post_sourcer_route(state: GraphState) -> str:
    """If sourcer produced a supplier, audit. Otherwise skip auditor but
    still run SENTINEL so exit signals are not missed even when no supplier
    was found. COMPLIANCE is always reached via the sentinel→compliance edge."""
    supplier = state.get("sourcer_supplier")
    if isinstance(supplier, dict) and supplier:
        return "auditor"
    return "sentinel"


def _post_auditor_route(state: GraphState) -> str:
    """Always run sentinel after auditor (it advises on exits)."""
    return "sentinel"


def _post_sentinel_route(state: GraphState) -> str:
    """Always run compliance after sentinel (mandatory gate)."""
    return "compliance"


def _post_compliance_route(state: GraphState) -> str:
    """If compliance failed → skip to finalize. Else continue to red_team.

    NOTE: We still run RED_TEAM after a HOLD-compliance because the
    HOLD is informational; we want the full decision trail.
    """
    if state.get("compliance_passed") is False:
        return "finalize"
    return "red_team"


def _post_red_team_route(state: GraphState) -> str:
    """If red_team blocked → skip to finalize. Else continue to hedge."""
    if state.get("red_team_passed") is False:
        return "finalize"
    return "hedge"


def _post_hedge_route(state: GraphState) -> str:
    """Hedge is the last gate; always finalize after."""
    return "finalize"


# ----------------------------------------------------------------------
# Builder
# ----------------------------------------------------------------------


def build_graph(
    *,
    llm_router: LLMRouter | None = None,
    historian_store: ChromaMemoryStore | None = None,
    shared_memory: SharedWorkingMemory | None = None,
    use_llm: bool = True,
) -> Any:
    """Build the compiled LangGraph state machine.

    `langgraph` must be installed; it is the only hard dep of this
    module. We do the import here (not at module import time) so
    static-analysis and linting still work in environments where
    langgraph hasn't been installed yet.
    """
    try:
        from langgraph.graph import END, START, StateGraph  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "langgraph is required to build the agent graph. "
            "Install it with: pip install langgraph"
        ) from exc

    # Instantiate every agent. Agents that need extra wiring receive it.
    scout = ScoutAgent(router=llm_router, use_llm=use_llm)
    geo = GeoArbitrageAgent(router=llm_router, use_llm=use_llm)
    narrative = NarrativeAgent(router=llm_router, use_llm=use_llm)
    historian = HistorianAgent(store=historian_store, router=llm_router, use_llm=use_llm)
    sourcer = SourcerAgent(router=llm_router, use_llm=use_llm)
    auditor = AuditorAgent(router=llm_router, use_llm=use_llm)
    sentinel = SentinelAgent(router=llm_router, use_llm=use_llm)
    compliance = ComplianceAgent(router=llm_router, use_llm=use_llm)
    red_team = RedTeamAgent(router=llm_router, use_llm=use_llm)
    hedge = HedgeAgent(shared_memory=shared_memory, router=llm_router, use_llm=use_llm)

    builder = StateGraph(GraphState)

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    builder.add_node("scout", scout)
    builder.add_node("geo_arbitrage", geo)
    builder.add_node("narrative", narrative)
    builder.add_node("historian", historian)
    builder.add_node("sourcer", sourcer)
    builder.add_node("auditor", auditor)
    builder.add_node("sentinel", sentinel)
    builder.add_node("compliance", compliance)
    builder.add_node("red_team", red_team)
    builder.add_node("hedge", hedge)
    builder.add_node("finalize", lambda state: finalize(state))

    # ------------------------------------------------------------------
    # Parallel discovery: START → {scout, geo, narrative}
    # ------------------------------------------------------------------
    builder.add_edge(START, "scout")
    builder.add_edge(START, "geo_arbitrage")
    builder.add_edge(START, "narrative")

    # All three converge into historian (LangGraph waits for all three
    # to finish before invoking historian thanks to the multiple
    # in-edges + state reducer).
    builder.add_edge("scout", "historian")
    builder.add_edge("geo_arbitrage", "historian")
    builder.add_edge("narrative", "historian")

    # ------------------------------------------------------------------
    # Sequential remainder, with conditional gates.
    # ------------------------------------------------------------------
    builder.add_conditional_edges(
        "historian",
        _post_historian_route,
        {"sourcer": "sourcer", "sentinel": "sentinel"},
    )
    builder.add_conditional_edges(
        "sourcer",
        _post_sourcer_route,
        {"auditor": "auditor", "sentinel": "sentinel"},
    )
    builder.add_edge("auditor", "sentinel")
    builder.add_edge("sentinel", "compliance")
    builder.add_conditional_edges(
        "compliance",
        _post_compliance_route,
        {"red_team": "red_team", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "red_team",
        _post_red_team_route,
        {"hedge": "hedge", "finalize": "finalize"},
    )
    builder.add_edge("hedge", "finalize")
    builder.add_edge("finalize", END)

    compiled = builder.compile()
    _log.info(
        "graph.built",
        nodes=[
            "scout",
            "geo_arbitrage",
            "narrative",
            "historian",
            "sourcer",
            "auditor",
            "sentinel",
            "compliance",
            "red_team",
            "hedge",
            "finalize",
        ],
        use_llm=use_llm,
    )
    return compiled
