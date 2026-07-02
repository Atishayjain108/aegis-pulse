"""Pass 10 — SettlementManager EOD reconciliation + outcome math.

Covers the PnL/ROI computation and daily snapshot accounting that feed the
evolution outcome label. Pure arithmetic + staging behavior; no infra.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.execute.settlement import OrderOutcome, SettlementManager


def _pool():
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"trend_id": "t", "score": 0.5, "confidence": 0.5})
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def test_net_pnl_positive() -> None:
    """Happy path: revenue exceeds all costs → positive net PnL."""
    o = OrderOutcome(
        order_id="o1", plan_id="p1", unit_cost_usd=10.0, quantity=2,
        revenue_usd=40.0, refund_usd=0.0, shipping_usd=5.0, platform_fee_usd=1.0,
    )
    # (40 - 0 - 5 - 1) - 10*2 = 14
    assert o.net_pnl() == pytest.approx(14.0)


def test_net_pnl_negative_with_refund() -> None:
    """Edge case: a full refund drives net PnL deeply negative."""
    o = OrderOutcome(
        order_id="o2", plan_id="p1", unit_cost_usd=50.0, quantity=1,
        revenue_usd=30.0, refund_usd=30.0, shipping_usd=5.0, platform_fee_usd=0.9,
    )
    assert o.net_pnl() < 0


async def test_daily_snapshot_aggregates() -> None:
    """Invariant: snapshot order_count and PnL reflect all staged orders."""
    mgr = SettlementManager(_pool())
    mgr.stage_outcome(OrderOutcome(
        order_id="a", plan_id="p", unit_cost_usd=5.0, quantity=1,
        revenue_usd=20.0, refund_usd=0.0, shipping_usd=2.0, platform_fee_usd=1.0,
    ))
    mgr.stage_outcome(OrderOutcome(
        order_id="b", plan_id="p", unit_cost_usd=5.0, quantity=1,
        revenue_usd=20.0, refund_usd=0.0, shipping_usd=2.0, platform_fee_usd=1.0,
    ))
    with patch("aegis.evolve.outcomes.OutcomeRecorder", return_value=AsyncMock()), \
         patch("aegis.core.event_bus.publish_event", new=AsyncMock()):
        snap = await mgr.settle_daily(tenant_id="t-1")
    assert snap.order_count == 2
    assert snap.total_pnl_usd == pytest.approx(24.0)


async def test_staging_clears_after_settle() -> None:
    """Settling flushes the pending buffer — re-settling yields zero orders."""
    mgr = SettlementManager(_pool())
    mgr.stage_outcome(OrderOutcome(
        order_id="a", plan_id="p", unit_cost_usd=5.0, quantity=1,
        revenue_usd=20.0, refund_usd=0.0, shipping_usd=2.0, platform_fee_usd=1.0,
    ))
    with patch("aegis.evolve.outcomes.OutcomeRecorder", return_value=AsyncMock()), \
         patch("aegis.core.event_bus.publish_event", new=AsyncMock()):
        await mgr.settle_daily(tenant_id="t-1")
        snap2 = await mgr.settle_daily(tenant_id="t-1")
    assert snap2.order_count == 0


async def test_persist_failure_recorded_not_raised() -> None:
    """Failure path: a DB persist error is captured in reconciliation_errors."""
    conn = AsyncMock()
    conn.execute = AsyncMock(side_effect=RuntimeError("db write failed"))
    conn.fetchrow = AsyncMock(return_value=None)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)

    mgr = SettlementManager(pool)
    mgr.stage_outcome(OrderOutcome(
        order_id="a", plan_id="p", unit_cost_usd=5.0, quantity=1,
        revenue_usd=20.0, refund_usd=0.0, shipping_usd=2.0, platform_fee_usd=1.0,
    ))
    snap = await mgr.settle_daily(tenant_id="t-1")
    assert snap.reconciliation_errors
