"""PASS3-3A — settlement → Phase 9 OutcomeRecorder bridge tests.

Contract: every settled order is recorded as a TradeOutcome ground-truth
label and announced on the evolve stream, and settlement NEVER fails
because the evolution layer is missing or unhealthy.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.execute.settlement import OrderOutcome, SettlementManager


def _make_pool(plan_row: dict | None = None, fetchrow_error: Exception | None = None):
    conn = AsyncMock()
    conn.execute = AsyncMock()
    if fetchrow_error is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_error)
    else:
        conn.fetchrow = AsyncMock(return_value=plan_row)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _profitable_order(order_id: str = "ord-1") -> OrderOutcome:
    # net = (40 - 0 - 5 - 1) - 10*2 = 14 > 0
    return OrderOutcome(
        order_id=order_id,
        plan_id="11111111-2222-3333-4444-555555555555",
        unit_cost_usd=10.0,
        quantity=2,
        revenue_usd=40.0,
        refund_usd=0.0,
        shipping_usd=5.0,
        platform_fee_usd=1.0,
    )


def _losing_order(refund: float = 0.0) -> OrderOutcome:
    # net = (30 - refund - 5 - 0.9) - 50 < 0
    return OrderOutcome(
        order_id="ord-loss",
        plan_id="11111111-2222-3333-4444-555555555555",
        unit_cost_usd=50.0,
        quantity=1,
        revenue_usd=30.0,
        refund_usd=refund,
        shipping_usd=5.0,
        platform_fee_usd=0.9,
    )


_PLAN_ROW = {"trend_id": "trend-x", "score": 0.8, "confidence": 0.9}


class TestEvolutionBridge:
    @pytest.mark.asyncio
    async def test_positive_pnl_records_successful_outcome(self) -> None:
        pool, _conn = _make_pool(plan_row=_PLAN_ROW)
        mgr = SettlementManager(pool)
        mgr.stage_outcome(_profitable_order())

        with (
            patch(
                "aegis.evolve.outcomes.OutcomeRecorder.record_outcome",
                new_callable=AsyncMock,
                return_value=True,
            ) as record,
            patch("aegis.core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            snap = await mgr.settle_daily(tenant_id="00000000-0000-0000-0000-000000000001")

        assert snap.reconciliation_errors == []
        record.assert_awaited_once()
        trade = record.await_args[0][0]
        assert trade.resolution_status == "successful"
        assert float(trade.actual_roi_pct) > 0
        assert trade.execution_plan_id == "11111111-2222-3333-4444-555555555555"
        assert trade.trend_id == "trend-x"
        assert trade.prediction_score == pytest.approx(0.8)
        assert trade.prediction_confidence == pytest.approx(0.9)
        assert trade.units_sold == 2

    @pytest.mark.asyncio
    async def test_negative_pnl_no_refund_records_dispute(self) -> None:
        """'failed' is not allowed by the prediction_outcomes CHECK — losses
        with no refund map to 'dispute'."""
        pool, _conn = _make_pool(plan_row=_PLAN_ROW)
        mgr = SettlementManager(pool)
        mgr.stage_outcome(_losing_order(refund=0.0))

        with (
            patch(
                "aegis.evolve.outcomes.OutcomeRecorder.record_outcome",
                new_callable=AsyncMock,
                return_value=True,
            ) as record,
            patch("aegis.core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            await mgr.settle_daily()

        trade = record.await_args[0][0]
        assert trade.resolution_status == "dispute"
        assert float(trade.actual_roi_pct) < 0

    @pytest.mark.asyncio
    async def test_full_refund_status(self) -> None:
        pool, _conn = _make_pool(plan_row=_PLAN_ROW)
        mgr = SettlementManager(pool)
        mgr.stage_outcome(_losing_order(refund=30.0))  # refund == revenue

        with (
            patch(
                "aegis.evolve.outcomes.OutcomeRecorder.record_outcome",
                new_callable=AsyncMock,
                return_value=True,
            ) as record,
            patch("aegis.core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            await mgr.settle_daily()

        trade = record.await_args[0][0]
        assert trade.resolution_status == "full_refund"

    @pytest.mark.asyncio
    async def test_recorder_failure_settlement_still_succeeds(self) -> None:
        pool, _conn = _make_pool(plan_row=_PLAN_ROW)
        mgr = SettlementManager(pool)
        mgr.stage_outcome(_profitable_order())

        with patch(
            "aegis.evolve.outcomes.OutcomeRecorder.record_outcome",
            new_callable=AsyncMock,
            side_effect=RuntimeError("evolution layer down"),
        ):
            snap = await mgr.settle_daily()

        # Settlement is unaffected: order persisted, no reconciliation error.
        assert snap.order_count == 1
        assert snap.reconciliation_errors == []
        assert snap.total_pnl_usd == pytest.approx(14.0)

    @pytest.mark.asyncio
    async def test_event_published_after_recording(self) -> None:
        pool, _conn = _make_pool(plan_row=_PLAN_ROW)
        mgr = SettlementManager(pool)
        mgr.stage_outcome(_profitable_order())

        with (
            patch(
                "aegis.evolve.outcomes.OutcomeRecorder.record_outcome",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "aegis.core.event_bus.publish_event", new_callable=AsyncMock
            ) as publish,
        ):
            await mgr.settle_daily()

        publish.assert_awaited_once()
        stream, payload = publish.await_args[0][0], publish.await_args[0][1]
        assert stream == "aegis:phase9:evolve_events"
        assert payload["event"] == "outcome_recorded"
        assert payload["status"] == "successful"
        assert payload["trend_id"] == "trend-x"

    @pytest.mark.asyncio
    async def test_zero_cost_no_division_error(self) -> None:
        pool, _conn = _make_pool(plan_row=_PLAN_ROW)
        mgr = SettlementManager(pool)
        mgr.stage_outcome(
            OrderOutcome(
                order_id="ord-free",
                plan_id="11111111-2222-3333-4444-555555555555",
                unit_cost_usd=0.0,
                quantity=0,
                revenue_usd=10.0,
                refund_usd=0.0,
                shipping_usd=0.0,
                platform_fee_usd=0.0,
            )
        )

        with (
            patch(
                "aegis.evolve.outcomes.OutcomeRecorder.record_outcome",
                new_callable=AsyncMock,
                return_value=True,
            ) as record,
            patch("aegis.core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            snap = await mgr.settle_daily()

        assert snap.reconciliation_errors == []
        trade = record.await_args[0][0]
        # cost floored at 0.01 → roi finite
        assert float(trade.actual_roi_pct) > 0

    @pytest.mark.asyncio
    async def test_plan_context_fallback_on_db_error(self) -> None:
        pool, _conn = _make_pool(fetchrow_error=RuntimeError("join failed"))
        mgr = SettlementManager(pool)
        mgr.stage_outcome(_profitable_order())

        with (
            patch(
                "aegis.evolve.outcomes.OutcomeRecorder.record_outcome",
                new_callable=AsyncMock,
                return_value=True,
            ) as record,
            patch("aegis.core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            await mgr.settle_daily()

        trade = record.await_args[0][0]
        assert trade.trend_id == "unknown"
        assert trade.prediction_score == pytest.approx(0.5)
        assert trade.prediction_confidence == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_no_pool_skips_evolution_silently(self) -> None:
        mgr = SettlementManager(None)
        mgr.stage_outcome(_profitable_order())
        snap = await mgr.settle_daily()  # must not raise
        assert snap.order_count == 1
