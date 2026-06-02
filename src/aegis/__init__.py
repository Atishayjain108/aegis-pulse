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

import pathlib as _pathlib

__version__ = "0.3.0"

# uv workspace ordering puts aegis-phase4/src before src/ in sys.path, which
# causes the Phase 4 aegis/__init__.py to shadow this package. By extending
# __path__ we expose aegis.execute (from aegis-phase4/) alongside the main
# submodules without requiring a pure namespace-package setup.
_root = _pathlib.Path(__file__).parent.parent.parent

_p4_aegis = _root / "aegis-phase4" / "src" / "aegis"
if _p4_aegis.is_dir() and str(_p4_aegis) not in __path__:
    __path__ = [*__path__, str(_p4_aegis)]  # type: ignore[assignment]

_harden_aegis = _root / "aegis-harden" / "src" / "aegis"
if _harden_aegis.is_dir() and str(_harden_aegis) not in __path__:
    __path__ = [*__path__, str(_harden_aegis)]  # type: ignore[assignment]

_phase12_aegis = _root / "aegis-phase12" / "src" / "aegis"
if _phase12_aegis.is_dir() and str(_phase12_aegis) not in __path__:
    __path__ = [*__path__, str(_phase12_aegis)]  # type: ignore[assignment]

_phase13_aegis = _root / "aegis-phase13" / "src" / "aegis"
if _phase13_aegis.is_dir() and str(_phase13_aegis) not in __path__:
    __path__ = [*__path__, str(_phase13_aegis)]  # type: ignore[assignment]

del _root, _p4_aegis, _harden_aegis, _phase12_aegis, _phase13_aegis, _pathlib
