"""
aegis.execution_intel.scoring — the Risk Engine (Rule 6).

Assembles an ``ExecutionScoreVector`` of SIX SEPARATED metrics. The metrics are
never merged into a composite — each is sourced independently and reported on
its own axis. Any metric without a measured basis is left ``None`` (UNVERIFIED),
never substituted with a default (Rule 1).

  risk          — caller-supplied plan risk (e.g. ExecutionPlan.risk_score)
  confidence    — caller-supplied predictor confidence
  trust         — measured supplier fulfillment trust (SupplierIntel)
  evidence      — caller-supplied opportunity evidence score (memory layer)
  execution     — measured success rate of settled plans (ExecutionMemory)
  survivability — deterministic simulation (ExecutionSimulator)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from aegis.execution_intel.memory import ExecutionMemory
from aegis.execution_intel.schemas import ExecutionScoreVector
from aegis.execution_intel.simulator import ExecutionSimulator
from aegis.execution_intel.supplier import SupplierIntel

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.execution_intel.scoring")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class ScoreVectorBuilder:
    """Assemble the six-axis ExecutionScoreVector (Rule 6)."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id
        self._suppliers = SupplierIntel(db_pool, tenant_id=tenant_id)
        self._memory = ExecutionMemory(db_pool, tenant_id=tenant_id)
        self._simulator = ExecutionSimulator(db_pool, tenant_id=tenant_id)

    async def build(
        self,
        plan_id: str,
        *,
        supplier_name: str | None = None,
        region: str = "GLOBAL",
        category: str = "general",
        risk: float | None = None,
        confidence: float | None = None,
        evidence: float | None = None,
        compliance_risk: float | None = None,
    ) -> ExecutionScoreVector:
        """Build the vector. Each axis is measured independently or UNVERIFIED."""
        source: dict[str, str] = {}

        source["risk"] = "measured" if risk is not None else "unverified"
        source["confidence"] = "measured" if confidence is not None else "unverified"
        source["evidence"] = "measured" if evidence is not None else "unverified"

        trust: float | None = None
        if supplier_name:
            s = await self._suppliers.trust_score(supplier_name)
            trust = s.trust
        source["trust"] = "measured" if trust is not None else "unverified"

        execution = await self._memory.success_rate()
        source["execution"] = "measured" if execution is not None else "unverified"

        surv = await self._simulator.simulate(
            plan_id,
            supplier_name=supplier_name,
            region=region,
            category=category,
            compliance_risk=compliance_risk,
        )
        survivability = surv.overall
        source["survivability"] = "measured" if survivability is not None else "unverified"

        return ExecutionScoreVector(
            plan_id=plan_id,
            risk=risk,
            confidence=confidence,
            trust=trust,
            evidence=evidence,
            execution=execution,
            survivability=survivability,
            source=source,
        )
