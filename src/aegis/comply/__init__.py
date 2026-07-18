"""AEGIS Pulse — Phase 8: Regulatory & Compliance Engine.

This package is the deterministic compliance gate that backs the Phase 2
``COMPLIANCE`` agent node. It scores a product/trend candidate against a
data-driven, multi-jurisdiction rule engine plus trademark and counterfeit
screeners, and emits a fail-closed verdict (``clear`` / ``flag`` / ``block``).

Architecture relationship
--------------------------
- **Phase 1**: writes audit rows via ``store.audit_repo`` using the shared pool.
- **Phase 2**: ``bridge.agents_bridge.evaluate_for_compliance_node`` maps a
  ``TrendCandidate``-shaped dict to an ``AgentDecision``-shaped dict with no
  LangGraph import (structural typing only).
- **Phase 4**: ``bridge.phase4_bridge`` escalates ``block`` verdicts to the
  alert outbox / killswitch via duck-typed publishers.
- **Phase 10**: ``compliance_audit`` is a Bronze-ingestable table (tenant_id +
  timestamps + content-addressable id).
- **Phase 11**: ``llm.augmentor`` may *append* reasoning and *raise* severity
  through the LLM gateway — it can never clear a flagged item.

Heuristic-first doctrine (non-negotiable)
-----------------------------------------
``ComplianceEngine.evaluate`` is synchronous, network-free, and deterministic.
It produces a complete verdict from numeric features and rules alone, with zero
LLM and zero API keys. The LLM is augmentation only, and for a compliance gate
the safe direction is *fail-closed*: augmentation may only make a verdict more
restrictive (clear -> flag -> block) and only lower confidence, never the
reverse.

ASSUMPTIONS
-----------
1. Rulesets are advisory heuristics, **not legal advice**. Citations are
   provided for operator orientation only.
2. ``aegis`` resolves as a PEP 420 namespace package when this add-on is dropped
   into ``src/aegis/comply/`` of the main repo (same as Phase 10/11).
3. ``structlog``, ``yaml``, ``typer``, ``fastapi``, ``asyncpg`` and any LLM
   dependency are all optional at import time; the deterministic core never
   requires them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

PHASE: str = "phase8"
VERSION: str = "0.8.0"

#: Feature flags surfaced to ``aegis comply doctor`` and the dashboard.
FEATURE_FLAGS: dict[str, bool] = {
    "rule_engine": True,
    "trademark_screening": True,
    "counterfeit_detection": True,
    "risk_matrix": True,
    "llm_augmentation": True,  # opt-in; degrades to no-op when gateway absent
    "live_trademark_lookup": False,  # opt-in; deterministic local path is default
}

__all__ = [
    "FEATURE_FLAGS",
    "PHASE",
    "VERSION",
    "ComplianceEngine",
    "ComplianceRequest",
    "ComplianceVerdict",
    "ComplianceVerdictResult",
    "Jurisdiction",
    "RiskCategory",
    "Severity",
    "evaluate_for_compliance_node",
]

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aegis.comply.bridge.agents_bridge import evaluate_for_compliance_node
    from aegis.comply.engine import ComplianceEngine
    from aegis.comply.schemas import (
        ComplianceRequest,
        ComplianceVerdict,
        ComplianceVerdictResult,
        Jurisdiction,
        RiskCategory,
        Severity,
    )


def __getattr__(name: str) -> Any:
    """Lazy attribute access so importing the package is cheap and side-effect free."""
    if name == "ComplianceEngine":
        from aegis.comply.engine import ComplianceEngine

        return ComplianceEngine
    if name in {
        "ComplianceRequest",
        "ComplianceVerdictResult",
        "ComplianceVerdict",
        "Jurisdiction",
        "Severity",
        "RiskCategory",
    }:
        from aegis.comply import schemas

        return getattr(schemas, name)
    if name == "evaluate_for_compliance_node":
        from aegis.comply.bridge.agents_bridge import evaluate_for_compliance_node

        return evaluate_for_compliance_node
    raise AttributeError(f"module 'aegis.comply' has no attribute {name!r}")
