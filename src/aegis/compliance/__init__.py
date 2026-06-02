"""
AEGIS Pulse — Phase 8: Regulatory & Compliance Engine.

Scores every opportunity against a composite compliance risk matrix before
Phase 6 execution.  Checks covered:

  - Trademark / Patent / Copyright (IPR)   — USPTO PatentsView + EUIPO TMview
  - FDA import restrictions / enforcements  — OpenFDA REST API (no key)
  - FTC advertising / endorsement rules     — rule engine (30+ patterns)
  - EU DSA / GPSR + India DPDP + GDPR      — jurisdiction rule engine
  - AML / Sanctions                         — OFAC SDN list + FATF grey-list
  - Counterfeit detection                   — text heuristics + optional CLIP

Decision matrix:
  overall_risk > 0.70  → BLOCK      (reject execution entirely)
  overall_risk > 0.50  → ESCALATE   (human review required)
  overall_risk ≤ 0.50  → PROCEED    (auto-execute if P0/P1)
"""

from __future__ import annotations

from aegis.compliance.config import ComplianceSettings
from aegis.compliance.engine import ComplianceEngine
from aegis.compliance.schemas import (
    ComplianceRequest,
    ComplianceRiskAssessment,
    Recommendation,
)

__all__ = [
    "ComplianceEngine",
    "ComplianceRiskAssessment",
    "ComplianceRequest",
    "ComplianceSettings",
    "Recommendation",
]

__version__ = "8.0.0"
