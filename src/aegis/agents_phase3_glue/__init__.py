"""
Phase 2 ↔ Phase 3 bridge.

Phase 2 owns the 10-agent LangGraph pipeline; Phase 3 owns prediction.
The two sides talk through ONE adapter, defined here:

    enrich_scout_decision(state)   — adds Phase 3 PredictionBundle to
                                     the state going from SCOUT to
                                     downstream agents.
    enrich_sentinel_decision(state) — same hook for SENTINEL's
                                      saturation detection.

Why a separate package and not a method on InferenceRunner?
-----------------------------------------------------------
The Phase 2 graph state (`GraphState` TypedDict) lives in `aegis.agents`
which has its own dependency footprint (LangGraph, Redis Streams).
Putting the adapter HERE means Phase 3 still imports cleanly without
LangGraph installed — the bridge is loaded on demand by Phase 2's graph
builder.

If `aegis.agents` is missing, every public function in this module
returns the input state unchanged. That guarantees Phase 3 alone is a
deployable artefact, with the bridge being a Phase 2-and-3-together
optional package.
"""

from .bridge import (
    enrich_scout_decision,
    enrich_sentinel_decision,
    inference_to_agent_decision,
)

__all__ = [
    "enrich_scout_decision",
    "enrich_sentinel_decision",
    "inference_to_agent_decision",
]
