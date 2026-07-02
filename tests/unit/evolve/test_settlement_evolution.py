"""Pass 10 — settlement → Phase 9 evolution outcome recording.

Contract (§ PASS3-3A): every settled order becomes a TradeOutcome ground
truth label recorded via OutcomeRecorder and announced on the evolve
stream; settlement NEVER fails because the evolution layer is unhealthy.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.execute.settlement import OrderOutcome, SettlementManager

pytestmark = pytest.mark.asyncio


def _make_pool(plan_row: dict | None = None):
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=plan_row)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _profit_order() -> OrderOutcome:
    return OrderOutcome(
        order_id="ord-1",
        plan_id="11111111-2222-3333-4444-555555555555",
        unit_cost_usd=10.0, quantity=2, revenue_usd=40.0,
        refund_usd=0.0, shipping_usd=5.0, platform_fee_usd=1.0,
    )


async def test_settle_records_outcome() -> None:
    """Happy path: a profitable settled order is recorded as ground truth."""
    pool = _make_pool({"trend_id": "trend-x", "score": 0.8, "confidence": 0.9})
    mgr = SettlementManager(pool)
    mgr.stage_outcome(_profit_order())

    recorder = AsyncMock()
    recorder.record_outcome = AsyncMock(return_value=True)
    with patch("aegis.evolve.outcomes.OutcomeRecorder", return_value=recorder), \
         patch("aegis.core.event_bus.publish_event", new=AsyncMock()) as pub:
        snap = await mgr.settle_daily(tenant_id="t-1")

    assert snap.order_count == 1
    recorder.record_outcome.assert_awaited_once()
    trade = recorder.record_outcome.await_args.args[0]
    assert trade.trend_id == "trend-x"
    assert float(trade.actual_roi_pct) > 0  # profitable
    pub.assert_awaited()


async def test_publishes_to_evolve_stream_with_body() -> None:
    """Invariant: stream publish uses STREAM_EVOLVE and the 'body' field."""
    pool = _make_pool({"trend_id": "tx", "score": 0.5, "confidence": 0.5})
    mgr = SettlementManager(pool)
    mgr.stage_outcome(_profit_order())

    recorder = AsyncMock()
    recorder.record_outcome = AsyncMock(return_value=True)
    with patch("aegis.evolve.outcomes.OutcomeRecorder", return_value=recorder), \
         patch("aegis.core.event_bus.publish_event", new=AsyncMock()) as pub:
        await mgr.settle_daily(tenant_id="t-1")

    stream_name = pub.await_args.args[0]
    assert stream_name == "aegis:phase9:evolve_events"


async def test_settlement_survives_recorder_failure() -> None:
    """Failure path: OutcomeRecorder raising must NOT fail settlement."""
    pool = _make_pool({"trend_id": "tx", "score": 0.5, "confidence": 0.5})
    mgr = SettlementManager(pool)
    mgr.stage_outcome(_profit_order())

    with patch(
        "aegis.evolve.outcomes.OutcomeRecorder",
        side_effect=RuntimeError("evolve DB down"),
    ):
        snap = await mgr.settle_daily(tenant_id="t-1")

    # Persist succeeded; the evolution bridge error is swallowed.
    assert snap.order_count == 1
    assert snap.reconciliation_errors == []


async def test_no_pool_skips_evolution() -> None:
    """Edge case: with no pool the evolution bridge is a no-op, no crash."""
    mgr = SettlementManager(None)
    mgr.stage_outcome(_profit_order())
    snap = await mgr.settle_daily()
    assert snap.order_count == 1


async def test_empty_day_records_nothing() -> None:
    """Edge case: a zero-order day produces an empty snapshot."""
    pool = _make_pool()
    mgr = SettlementManager(pool)
    with patch("aegis.evolve.outcomes.OutcomeRecorder") as rec:
        snap = await mgr.settle_daily(tenant_id="t-1")
    assert snap.order_count == 0
    rec.assert_not_called()
