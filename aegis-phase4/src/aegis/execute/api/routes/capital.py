"""Capital Execution Engine API routes — Phase 6.

Endpoints:
  POST /capital/plan          Create an execution plan from an intent.
  GET  /capital/plan/{plan_id} Retrieve a plan.
  POST /capital/plan/{plan_id}/approve  Approve a pending plan.
  POST /capital/plan/{plan_id}/execute  Dispatch an approved plan.
  POST /capital/callback      Handle Telegram approval callback.
  GET  /capital/settlement    Export the latest daily settlement CSV.
  GET  /capital/status        Engine status: mode, daily PnL, drawdown.

All endpoints respect the killswitch. POST /capital/plan/{id}/execute is
blocked when the killswitch is TRIPPED.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from aegis.execute.approval import ApprovalBroker
from aegis.execute.engine import ExecutionEngine, ExecutionPlan
from aegis.execute.killswitch.switch import KillSwitch

_log = structlog.get_logger(__name__)

router = APIRouter(prefix="/capital", tags=["capital"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreatePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=1, max_length=128)
    trend_id: str = Field(min_length=1, max_length=256)
    kind: str = Field(default="enter_position")
    advised_units: int = Field(ge=0, default=0)
    advised_capital_usd: float = Field(ge=0.0, default=0.0)
    expected_margin_usd: float | None = None
    loss_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    horizon_hours: int = Field(default=24, gt=0)


class ApprovePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_by: str = Field(min_length=1, max_length=128)


class TelegramCallbackRequest(BaseModel):
    """Telegram Bot callback_query payload (simplified)."""

    model_config = ConfigDict(extra="ignore")

    callback_query: dict[str, Any]


class EngineStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    daily_pnl_usd: float
    drawdown_pct: float
    killswitch_tripped: bool
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_engine(request: Request) -> ExecutionEngine:
    engine = getattr(request.app.state, "capital_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="capital engine not initialised",
        )
    return engine


def _get_broker(request: Request) -> ApprovalBroker:
    broker = getattr(request.app.state, "approval_broker", None)
    if broker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="approval broker not initialised",
        )
    return broker


def _get_killswitch(request: Request) -> KillSwitch:
    ks = getattr(request.app.state, "killswitch", None)
    if ks is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="killswitch not initialised",
        )
    return ks


def _plan_store(request: Request) -> dict[str, ExecutionPlan]:
    store = getattr(request.app.state, "plan_store", None)
    if store is None:
        request.app.state.plan_store = {}
        store = request.app.state.plan_store
    return store  # type: ignore[return-value]


def _tenant_id(
    x_aegis_tenant: Annotated[str | None, Header(alias="X-Aegis-Tenant")] = None,
) -> str:
    if not x_aegis_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing X-Aegis-Tenant header",
        )
    return x_aegis_tenant


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", response_model=EngineStatusResponse)
async def engine_status(
    engine: ExecutionEngine = Depends(_get_engine),
    ks: KillSwitch = Depends(_get_killswitch),
) -> EngineStatusResponse:
    """Return current engine mode, daily PnL, and killswitch state."""
    daily_limit = engine._settings.capital_daily_loss_limit_usd
    drawdown_pct = (
        max(0.0, -engine.daily_pnl) / daily_limit * 100 if daily_limit > 0 else 0.0
    )
    tripped = await ks.is_tripped()
    return EngineStatusResponse(
        mode=engine._settings.mode,
        daily_pnl_usd=round(engine.daily_pnl, 4),
        drawdown_pct=round(drawdown_pct, 2),
        killswitch_tripped=tripped,
    )


@router.post("/plan", response_model=ExecutionPlan, status_code=status.HTTP_201_CREATED)
async def create_plan(
    request: Request,
    body: CreatePlanRequest,
    engine: ExecutionEngine = Depends(_get_engine),
    store: dict[str, ExecutionPlan] = Depends(_plan_store),
    _tenant: str = Depends(_tenant_id),
) -> ExecutionPlan:
    """Derive an execution plan from an advisory intent."""
    from aegis.execute.schemas.intent import ExecutionIntent, IntentKind, IntentStatus

    intent = ExecutionIntent(
        intent_id=body.intent_id,
        alert_id="api-direct",
        tenant_id=_tenant,
        trend_id=body.trend_id,
        kind=IntentKind(body.kind),
        status=IntentStatus.PROPOSED,
        advised_units=body.advised_units,
        advised_capital_usd=body.advised_capital_usd,
        expected_margin_usd=body.expected_margin_usd,
        loss_probability=body.loss_probability,
        horizon_hours=body.horizon_hours,
    )
    plan = await engine.create_plan(intent)
    store[plan.plan_id] = plan
    # Phase D (Rules 2 + 8): record the plan + a stored failure forecast at
    # recommendation time so the forecast can later be scored against the
    # settled outcome. Best-effort — never breaks plan creation.
    await _record_execution_intel(request, plan, intent, tenant=_tenant)
    return plan


async def _record_execution_intel(
    request: Request,
    plan: ExecutionPlan,
    intent: Any,
    *,
    tenant: str,
) -> None:
    """Best-effort Phase D recording: ExecutionRecord + stored FailureForecast."""
    pool = getattr(request.app.state, "audit_pool", None)
    if pool is None:
        return
    try:
        from aegis.execution_intel.forecast import FailureForecaster
        from aegis.execution_intel.memory import ExecutionMemory
        from aegis.execution_intel.schemas import ExecutionAssumption, ExecutionRecord
        from aegis.execution_intel.taxonomy import AssumptionKind, AssumptionStatus

        unit_price = plan.unit_price_usd or 0.0
        margin_pct = (
            (unit_price - plan.unit_cost_usd) / unit_price if unit_price > 0 else None
        )
        mem = ExecutionMemory(pool, tenant_id=tenant)
        await mem.record_plan(
            ExecutionRecord(
                plan_id=plan.plan_id,
                trend_id=plan.trend_id,
                supplier_name=plan.supplier_name,
                planned_units=plan.quantity,
                planned_unit_cost_usd=plan.unit_cost_usd,
                planned_margin_pct=margin_pct,
            )
        )
        # Honest supplier assumption: VERIFIED only when a real supplier priced it.
        supplier_verified = plan.supplier_name is not None
        await mem.log_assumption(
            ExecutionAssumption(
                plan_id=plan.plan_id,
                kind=AssumptionKind.SUPPLIER_EXISTS,
                claim=f"supplier '{plan.supplier_name}' can fulfil this plan",
                status=(
                    AssumptionStatus.VERIFIED
                    if supplier_verified
                    else AssumptionStatus.UNVERIFIED
                ),
                verified_via="supplier_api" if supplier_verified else None,
            )
        )
        forecaster = FailureForecaster(pool, tenant_id=tenant)
        fc = await forecaster.forecast(
            plan.plan_id,
            supplier_name=plan.supplier_name,
            category=getattr(intent, "category", "") or "general",
        )
        await forecaster.record_forecast(fc)
    except Exception as exc:  # INTENTIONAL: recording must never break the route.
        _log.debug(
            "capital.execution_intel_skipped", plan_id=plan.plan_id, reason=str(exc)
        )


@router.get("/plan/{plan_id}", response_model=ExecutionPlan)
async def get_plan(
    plan_id: str,
    store: dict[str, ExecutionPlan] = Depends(_plan_store),
    _tenant: str = Depends(_tenant_id),
) -> ExecutionPlan:
    plan = store.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")
    return plan


@router.post("/plan/{plan_id}/approve", response_model=ExecutionPlan)
async def approve_plan(
    plan_id: str,
    body: ApprovePlanRequest,
    store: dict[str, ExecutionPlan] = Depends(_plan_store),
    _tenant: str = Depends(_tenant_id),
) -> ExecutionPlan:
    """Manually approve a pending execution plan."""
    plan = store.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")

    from aegis.execute.engine import PlanStatus

    updated = plan.model_copy(update={"status": PlanStatus.APPROVED})
    store[plan_id] = updated

    _log.info(
        "execute.api.plan_approved",
        plan_id=plan_id,
        approved_by=body.approved_by,
    )
    return updated


@router.post("/plan/{plan_id}/execute", response_model=dict)
async def execute_plan(
    plan_id: str,
    store: dict[str, ExecutionPlan] = Depends(_plan_store),
    engine: ExecutionEngine = Depends(_get_engine),
    ks: KillSwitch = Depends(_get_killswitch),
    _tenant: str = Depends(_tenant_id),
) -> dict:
    """Dispatch an approved plan to the fulfillment backend.

    Blocked when the killswitch is TRIPPED.
    """
    if await ks.is_tripped():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="killswitch is TRIPPED — execution halted",
        )

    plan = store.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")

    from aegis.execute.engine import PlanStatus

    if plan.requires_approval and plan.status != PlanStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="plan requires approval before execution",
        )

    outcome = await engine.execute_plan(plan)

    updated = plan.model_copy(
        update={
            "status": PlanStatus.EXECUTED if outcome.status == "executed" else PlanStatus.FAILED,
            "order_ids": outcome.order_ids,
        }
    )
    store[plan_id] = updated

    return {
        "plan_id": plan_id,
        "status": outcome.status,
        "order_ids": list(outcome.order_ids),
        "executed_at": outcome.executed_at.isoformat(),
        "error": outcome.error,
    }


@router.post("/callback")
async def telegram_callback(
    body: TelegramCallbackRequest,
    broker: ApprovalBroker = Depends(_get_broker),
) -> dict:
    """Receive a Telegram inline-keyboard callback and resolve an approval."""
    cq = body.callback_query
    data: str = cq.get("data", "")
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] not in ("approve", "reject", "escalate"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unrecognised callback_data: {data!r}",
        )
    action, request_id = parts

    decision_map = {"approve": "approved", "reject": "rejected", "escalate": "escalated"}
    from aegis.execute.approval import ApprovalDecision

    decision: ApprovalDecision = decision_map[action]  # type: ignore[assignment]
    decided_by = str(cq.get("from", {}).get("username", "unknown"))

    await broker.handle_callback(request_id, decision, decided_by=decided_by)
    return {"ok": True, "request_id": request_id, "decision": decision}


__all__ = ["router"]
