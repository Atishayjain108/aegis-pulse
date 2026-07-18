"""
aegis.execution_intel — PROJECT OMEGA Phase D: Execution Intelligence.

The objective is NOT autonomous execution. It is *understanding* execution:
how plans succeed and fail, which suppliers are reliable, what assumptions a
recommendation rests on, and whether those assumptions are verified.

Stages shipped so far:
  S1 — Execution Knowledge Engine (Rule 1, 2): ``ExecutionMemory`` +
       ``ExecutionRecord`` / ``ExecutionAssumption`` over ``execution_records``
       and ``execution_assumptions`` (migration 0021).
  S2 — Supplier Intelligence (Rule 3): ``SupplierIntel`` + ``SupplierTrustScore``
       over ``supplier_reliability`` (reuses ``EntityMemory``).

Doctrine: Reality First (Rule 1). Every unmeasurable value is ``UNVERIFIED``,
never guessed. All persistence is best-effort — the ledger never breaks the
live execution path.
"""

from __future__ import annotations

from aegis.execution_intel.audit import ExecutionAuditor, arbitrage_rationale
from aegis.execution_intel.buyer import BuyerIntel
from aegis.execution_intel.explain import explain_plan
from aegis.execution_intel.forecast import FailureForecaster
from aegis.execution_intel.memory import ExecutionMemory
from aegis.execution_intel.schemas import (
    ArbitrageRationale,
    BuyerDemandProxy,
    ExecutionAssumption,
    ExecutionAudit,
    ExecutionExplanation,
    ExecutionRecord,
    ExecutionScoreVector,
    FailureForecast,
    ForecastAccuracy,
    SupplierTrustScore,
    SurvivabilityScore,
)
from aegis.execution_intel.scoring import ScoreVectorBuilder
from aegis.execution_intel.simulator import ExecutionSimulator
from aegis.execution_intel.supplier import SupplierIntel
from aegis.execution_intel.taxonomy import (
    UNVERIFIED,
    AssumptionKind,
    AssumptionStatus,
    ExecutionFailureCategory,
    ExecutionOutcome,
)

__version__ = "0.1.0"  # Phase D, stages S1+S2

__all__ = [
    "UNVERIFIED",
    "ArbitrageRationale",
    "AssumptionKind",
    "AssumptionStatus",
    "BuyerDemandProxy",
    "BuyerIntel",
    "ExecutionAssumption",
    "ExecutionAudit",
    "ExecutionAuditor",
    "ExecutionExplanation",
    "ExecutionFailureCategory",
    "ExecutionMemory",
    "ExecutionOutcome",
    "ExecutionRecord",
    "ExecutionScoreVector",
    "ExecutionSimulator",
    "FailureForecast",
    "FailureForecaster",
    "ForecastAccuracy",
    "ScoreVectorBuilder",
    "SupplierIntel",
    "SupplierTrustScore",
    "SurvivabilityScore",
    "arbitrage_rationale",
    "explain_plan",
]
