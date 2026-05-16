"""AEGIS Pulse — autonomous market arbitrage intelligence engine.

This is the top-level package. Sub-packages:

- ``schemas`` — canonical data types (``ProductSignal`` et al.)
- ``constants`` — shared named constants (no magic numbers anywhere else)
- ``core`` — logging, resilience, metrics, tracing
- ``db`` — async Postgres pool + migrations helpers
- ``cache`` — Redis cache + priority queue
- ``scrape`` — source adapters (Phase 1 signal ingestion)
- ``agents`` — multi-agent intelligence pipeline (Phase 2)
  - ``agents.nodes`` — 10 agent nodes (scout, auditor, compliance, …)
  - ``agents.llm`` — LLM router + multi-provider abstraction
  - ``agents.memory`` — ChromaDB semantic memory + shared working memory
  - ``agents.messaging`` — Redis Streams inter-agent bus + HMAC signing
  - ``agents.tools`` — deterministic tools (velocity, monte_carlo, …)
- ``predict`` — Phase 3 Predictive Apex (heuristic ML core)
  - ``predict.features`` — feature builder, velocity, creator graph
  - ``predict.models`` — heuristic floor + optional neural backbones
  - ``predict.inference`` — InferenceRunner (single entry point)
  - ``predict.causal`` — deterministic attribution + counterfactuals
  - ``predict.rl`` — fractional-Kelly execution policy
  - ``predict.backtest`` — walk-forward evaluator
  - ``predict.registry`` — ModelStore + PromotionGate
  - ``predict.serving`` — FastAPI /predict surface
- ``agents_phase3_glue`` — Phase 2 ↔ Phase 3 bridge (no LangGraph import)

Author: AEGIS Pulse Team
"""

from __future__ import annotations

__version__ = "0.3.0"
