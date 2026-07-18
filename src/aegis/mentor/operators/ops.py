"""OpsOp — pricing, fulfillment, settlement (advisory; capital stays gated).

The operations operator turns grounded cost/demand numbers into an **advisory**
operating plan, reusing the real Phase 6 capital machinery
(:mod:`aegis.execute.pricing`, :mod:`aegis.execute.engine`):

* **Pricing** — :class:`~aegis.execute.pricing.PricingStrategy` computes a
  multi-objective sale price from a *real* unit cost + demand/inventory/competitor
  inputs. No real cost → no price (grounded-or-silent: a price off a guessed cost
  would be fiction).
* **Plan sketch** — :class:`~aegis.execute.engine.ExecutionEngine` in **advisory
  mode** produces a paper sizing sketch. Advisory mode never places an order and
  never moves capital; the plan is returned for inspection only.

Hard rule (blueprint M5 + non-negotiable #7): OpsOp **never executes** a plan.
It only ever calls ``create_plan`` (advisory) — never ``execute_plan`` — so every
capital-moving action stays behind the existing capital/compliance gate.

Grounded inputs arrive via ``context.extras["ops_inputs"]`` (a verified supplier
cost from :class:`SupplierOp`, observed demand, etc.). Everything is injectable so
tests run with fakes and zero network / zero capital.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog

from aegis.mentor.operators.base import OperatorContext, OperatorResult

if TYPE_CHECKING:
    from aegis.mentor.schemas import UserProfile

_log = structlog.get_logger("aegis.mentor.operators.ops")


class OpsOp:
    """Operations operator — pricing + advisory plan, capital always gated."""

    name = "ops"

    def __init__(
        self,
        *,
        engine: Any | None = None,
        pricer: Any | None = None,
        settings: Any | None = None,
    ) -> None:
        # All injectable so tests run with fakes, zero network, zero capital.
        self._engine = engine        # ExecutionEngine-like: async create_plan(intent)
        self._pricer = pricer        # PricingStrategy-like: compute_price(...)
        self._settings = settings    # ExecuteSettings-like (advisory by default)

    # ------------------------------------------------------------------

    def _get_settings(self) -> Any | None:
        if self._settings is not None:
            return self._settings
        try:
            from aegis.execute.config import ExecuteSettings

            return ExecuteSettings()
        except Exception as exc:
            _log.debug("mentor.ops.settings_unavailable", error=str(exc)[:200])
            return None

    def _get_pricer(self, sku: str, seed: int) -> Any | None:
        if self._pricer is not None:
            return self._pricer
        try:
            from aegis.execute.pricing import PricingStrategy

            return PricingStrategy(sku, seed=seed)
        except Exception as exc:
            _log.debug("mentor.ops.pricer_unavailable", error=str(exc)[:200])
            return None

    def _get_engine(self, settings: Any | None) -> Any | None:
        if self._engine is not None:
            return self._engine
        if settings is None:
            return None
        try:
            from aegis.execute.engine import ExecutionEngine

            return ExecutionEngine(settings)
        except Exception as exc:
            _log.debug("mentor.ops.engine_unavailable", error=str(exc)[:200])
            return None

    # ------------------------------------------------------------------

    async def run(
        self,
        profile: UserProfile,
        request: str,
        context: OperatorContext,
    ) -> OperatorResult:
        inputs = dict(context.extras.get("ops_inputs", {}) or {})
        unit_cost = self._as_float(inputs.get("unit_cost_usd"))

        # Grounded-or-silent: pricing/sizing without a real cost is fiction.
        if unit_cost is None or unit_cost <= 0:
            return OperatorResult(
                operator=self.name,
                reasoning=(
                    "No verified unit cost available — cannot price or size a plan "
                    "without a real supplier cost. Source + verify a supplier first "
                    "(SupplierOp), then re-run ops. (Grounded-or-silent.)"
                ),
                actions=[
                    "Get a verified unit cost from a real supplier before any pricing "
                    "or capital-moving step (which stays gated)."
                ],
                confidence=0.0,
                data={"pricing": None, "plan": None, "capital_gated": True,
                      "executed": False},
            )

        findings: list[str] = []
        sources: set[str] = set()
        actions: list[str] = []

        # 1. Pricing (real cost → multi-objective price).
        pricing = self._price(inputs, unit_cost, request, profile, findings, sources)

        # 2. Advisory plan sketch (capital gated; never executed).
        plan = await self._advisory_plan(inputs, profile, request, findings, sources)

        # 3. Sector margin gate check (grounded benchmark).
        self._margin_gate(context, pricing, findings, sources)

        actions.append(
            "ADVISORY ONLY: this plan moves no capital. Any real order stays behind "
            "the capital + compliance approval gate."
        )
        if pricing is not None:
            actions.append(
                f"Set the sale price near ${pricing['price_usd']:g} "
                f"(gross margin {pricing['margin_pct']}%) and validate with a small batch."
            )

        confidence = self._confidence(pricing, plan)
        reasoning = (
            f"Advisory operating plan for '{request or profile.sector}': "
            f"{'priced' if pricing else 'no price'}, "
            f"{'plan sketched' if plan else 'no plan sketch'}. "
            "Capital-moving execution stays gated — nothing was placed."
        )

        return OperatorResult(
            operator=self.name,
            findings=findings,
            actions=actions,
            sources=sorted(sources),
            reasoning=reasoning,
            confidence=confidence,
            data={
                "pricing": pricing,
                "plan": plan,
                "capital_gated": True,
                "executed": False,
            },
        )

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------

    def _price(
        self,
        inputs: dict[str, Any],
        unit_cost: float,
        request: str,
        profile: UserProfile,
        findings: list[str],
        sources: set[str],
    ) -> dict[str, Any] | None:
        sku = str(inputs.get("sku") or (request or profile.sector or "sku")).strip()[:64]
        seed = int(inputs.get("pricing_seed", 7))
        pricer = self._get_pricer(sku, seed)
        if pricer is None:
            return None
        demand = self._as_float(inputs.get("demand_units_per_h")) or 0.0
        inventory = int(inputs.get("inventory_level", 100) or 0)
        competitors = [
            c for c in (inputs.get("competitor_prices") or [])
            if isinstance(c, int | float)
        ]
        try:
            price = float(
                pricer.compute_price(unit_cost, demand, inventory, competitors or None)
            )
        except Exception as exc:
            _log.warning("mentor.ops.pricing_failed", error=str(exc)[:200])
            return None
        margin = price - unit_cost
        margin_pct = round((margin / price) * 100, 1) if price > 0 else 0.0
        findings.append(
            f"Recommended price ${price:g} on a ${unit_cost:g} verified unit cost "
            f"→ gross margin {margin_pct}% (advisory)."
        )
        sources.add("execute:pricing_strategy")
        return {
            "sku": sku,
            "price_usd": round(price, 4),
            "unit_cost_usd": round(unit_cost, 4),
            "margin_usd": round(margin, 4),
            "margin_pct": margin_pct,
        }

    # ------------------------------------------------------------------
    # Advisory plan (capital gated — never executed)
    # ------------------------------------------------------------------

    async def _advisory_plan(
        self,
        inputs: dict[str, Any],
        profile: UserProfile,
        request: str,
        findings: list[str],
        sources: set[str],
    ) -> dict[str, Any] | None:
        settings = self._get_settings()
        engine = self._get_engine(settings)
        if engine is None:
            return None
        intent = self._build_intent(inputs, profile, request)
        if intent is None:
            return None
        try:
            plan = await engine.create_plan(intent)
        except Exception as exc:
            _log.warning("mentor.ops.plan_failed", error=str(exc)[:200])
            return None
        mode = getattr(plan, "execution_mode", "advisory")
        qty = int(getattr(plan, "quantity", 0) or 0)
        total_capital = float(getattr(plan, "total_capital_usd", 0.0) or 0.0)
        requires_approval = bool(getattr(plan, "requires_approval", True))
        findings.append(
            f"Advisory plan sketch ({mode}): {qty} unit(s), "
            f"~${total_capital:g} capital, requires_approval={requires_approval}. "
            "No order placed."
        )
        sources.add("execute:execution_engine")
        return {
            "execution_mode": mode,
            "quantity": qty,
            "total_capital_usd": round(total_capital, 2),
            "estimated_profit_usd": round(
                float(getattr(plan, "estimated_profit_usd", 0.0) or 0.0), 2
            ),
            "requires_approval": requires_approval,
            "supplier_name": getattr(plan, "supplier_name", None),
        }

    @staticmethod
    def _build_intent(
        inputs: dict[str, Any], profile: UserProfile, request: str
    ) -> Any | None:
        try:
            from aegis.execute.schemas.intent import ExecutionIntent, IntentKind
        except Exception:
            return None
        try:
            tenant = UUID(profile.tenant_id)
        except Exception:
            return None
        units = int(inputs.get("expected_units", 0) or 0)
        cost = OpsOp._as_float(inputs.get("unit_cost_usd")) or 0.0
        margin = OpsOp._as_float(inputs.get("expected_margin_usd"))
        loss_p = OpsOp._as_float(inputs.get("loss_probability"))
        try:
            return ExecutionIntent(
                intent_id=uuid4().hex,
                alert_id=uuid4().hex,
                tenant_id=tenant,
                trend_id=(request or profile.sector or "mentor-ops")[:256] or "mentor-ops",
                kind=IntentKind.ENTER_POSITION,
                advised_units=units,
                advised_capital_usd=round(units * cost, 2),
                expected_margin_usd=margin,
                loss_probability=loss_p if loss_p is not None and 0.0 <= loss_p <= 1.0 else None,
                rationale="AEGIS Mentor advisory operating plan (no capital moved).",
            )
        except Exception as exc:
            _log.debug("mentor.ops.intent_build_failed", error=str(exc)[:200])
            return None

    # ------------------------------------------------------------------

    @staticmethod
    def _margin_gate(
        context: OperatorContext,
        pricing: dict[str, Any] | None,
        findings: list[str],
        sources: set[str],
    ) -> None:
        if pricing is None or context.sector_pack is None:
            return
        try:
            floor = context.sector_pack.benchmarks().get("min_viable_gross_margin_pct")
        except Exception:
            return
        if not isinstance(floor, int | float):
            return
        floor_pct = round(floor * 100, 1)
        tag = getattr(context.sector_pack, "sector_tag", "baseline")
        sources.add(f"sector_pack:{tag}")
        if pricing["margin_pct"] < floor_pct:
            findings.append(
                f"⚠ Margin {pricing['margin_pct']}% is BELOW the sector's minimum "
                f"viable gross margin ({floor_pct}%) — unit economics likely won't "
                "survive ad spend. Rework cost or price before committing."
            )
        else:
            findings.append(
                f"Margin {pricing['margin_pct']}% clears the sector floor "
                f"({floor_pct}%)."
            )

    @staticmethod
    def _as_float(v: Any) -> float | None:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _confidence(
        pricing: dict[str, Any] | None, plan: dict[str, Any] | None
    ) -> float:
        score = 0.0
        if pricing is not None:
            score += 0.45
        if plan is not None:
            score += 0.25
        return round(min(0.7, score), 3)
