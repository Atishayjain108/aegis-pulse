"""AEGIS Pulse — Phase 4: Execution & Alert System.

This package is the action layer of AEGIS Pulse. It converts upstream verdicts
from Phase 2 (multi-agent graph) and Phase 3 (predictive ML core) into
deterministic, deduplicated, observable alerts and (advisory) execution
intents.

Doctrine: heuristic first. The system functions with zero notification
channels configured and zero LLM keys. Channels and LLM augmentation are
strictly additive.

See `docs/phase4/ARCHITECTURE.md` for the full design.
"""

from __future__ import annotations

__version__ = "0.4.0"

__all__ = ["__version__"]
