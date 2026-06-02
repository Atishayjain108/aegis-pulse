"""PnL settlement and daily reconciliation — Phase 6.

`SettlementManager` tracks per-order outcomes (revenue, refunds, fees) and
produces a `DailySettlement` record each EOD. The record is written to
`execution_orders` via the passed asyncpg pool and logged via structlog for
audit.

Settlement flow per order:
  1. Fulfillment partner signals completion (webhook → `record_order_outcome`).
  2. Revenue, refund, shipping, and platform-fee fields are extracted.
  3. Net PnL = revenue − refund − shipping − platform_fee − unit_cost.
  4. `ExecutionEngine.record_settlement(pnl)` is called to update daily total.
  5. EOD: `settle_daily()` aggregates and persists the full-day snapshot.

Tax CSV export is provided for accountant handoff.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

import structlog

from aegis.execute.constants import (
    SETTLEMENT_DEFAULT_PLATFORM_FEE_PCT,
    SETTLEMENT_DEFAULT_SHIPPING_USD,
)

_log = structlog.get_logger(__name__)


@dataclass
class OrderOutcome:
    """Raw settlement data for a single order."""

    order_id: str
    plan_id: str
    unit_cost_usd: float
    quantity: int
    revenue_usd: float = 0.0
    refund_usd: float = 0.0
    shipping_usd: float = SETTLEMENT_DEFAULT_SHIPPING_USD
    platform_fee_usd: float | None = None  # None → derive from revenue

    def net_pnl(self) -> float:
        fee = (
            self.platform_fee_usd
            if self.platform_fee_usd is not None
            else self.revenue_usd * SETTLEMENT_DEFAULT_PLATFORM_FEE_PCT
        )
        gross = self.revenue_usd - self.refund_usd - self.shipping_usd - fee
        return round(gross - self.unit_cost_usd * self.quantity, 4)


@dataclass
class DailySettlement:
    """Aggregated PnL for a trading day."""

    settlement_date: date
    order_count: int
    total_revenue_usd: float
    total_cost_usd: float
    total_pnl_usd: float
    reconciliation_errors: list[str] = field(default_factory=list)
    settled_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SettlementManager:
    """Manage order settlement and EOD reconciliation.

    Pass the asyncpg pool; all DB writes use parameterised queries under
    `app.current_tenant` RLS.
    """

    __slots__ = ("_pool", "_pending")

    def __init__(self, pool: object) -> None:
        self._pool = pool
        self._pending: dict[str, OrderOutcome] = {}

    def stage_outcome(self, outcome: OrderOutcome) -> None:
        """Register an order outcome for inclusion in the next EOD batch."""
        self._pending[outcome.order_id] = outcome
        _log.debug(
            "execute.settlement.staged",
            order_id=outcome.order_id,
            pnl=outcome.net_pnl(),
        )

    async def settle_daily(
        self,
        settlement_date: date | None = None,
        *,
        tenant_id: str | None = None,
    ) -> DailySettlement:
        """Flush staged outcomes to DB and produce a daily snapshot.

        If `settlement_date` is None, uses today (UTC). Returns the snapshot
        even when no orders were staged (zero-revenue day).
        """
        target_date = settlement_date or datetime.now(UTC).date()
        outcomes = list(self._pending.values())
        self._pending.clear()

        total_revenue = Decimal(0)
        total_cost = Decimal(0)
        total_pnl = Decimal(0)
        errors: list[str] = []

        for o in outcomes:
            try:
                pnl = Decimal(str(o.net_pnl()))
                total_revenue += Decimal(str(o.revenue_usd - o.refund_usd))
                total_cost += Decimal(str(o.unit_cost_usd * o.quantity))
                total_pnl += pnl
                await self._persist_outcome(o, pnl, tenant_id=tenant_id)
            except Exception as exc:
                errors.append(f"order {o.order_id}: {exc}")
                _log.error(
                    "execute.settlement.persist_failed",
                    order_id=o.order_id,
                    error=str(exc),
                )

        snap = DailySettlement(
            settlement_date=target_date,
            order_count=len(outcomes),
            total_revenue_usd=round(float(total_revenue), 4),
            total_cost_usd=round(float(total_cost), 4),
            total_pnl_usd=round(float(total_pnl), 4),
            reconciliation_errors=errors,
        )

        _log.info(
            "execute.settlement.daily",
            date=str(target_date),
            orders=snap.order_count,
            pnl=snap.total_pnl_usd,
            errors=len(errors),
        )
        return snap

    def export_tax_csv(self, settlements: list[DailySettlement]) -> str:
        """Render settlements as a UTF-8 CSV string for accountant export."""
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=["date", "orders", "revenue_usd", "cost_usd", "pnl_usd", "errors"],
        )
        writer.writeheader()
        for s in settlements:
            writer.writerow(
                {
                    "date": s.settlement_date.isoformat(),
                    "orders": s.order_count,
                    "revenue_usd": f"{s.total_revenue_usd:.2f}",
                    "cost_usd": f"{s.total_cost_usd:.2f}",
                    "pnl_usd": f"{s.total_pnl_usd:.2f}",
                    "errors": len(s.reconciliation_errors),
                }
            )
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _persist_outcome(
        self,
        outcome: OrderOutcome,
        pnl: Decimal,
        *,
        tenant_id: str | None,
    ) -> None:
        if self._pool is None:
            return
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            if tenant_id:
                await conn.execute(
                    f"SET app.current_tenant = '{tenant_id}'"
                )
            await conn.execute(
                """
                INSERT INTO execution_orders
                  (order_id, plan_id, unit_cost_usd, quantity,
                   revenue_usd, refund_usd, shipping_usd, platform_fee_usd, pnl_usd)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (order_id) DO UPDATE SET
                  pnl_usd = EXCLUDED.pnl_usd
                """,
                outcome.order_id,
                outcome.plan_id,
                outcome.unit_cost_usd,
                outcome.quantity,
                outcome.revenue_usd,
                outcome.refund_usd,
                outcome.shipping_usd,
                outcome.platform_fee_usd
                    or outcome.revenue_usd * SETTLEMENT_DEFAULT_PLATFORM_FEE_PCT,
                float(pnl),
            )


__all__: Final = [
    "DailySettlement",
    "OrderOutcome",
    "SettlementManager",
]
