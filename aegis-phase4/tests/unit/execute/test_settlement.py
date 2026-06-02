"""Unit tests for Phase 6 PnL settlement module."""

from __future__ import annotations

from datetime import date

import pytest

from aegis.execute.settlement import DailySettlement, OrderOutcome, SettlementManager

# ---------------------------------------------------------------------------
# OrderOutcome.net_pnl
# ---------------------------------------------------------------------------


def test_net_pnl_positive():
    o = OrderOutcome(
        order_id="ord-1",
        plan_id="plan-1",
        unit_cost_usd=10.0,
        quantity=2,
        revenue_usd=40.0,
        refund_usd=0.0,
        shipping_usd=5.0,
        platform_fee_usd=1.0,
    )
    # net = (40 - 0 - 5 - 1) - 10*2 = 34 - 20 = 14
    assert o.net_pnl() == pytest.approx(14.0)


def test_net_pnl_negative_on_high_cost():
    o = OrderOutcome(
        order_id="ord-2",
        plan_id="plan-2",
        unit_cost_usd=50.0,
        quantity=1,
        revenue_usd=30.0,
        refund_usd=0.0,
        shipping_usd=5.0,
        platform_fee_usd=0.9,
    )
    # net = (30 - 0 - 5 - 0.9) - 50*1 = 24.1 - 50 = -25.9
    assert o.net_pnl() < 0


def test_net_pnl_derives_platform_fee_when_none():
    o = OrderOutcome(
        order_id="ord-3",
        plan_id="plan-3",
        unit_cost_usd=10.0,
        quantity=1,
        revenue_usd=20.0,
        refund_usd=0.0,
        shipping_usd=5.0,
        platform_fee_usd=None,  # should derive 3% of revenue
    )
    expected_fee = 20.0 * 0.03
    expected_pnl = (20.0 - 0.0 - 5.0 - expected_fee) - 10.0 * 1
    assert o.net_pnl() == pytest.approx(expected_pnl, abs=1e-4)


def test_net_pnl_full_refund_is_negative():
    o = OrderOutcome(
        order_id="ord-4",
        plan_id="plan-4",
        unit_cost_usd=10.0,
        quantity=1,
        revenue_usd=20.0,
        refund_usd=20.0,  # full refund
        shipping_usd=5.0,
        platform_fee_usd=0.0,
    )
    # net = (20 - 20 - 5 - 0) - 10 = -15
    assert o.net_pnl() == pytest.approx(-15.0)


# ---------------------------------------------------------------------------
# SettlementManager.stage_outcome / settle_daily (no DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settle_daily_no_pool():
    mgr = SettlementManager(pool=None)
    o = OrderOutcome(
        order_id="ord-100",
        plan_id="plan-100",
        unit_cost_usd=10.0,
        quantity=2,
        revenue_usd=30.0,
        refund_usd=0.0,
        shipping_usd=5.0,
        platform_fee_usd=0.9,
    )
    mgr.stage_outcome(o)

    snap = await mgr.settle_daily(settlement_date=date(2026, 6, 1))

    assert isinstance(snap, DailySettlement)
    assert snap.order_count == 1
    assert snap.total_pnl_usd == pytest.approx(o.net_pnl(), abs=1e-4)
    assert snap.reconciliation_errors == []


@pytest.mark.asyncio
async def test_settle_daily_zero_orders():
    mgr = SettlementManager(pool=None)
    snap = await mgr.settle_daily(settlement_date=date(2026, 6, 1))
    assert snap.order_count == 0
    assert snap.total_revenue_usd == pytest.approx(0.0)
    assert snap.total_pnl_usd == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_settle_daily_clears_pending():
    mgr = SettlementManager(pool=None)
    mgr.stage_outcome(
        OrderOutcome("o1", "p1", 10.0, 1, revenue_usd=20.0, shipping_usd=2.0)
    )
    await mgr.settle_daily()
    # Pending should be cleared; second settle should show 0 orders.
    snap2 = await mgr.settle_daily()
    assert snap2.order_count == 0


# ---------------------------------------------------------------------------
# SettlementManager.export_tax_csv
# ---------------------------------------------------------------------------


def test_export_tax_csv_structure():
    mgr = SettlementManager(pool=None)
    settlements = [
        DailySettlement(
            settlement_date=date(2026, 6, 1),
            order_count=3,
            total_revenue_usd=90.0,
            total_cost_usd=60.0,
            total_pnl_usd=30.0,
        ),
        DailySettlement(
            settlement_date=date(2026, 6, 2),
            order_count=1,
            total_revenue_usd=25.0,
            total_cost_usd=15.0,
            total_pnl_usd=10.0,
        ),
    ]
    csv_text = mgr.export_tax_csv(settlements)
    lines = csv_text.strip().splitlines()
    assert lines[0] == "date,orders,revenue_usd,cost_usd,pnl_usd,errors"
    assert len(lines) == 3  # header + 2 rows
    assert "2026-06-01" in lines[1]
    assert "30.00" in lines[1]
