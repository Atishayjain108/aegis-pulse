"""
AEGIS Pulse — Phase 2: Multi-Agent Intelligence Layer.

This package implements a 10-agent LangGraph-based supervisor system that
takes a `ProductSignal` (or batch of signals representing a candidate
trend) and routes it through a deterministic, idempotent decision pipeline:

    SCOUT  +  GEO_ARBITRAGE  +  NARRATIVE   (parallel discovery)
        │
        ├──► HISTORIAN  (analogous past trend lookup)
        │
        ├──► SOURCER    (only if SCOUT score >= threshold)
        │
        ├──► AUDITOR    (only if SOURCER produced a supplier)
        │
        ├──► SENTINEL   (saturation / exit timing)
        │
        ├──► COMPLIANCE (mandatory legal/regulatory gate)
        │
        ├──► RED_TEAM   (adversarial veto on P0 alerts)
        │
        └──► HEDGE      (portfolio-level final veto)

The package is fully usable WITHOUT any LLM provider configured. Every
agent node ships with a deterministic heuristic implementation that
produces a calibrated decision from numeric features alone. LLM calls
are *augmentation only* — they refine reasoning text and confidence,
never gate the pipeline.

Author: AEGIS Pulse core team
Phase:  2 (Multi-Agent Intelligence)
"""
from __future__ import annotations

__all__ = [
    "AGENT_NAMES",
    "PHASE",
]

PHASE: str = "2"

# Canonical, ordered roster. The supervisor uses this list to validate
# routing targets — any string outside this set is rejected.
AGENT_NAMES: tuple[str, ...] = (
    "scout",
    "sourcer",
    "auditor",
    "sentinel",
    "compliance",
    "geo_arbitrage",
    "narrative",
    "hedge",
    "red_team",
    "historian",
)
