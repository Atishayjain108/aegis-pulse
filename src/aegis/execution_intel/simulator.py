"""
aegis.execution_intel.simulator — Execution Simulation (Rule 5).

Produces a deterministic Execution Survivability Score by modelling the six
failure modes named in the protocol: inventory, supplier, shipping, compliance,
payment, demand collapse.

Anti-theater doctrine (Rule 2 risk R2): this is NOT a Monte Carlo with invented
inputs. Each mode's survival probability is sourced from a MEASURED input or it
is marked ``assumed``/``unverified`` in the ``basis``. If the critical supplier
input has no measured basis, the simulator ABSTAINS (``overall=None``,
``abstained=True``) rather than emit a confident-looking number (Rule 1).

The score is ADVISORY. It is never a hard gate and never triggers execution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from aegis.execution_intel.buyer import BuyerIntel
from aegis.execution_intel.memory import ExecutionMemory
from aegis.execution_intel.schemas import SurvivabilityScore
from aegis.execution_intel.supplier import SupplierIntel

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.execution_intel.simulator")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# Conservative survival assumptions used ONLY when no measured base rate exists.
# These are explicitly flagged "assumed" in the basis so the consumer knows the
# number is not learned. They are deliberately pessimistic (Reality First).
_ASSUMED_SURVIVAL = 0.9

# Failure-category → simulation mode, for translating measured base rates.
_CATEGORY_TO_MODE = {
    "inventory_failure": "inventory",
    "supplier_failure": "supplier",
    "no_verified_supplier": "supplier",
    "shipping_delay": "shipping",
    "compliance_issue": "compliance",
    "payment_issue": "payment",
    "demand_collapse": "demand",
    "margin_evaporated": "demand",
}


class ExecutionSimulator:
    """Compute an advisory Execution Survivability Score for a plan (Rule 5)."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id
        self._suppliers = SupplierIntel(db_pool, tenant_id=tenant_id)
        self._buyers = BuyerIntel(db_pool, tenant_id=tenant_id)
        self._memory = ExecutionMemory(db_pool, tenant_id=tenant_id)

    async def simulate(
        self,
        plan_id: str,
        *,
        supplier_name: str | None = None,
        region: str = "GLOBAL",
        category: str = "general",
        compliance_risk: float | None = None,
    ) -> SurvivabilityScore:
        """Run the deterministic survivability simulation.

        Critical input is supplier reliability. With no measured supplier trust
        the simulator abstains — we will not pretend to know whether a plan
        survives when its single most decisive dependency is unverified.
        """
        mode_survival: dict[str, float] = {}
        basis: dict[str, str] = {}

        # Learned base rates from settled history (Rule 8 input).
        rates = await self._memory.failure_base_rates()
        n_settled = int(rates.get("n_settled", 0) or 0)

        # --- Supplier mode (critical) -------------------------------------
        supplier_measured = False
        if supplier_name:
            score = await self._suppliers.trust_score(supplier_name)
            if score.trust is not None:  # measured fulfillment history
                mode_survival["supplier"] = round(float(score.trust), 4)
                basis["supplier"] = "measured"
                supplier_measured = True
            else:
                basis["supplier"] = "unverified"
        else:
            basis["supplier"] = "unverified"

        # --- Demand mode --------------------------------------------------
        proxy = await self._buyers.demand_proxy(region, category)
        if proxy.demand_intensity is not None:
            # Demand intensity is a proxy; survival rises with it. Flagged proxy.
            mode_survival["demand"] = round(
                0.5 + 0.5 * max(0.0, min(1.0, proxy.demand_intensity)), 4
            )
            basis["demand"] = "proxy"
        else:
            basis["demand"] = "unverified"

        # --- Compliance mode ----------------------------------------------
        if compliance_risk is not None:
            mode_survival["compliance"] = round(
                max(0.0, 1.0 - max(0.0, min(1.0, compliance_risk))), 4
            )
            basis["compliance"] = "measured"

        # --- History-driven modes (inventory / shipping / payment) --------
        for mode in ("inventory", "shipping", "payment"):
            measured = self._measured_rate_for_mode(rates, mode) if n_settled > 0 else None
            if measured is not None:
                mode_survival[mode] = round(1.0 - measured, 4)
                basis[mode] = "measured"
            else:
                mode_survival[mode] = _ASSUMED_SURVIVAL
                basis[mode] = "assumed"

        # --- Abstain when the critical supplier input is unverified -------
        if not supplier_measured:
            return SurvivabilityScore(
                plan_id=plan_id,
                overall=None,
                abstained=True,
                reason=(
                    "supplier reliability is UNVERIFIED — refusing to estimate "
                    "survivability without the decisive input (Rule 1)"
                ),
                mode_survival=mode_survival,
                basis=basis,
            )

        overall = 1.0
        for v in mode_survival.values():
            overall *= v
        return SurvivabilityScore(
            plan_id=plan_id,
            overall=round(overall, 4),
            abstained=False,
            reason="product of per-mode survival probabilities",
            mode_survival=mode_survival,
            basis=basis,
        )

    @staticmethod
    def _measured_rate_for_mode(
        rates: dict[str, float | int], mode: str,
    ) -> float | None:
        """Sum measured failure rates whose category maps to ``mode``."""
        total = 0.0
        found = False
        for category, m in _CATEGORY_TO_MODE.items():
            if m != mode:
                continue
            r = rates.get(category)
            if isinstance(r, (int | float)) and category != "n_settled":
                total += float(r)
                found = True
        return min(1.0, total) if found else None
