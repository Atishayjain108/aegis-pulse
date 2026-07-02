"""Unit tests for Phase 6 Capital Execution Engine."""

from __future__ import annotations

import pytest

from aegis.execute.config import ExecuteSettings
from aegis.execute.constants import MODE_ADVISORY, MODE_LIVE
from aegis.execute.engine import (
    ExecutionEngine,
    ExecutionPlan,
    FulfillmentMethod,
    _compute_risk,
    _derive_unit_cost,
    _derive_unit_price,
    _select_fulfillment,
)
from aegis.execute.schemas.intent import ExecutionIntent, IntentKind, IntentStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _settings(**overrides) -> ExecuteSettings:
    defaults = {
        "mode": MODE_ADVISORY,
        "capital_max_risk_usd": 500.0,
        "capital_daily_loss_limit_usd": 1000.0,
        "capital_kelly_fraction": 0.25,
        "approval_timeout_s": 5,
        "auto_execute_p0": False,
        # REALITY-FIRST: non-advisory modes now require a complete recipient.
        "fulfillment_recipient_name": "Test Operator",
        "fulfillment_recipient_address1": "1 Test St",
        "fulfillment_recipient_city": "Testville",
        "fulfillment_recipient_zip": "00001",
        "fulfillment_recipient_country": "US",
        "fulfillment_recipient_phone": "15555550100",
    }
    defaults.update(overrides)
    return ExecuteSettings(**defaults)


def _intent(**overrides) -> ExecutionIntent:
    defaults = {
        "intent_id": "test-intent-001-xxxx",
        "alert_id": "test-alert-001-xxxx",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "trend_id": "trend-123",
        "kind": IntentKind.ENTER_POSITION,
        "status": IntentStatus.PROPOSED,
        "advised_units": 10,
        "advised_capital_usd": 100.0,
        "expected_margin_usd": 10.0,
        "loss_probability": 0.20,
        "horizon_hours": 24,
    }
    defaults.update(overrides)
    return ExecutionIntent(**defaults)


# ---------------------------------------------------------------------------
# ExecutionEngine.create_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_plan_returns_execution_plan():
    engine = ExecutionEngine(_settings())
    intent = _intent()
    plan = await engine.create_plan(intent)

    assert isinstance(plan, ExecutionPlan)
    assert plan.trend_id == intent.trend_id
    assert plan.execution_mode == MODE_ADVISORY


@pytest.mark.asyncio
async def test_create_plan_kelly_fraction_bounded():
    engine = ExecutionEngine(_settings(capital_kelly_fraction=0.25))
    plan = await engine.create_plan(_intent(loss_probability=0.10))

    # Fractional Kelly with 0.25× should never exceed 25% of optimal.
    assert plan.kelly_fraction_used <= 0.25 + 1e-9


@pytest.mark.asyncio
async def test_create_plan_capital_capped_by_max_risk():
    engine = ExecutionEngine(_settings(capital_max_risk_usd=50.0))
    plan = await engine.create_plan(_intent(expected_margin_usd=5.0, loss_probability=0.05))
    assert plan.total_capital_usd <= 50.0 + 1e-6


@pytest.mark.asyncio
async def test_create_plan_requires_approval_for_enter():
    engine = ExecutionEngine(_settings(auto_execute_p0=False))
    plan = await engine.create_plan(_intent(kind=IntentKind.ENTER_POSITION))
    assert plan.requires_approval is True


@pytest.mark.asyncio
async def test_create_plan_no_approval_when_auto_execute():
    engine = ExecutionEngine(_settings(auto_execute_p0=True))
    plan = await engine.create_plan(_intent(kind=IntentKind.ENTER_POSITION))
    assert plan.requires_approval is False


@pytest.mark.asyncio
async def test_create_plan_risk_score_in_bounds():
    engine = ExecutionEngine(_settings())
    for lp in (0.0, 0.1, 0.5, 0.9, 1.0):
        plan = await engine.create_plan(_intent(loss_probability=lp))
        assert 0.0 <= plan.risk_score <= 1.0, f"risk_score out of bounds for lp={lp}"


@pytest.mark.asyncio
async def test_create_plan_advisory_has_no_supplier_name():
    # Reality First: advisory mode verifies no supplier, so supplier_name is None.
    engine = ExecutionEngine(_settings(mode=MODE_ADVISORY))
    plan = await engine.create_plan(_intent())
    assert plan.supplier_name is None


@pytest.mark.asyncio
async def test_create_plan_records_verified_supplier_name(monkeypatch):
    # Phase D: a verified supplier name is propagated onto the plan in live mode.
    engine = ExecutionEngine(_settings(mode=MODE_LIVE))

    async def _fake_verify(_self, _intent):
        return 4.25, "printful"

    # ExecutionEngine uses __slots__; patch the class, not the instance.
    monkeypatch.setattr(ExecutionEngine, "_get_verified_unit_cost", _fake_verify)
    plan = await engine.create_plan(_intent())
    assert plan.supplier_name == "printful"
    assert plan.unit_cost_usd == 4.25


# ---------------------------------------------------------------------------
# ExecutionEngine.execute_plan — advisory mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_plan_advisory_mode_noop():
    engine = ExecutionEngine(_settings(mode=MODE_ADVISORY))
    plan = await engine.create_plan(_intent())
    outcome = await engine.execute_plan(plan)

    assert outcome.status == "advisory_mode"
    assert outcome.order_ids == ()


@pytest.mark.asyncio
async def test_execute_plan_halted_on_drawdown():
    engine = ExecutionEngine(_settings(mode=MODE_LIVE, capital_daily_loss_limit_usd=10.0))
    engine.record_settlement(-50.0)  # blow past limit

    plan = await engine.create_plan(_intent())
    outcome = await engine.execute_plan(plan)
    assert outcome.status == "halted_drawdown"


# ---------------------------------------------------------------------------
# Daily PnL tracking
# ---------------------------------------------------------------------------


def test_record_settlement_accumulates():
    engine = ExecutionEngine(_settings())
    engine.record_settlement(10.0)
    engine.record_settlement(-5.0)
    assert engine.daily_pnl == pytest.approx(5.0)


def test_reset_daily_pnl():
    engine = ExecutionEngine(_settings())
    engine.record_settlement(100.0)
    engine.reset_daily_pnl()
    assert engine.daily_pnl == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_derive_unit_cost_from_margin():
    # margin=10 → cost≈20 (50% margin product assumption)
    cost = _derive_unit_cost(_intent(expected_margin_usd=10.0))
    assert cost == pytest.approx(20.0)


def test_derive_unit_cost_fallback():
    cost = _derive_unit_cost(_intent(expected_margin_usd=None))
    assert cost == pytest.approx(10.0)


def test_derive_unit_price_from_margin():
    intent = _intent(expected_margin_usd=10.0)
    cost = _derive_unit_cost(intent)
    price = _derive_unit_price(intent, cost)
    assert price == pytest.approx(30.0)


def test_select_fulfillment_pod_for_small_qty():
    assert _select_fulfillment(3, _intent()) == FulfillmentMethod.POD


def test_select_fulfillment_dropship_for_mid_qty():
    assert _select_fulfillment(15, _intent()) == FulfillmentMethod.DROPSHIP


def test_select_fulfillment_inventory_for_large_qty():
    assert _select_fulfillment(50, _intent()) == FulfillmentMethod.INVENTORY


def test_compute_risk_bounded():
    for lp in (0.0, 0.25, 0.5):
        r = _compute_risk(_intent(loss_probability=lp), FulfillmentMethod.POD)
        assert 0.0 <= r <= 1.0


def test_compute_risk_inventory_higher_than_pod():
    intent = _intent(loss_probability=0.2)
    r_pod = _compute_risk(intent, FulfillmentMethod.POD)
    r_inv = _compute_risk(intent, FulfillmentMethod.INVENTORY)
    assert r_inv > r_pod
