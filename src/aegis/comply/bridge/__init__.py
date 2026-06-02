"""Cross-phase bridges (Protocol-based; no hard inter-phase imports)."""

from __future__ import annotations

from aegis.comply.bridge.agents_bridge import (
    evaluate_for_compliance_node,
    get_engine,
)
from aegis.comply.bridge.phase4_bridge import (
    maybe_escalate,
    to_escalation_fields,
)

__all__ = [
    "evaluate_for_compliance_node",
    "get_engine",
    "maybe_escalate",
    "to_escalation_fields",
]
