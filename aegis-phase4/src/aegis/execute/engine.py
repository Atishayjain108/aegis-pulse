"""Capital Execution Engine — Phase 6.

Converts an `ExecutionIntent` (Phase 4 advisory output) into a concrete
`ExecutionPlan` with Kelly-sized quantities, fulfillment routing, and risk
scoring, then dispatches it to the appropriate fulfillment backend.

Three-tier execution model:
  advisory: logs everything, places nothing (CI default).
  staging:  real orders, mock/Stripe-test payment.
  live:     irreversible, full capital at risk.

The engine NEVER executes in advisory mode — it returns a detailed plan for
inspection only. In staging/live mode, the plan must first clear the
killswitch and (for P0/P1) the approval workflow.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import structlog
from pydantic import BaseModel, ConfigDict, Field

from aegis.execute.constants import (
    FULFILLMENT_DROPSHIP_MAX_QTY,
    FULFILLMENT_POD_MAX_QTY,
    KELLY_CAPITAL_DEFAULT_USD,
    MODE_ADVISORY,
)
from aegis.execute.schemas.intent import ExecutionIntent, IntentKind
from aegis.execute.sizing.kelly import KellyAdvisor

if TYPE_CHECKING:
    from aegis.execute.config import ExecuteSettings

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FulfillmentMethod(StrEnum):
    POD = "pod"          # print-on-demand (Printful/Printify)
    DROPSHIP = "dropship"  # CJ Dropshipping / Spocket
    INVENTORY = "inventory"  # self-managed stock


class PlanStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FULFILLED = "fulfilled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # FIX-4 (forensic audit): no real supplier could be verified, so the plan
    # is NOT sized on fictional cost data — it is blocked at zero quantity.
    NO_VERIFIED_SUPPLIER = "no_verified_supplier"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ExecutionPlan(BaseModel):
    """Concrete execution plan produced by the engine.

    Immutable after creation — use `model_copy(update={...})` for state
    transitions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    intent_id: str
    trend_id: str
    quantity: int = Field(ge=0)
    fulfillment_method: FulfillmentMethod
    unit_cost_usd: float = Field(ge=0.0)
    unit_price_usd: float = Field(ge=0.0)
    total_capital_usd: float = Field(ge=0.0)
    estimated_profit_usd: float
    kelly_fraction_raw: float
    kelly_fraction_used: float
    risk_score: float = Field(ge=0.0, le=1.0)
    requires_approval: bool
    status: PlanStatus = PlanStatus.PENDING
    execution_mode: str
    # Name of the REAL supplier whose cost was verified (staging/live only).
    # None in advisory mode — there is no verified supplier (Reality First).
    supplier_name: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    order_ids: tuple[str, ...] = Field(default_factory=tuple)


class ExecutionOutcome(BaseModel):
    """Result returned from `ExecutionEngine.execute_plan()`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    status: str
    order_ids: tuple[str, ...] = Field(default_factory=tuple)
    executed_at: datetime = Field(default_factory=_utc_now)
    error: str | None = None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ExecutionEngine:
    """Orchestrates intent → plan → dispatch for Phase 6.

    Thread-safe per instance; hold one per process.
    """

    __slots__ = ("_settings", "_kelly", "_daily_pnl")

    def __init__(self, settings: ExecuteSettings) -> None:
        self._settings = settings
        self._kelly = KellyAdvisor(
            fraction=settings.capital_kelly_fraction,
            max_pct_of_capital=0.10,
        )
        self._daily_pnl: Decimal = Decimal(0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_plan(self, intent: ExecutionIntent) -> ExecutionPlan:
        """Derive a concrete execution plan from an advisory intent.

        In advisory mode the unit cost is an explicitly-labelled estimate
        (``_derive_unit_cost``) used only for a paper sketch — nothing is placed.
        In staging/live mode the cost MUST come from a real supplier API
        (``_get_verified_unit_cost``); when no supplier can be verified the plan
        is blocked at zero quantity (``NO_VERIFIED_SUPPLIER``) rather than sized
        on a fabricated ``margin × 2`` constant (FIX-4, forensic audit).
        """
        supplier_name: str | None = None
        if self._settings.mode == MODE_ADVISORY:
            unit_cost = _derive_unit_cost(intent)
        else:
            verified = await self._get_verified_unit_cost(intent)
            if verified is None:
                _log.warning(
                    "execute.engine.no_verified_supplier",
                    trend_id=intent.trend_id,
                    mode=self._settings.mode,
                )
                return ExecutionPlan(
                    intent_id=intent.intent_id,
                    trend_id=intent.trend_id,
                    quantity=0,
                    fulfillment_method=FulfillmentMethod.POD,
                    unit_cost_usd=0.0,
                    unit_price_usd=0.0,
                    total_capital_usd=0.0,
                    estimated_profit_usd=0.0,
                    kelly_fraction_raw=0.0,
                    kelly_fraction_used=0.0,
                    risk_score=1.0,
                    requires_approval=True,
                    status=PlanStatus.NO_VERIFIED_SUPPLIER,
                    execution_mode=self._settings.mode,
                )
            unit_cost, supplier_name = verified

        unit_price = _derive_unit_price(intent, unit_cost)
        margin = unit_price - unit_cost

        sizing = self._kelly.advise(
            expected_margin_usd=margin,
            loss_probability=intent.loss_probability,
            unit_cost_usd=unit_cost,
            capital_usd=KELLY_CAPITAL_DEFAULT_USD,
        )

        quantity = min(
            sizing.units,
            int(self._settings.capital_max_risk_usd // max(unit_cost, 0.01)),
        )
        total_capital = quantity * unit_cost
        total_profit = quantity * margin

        fulfillment = _select_fulfillment(quantity, intent)
        risk_score = _compute_risk(intent, fulfillment)

        requires_approval = (
            intent.kind == IntentKind.ENTER_POSITION
            and not self._settings.auto_execute_p0
        )

        plan = ExecutionPlan(
            intent_id=intent.intent_id,
            trend_id=intent.trend_id,
            quantity=quantity,
            fulfillment_method=fulfillment,
            unit_cost_usd=round(unit_cost, 4),
            unit_price_usd=round(unit_price, 4),
            total_capital_usd=round(total_capital, 4),
            estimated_profit_usd=round(total_profit, 4),
            kelly_fraction_raw=round(sizing.kelly_fraction_raw, 6),
            kelly_fraction_used=round(sizing.kelly_fraction_used, 6),
            risk_score=round(risk_score, 4),
            requires_approval=requires_approval,
            execution_mode=self._settings.mode,
            supplier_name=supplier_name,
        )

        _log.info(
            "execute.engine.plan_created",
            plan_id=plan.plan_id,
            trend_id=intent.trend_id,
            qty=quantity,
            capital_usd=total_capital,
            kelly_used=sizing.kelly_fraction_used,
            fulfillment=fulfillment.value,
            mode=self._settings.mode,
        )
        return plan

    async def execute_plan(self, plan: ExecutionPlan) -> ExecutionOutcome:
        """Dispatch the plan to the appropriate fulfillment backend.

        In advisory mode this is always a no-op (returns immediately with
        status="advisory_mode").
        """
        if self._settings.mode == MODE_ADVISORY:
            _log.info("execute.engine.advisory_skip", plan_id=plan.plan_id)
            return ExecutionOutcome(
                plan_id=plan.plan_id,
                status="advisory_mode",
            )

        if await self._drawdown_breached():
            _log.warning(
                "execute.engine.drawdown_halt",
                plan_id=plan.plan_id,
                daily_pnl=float(self._daily_pnl),
                limit=self._settings.capital_daily_loss_limit_usd,
            )
            return ExecutionOutcome(
                plan_id=plan.plan_id,
                status="halted_drawdown",
                error="daily loss limit exceeded",
            )

        try:
            order_ids = await self._dispatch(plan)
            outcome = ExecutionOutcome(
                plan_id=plan.plan_id,
                status="executed" if order_ids else "failed",
                order_ids=tuple(order_ids),
            )
        except Exception as exc:
            _log.error(
                "execute.engine.dispatch_error",
                plan_id=plan.plan_id,
                error=str(exc),
            )
            outcome = ExecutionOutcome(
                plan_id=plan.plan_id,
                status="failed",
                error=str(exc),
            )

        _log.info(
            "execute.engine.outcome",
            plan_id=plan.plan_id,
            status=outcome.status,
            order_count=len(outcome.order_ids),
        )
        return outcome

    def record_settlement(self, pnl_usd: float) -> None:
        """Update running daily PnL after a settled order."""
        self._daily_pnl += Decimal(str(pnl_usd))

    def reset_daily_pnl(self) -> None:
        """Called at start of each trading day."""
        self._daily_pnl = Decimal(0)

    @property
    def daily_pnl(self) -> float:
        return float(self._daily_pnl)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_verified_unit_cost(
        self, intent: ExecutionIntent
    ) -> tuple[float, str] | None:
        """Return ``(real unit cost USD, supplier_name)`` from a supplier API.

        FIX-4 (forensic audit): tries Printful (POD) first, then CJ Dropshipping.
        Returns ``None`` when no real supplier can be verified — the caller then
        BLOCKS the plan instead of sizing a position on a fabricated constant.
        The supplier name lets Phase D attribute reliability + forecasts to the
        real supplier that backed the plan.
        """
        category = getattr(intent, "category", "") or ""
        keywords = list(getattr(intent, "keywords", []) or [])
        recipient = self._supplier_recipient()

        # 1. Printful (print-on-demand)
        try:
            from aegis.fulfillment.printful import PrintfulClient

            printful = PrintfulClient(api_key=self._settings.printful_api_key)
            product = await printful.find_matching_product(category, keywords)
            if product is not None and recipient is not None:
                cost = await printful.get_real_cost(product.variant_id, recipient)
                if cost is not None and cost > 0:
                    return float(cost), "printful"
        except Exception as exc:
            _log.warning("execute.engine.printful_verify_failed", error=str(exc))

        # 2. CJ Dropshipping
        try:
            from aegis.fulfillment.cjdropshipping import CJDropshipClient

            cj = CJDropshipClient(api_key=self._settings.cjdropship_api_key)
            search = getattr(cj, "search_product", None)
            if callable(search):
                product = await search(category, keywords)
                cost_usd = getattr(product, "cost_usd", None) if product else None
                if cost_usd is not None and cost_usd > 0:
                    return float(cost_usd), "cjdropshipping"
        except Exception as exc:
            _log.warning("execute.engine.cj_verify_failed", error=str(exc))

        return None

    def _supplier_recipient(self) -> dict[str, str] | None:
        """Build a real recipient address from settings, or None if unset.

        Reads optional ``fulfillment_recipient_*`` fields off ``ExecuteSettings``.
        When the operator has not configured a real shipping destination this
        returns ``None`` and order creation is refused (no "TBD" placeholder).
        """
        s = self._settings
        address1 = getattr(s, "fulfillment_recipient_address1", "") or ""
        zip_code = getattr(s, "fulfillment_recipient_zip", "") or ""
        country = getattr(s, "fulfillment_recipient_country", "") or ""
        if not (address1 and zip_code and country):
            return None
        return {
            "name": getattr(s, "fulfillment_recipient_name", "AEGIS Operator") or "AEGIS Operator",
            "address1": address1,
            "city": getattr(s, "fulfillment_recipient_city", "") or "",
            "country_code": country,
            "zip": zip_code,
        }

    async def _drawdown_breached(self) -> bool:
        return self._daily_pnl < -Decimal(
            str(self._settings.capital_daily_loss_limit_usd)
        )

    async def _dispatch(self, plan: ExecutionPlan) -> list[str]:
        """Route to fulfillment backend; returns list of order IDs."""
        if plan.fulfillment_method == FulfillmentMethod.POD:
            return await self._dispatch_pod(plan)
        if plan.fulfillment_method == FulfillmentMethod.DROPSHIP:
            return await self._dispatch_dropship(plan)
        return await self._dispatch_inventory(plan)

    async def _dispatch_pod(self, plan: ExecutionPlan) -> list[str]:
        # FIX-4: resolve a REAL catalog variant + recipient before ordering.
        # If either is unavailable, create_orders refuses and returns [] — no
        # fictional "generic T-shirt / TBD address" order is ever placed.
        from aegis.fulfillment.printful import PrintfulClient

        client = PrintfulClient(api_key=self._settings.printful_api_key)
        recipient = self._supplier_recipient()
        product = await client.find_matching_product(category="", keywords=[plan.trend_id])
        variant_id = product.variant_id if product else None
        return await client.create_orders(
            product_ref=plan.trend_id,
            quantity=plan.quantity,
            unit_price_usd=plan.unit_price_usd,
            variant_id=variant_id,
            recipient=recipient,
        )

    async def _dispatch_dropship(self, plan: ExecutionPlan) -> list[str]:
        # REALITY-FIRST (PROJECT OMEGA): resolve a REAL CJ product variant and a
        # REAL recipient before ordering. CJDropshipClient.create_orders refuses
        # (returns []) when either is missing — no "TBD" address / mock SKU order
        # is ever placed.
        from aegis.fulfillment.cjdropshipping import CJDropshipClient

        client = CJDropshipClient(api_key=self._settings.cjdropship_api_key)
        recipient = self._supplier_recipient()
        product_vid: str | None = None
        search = getattr(client, "search_product", None)
        if callable(search):
            try:
                product = await search("", [plan.trend_id])
                product_vid = getattr(product, "vid", None) if product else None
            except Exception as exc:  # never order on a failed resolution
                _log.warning("execute.engine.cj_vid_resolve_failed", error=str(exc))
        return await client.create_orders(
            product_ref=plan.trend_id,
            quantity=plan.quantity,
            unit_price_usd=plan.unit_price_usd,
            product_vid=product_vid,
            recipient=recipient,
        )

    async def _dispatch_inventory(self, plan: ExecutionPlan) -> list[str]:
        # Self-managed inventory: reserve stock then create Shopify draft orders.
        from aegis.fulfillment.shopify import ShopifyClient

        client = ShopifyClient(
            shop_domain=self._settings.shopify_shop_domain,
            access_token=self._settings.shopify_access_token,
        )
        return await client.create_draft_orders(
            product_ref=plan.trend_id,
            quantity=plan.quantity,
            unit_price_usd=plan.unit_price_usd,
        )


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def _derive_unit_cost(intent: ExecutionIntent) -> float:
    """ADVISORY-ONLY estimate of COGS from intent data (50% of implied sale price).

    FIX-4 (forensic audit): this is a paper-sketch heuristic used ONLY in
    advisory mode. It is NOT a real market quote — staging/live plans must use
    ``ExecutionEngine._get_verified_unit_cost`` (real supplier API) instead.
    """
    if intent.expected_margin_usd and intent.expected_margin_usd > 0:
        # margin ≈ sale_price − cost  ⇒  cost ≈ sale_price − margin
        # We don't have sale_price, so use margin as proxy for 50% margin products.
        return intent.expected_margin_usd * 2
    return 10.0  # conservative fallback COGS


def _derive_unit_price(intent: ExecutionIntent, unit_cost: float) -> float:
    if intent.expected_margin_usd and intent.expected_margin_usd > 0:
        return unit_cost + intent.expected_margin_usd
    return unit_cost * 2.0


def _select_fulfillment(quantity: int, intent: ExecutionIntent) -> FulfillmentMethod:
    if quantity <= FULFILLMENT_POD_MAX_QTY:
        return FulfillmentMethod.POD
    if quantity <= FULFILLMENT_DROPSHIP_MAX_QTY:
        return FulfillmentMethod.DROPSHIP
    _ = intent  # kept for future per-intent routing
    return FulfillmentMethod.INVENTORY


def _compute_risk(intent: ExecutionIntent, fulfillment: FulfillmentMethod) -> float:
    demand_risk = 1.0 - (intent.loss_probability if intent.loss_probability is not None else 0.5)
    inventory_risk = 0.20 if fulfillment == FulfillmentMethod.INVENTORY else 0.05
    competition_risk = 0.10  # conservative constant until Phase 7 market-depth data
    raw = demand_risk * 0.70 + inventory_risk * 0.20 + competition_risk * 0.10
    return min(1.0, max(0.0, raw))


__all__: Final = [
    "ExecutionEngine",
    "ExecutionOutcome",
    "ExecutionPlan",
    "FulfillmentMethod",
    "PlanStatus",
]
