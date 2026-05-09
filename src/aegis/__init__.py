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

Author: AEGIS Pulse Team
"""

from __future__ import annotations

__version__ = "0.2.0"
