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

        Works in all modes — no network calls, no capital at risk.
        """
        unit_cost = _derive_unit_cost(intent)
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
        from aegis.fulfillment.printful import PrintfulClient

        client = PrintfulClient(api_key=self._settings.printful_api_key)
        return await client.create_orders(
            product_ref=plan.trend_id,
            quantity=plan.quantity,
            unit_price_usd=plan.unit_price_usd,
        )

    async def _dispatch_dropship(self, plan: ExecutionPlan) -> list[str]:
        from aegis.fulfillment.cjdropshipping import CJDropshipClient

        client = CJDropshipClient(api_key=self._settings.cjdropship_api_key)
        return await client.create_orders(
            product_ref=plan.trend_id,
            quantity=plan.quantity,
            unit_price_usd=plan.unit_price_usd,
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
    """Estimate COGS from intent data (50% of implied sale price)."""
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
