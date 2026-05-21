"""Risk gate chain for Phase 4.

The risk layer is a sequence of pure functions (each a ``RiskGate``)
applied to a freshly-composed ``Alert``. Gates can only DOWNGRADE an
``ENTER`` verdict to ``BLOCK`` — they never upgrade, never alter scores,
and never reach the network. This guarantee is the foundation of
auditability: every block is traceable to a single gate and a single
``AEGIS-EXEC-NNNN`` code.

Public surface:

* ``RiskGate`` — the callable Protocol every gate satisfies.
* ``GateOutcome`` — the per-gate result returned to the chain.
* ``GateChain`` — runs gates in order, short-circuiting on first
  failure.
* ``default_chain`` — builds the chain used by ``Pipeline`` out of the
  box (margin floor → loss-probability ceiling → confidence floor).
* The four shipped gates: ``margin_floor_gate``,
  ``loss_probability_gate``, ``confidence_gate``,
  ``tenant_blocklist_gate``.
"""

from __future__ import annotations

from aegis.execute.risk.gates import (
    GateChain,
    GateOutcome,
    RiskGate,
    confidence_gate,
    default_chain,
    loss_probability_gate,
    margin_floor_gate,
    tenant_blocklist_gate,
)

__all__ = [
    "GateChain",
    "GateOutcome",
    "RiskGate",
    "confidence_gate",
    "default_chain",
    "loss_probability_gate",
    "margin_floor_gate",
    "tenant_blocklist_gate",
]
