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
  6. PASS3-3A: each persisted order is bridged to Phase 9 as a `TradeOutcome`
     ground-truth label (best-effort — settlement never fails because the
     evolution layer is unavailable).

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

        DORMANT-UNTIL-REAL-EXECUTION (PROJECT OMEGA): this is the entry point of
        the *capital* outcome loop. It has **no scheduled caller by design** —
        AEGIS runs in advisory mode and places no real orders, so there are no
        settled PnL rows to flush, and ``prediction_outcomes`` stays empty. The
        downstream consumers (RetrainingPipeline, FailureForecaster, buyer/
        supplier trust, drawdown breaker) are therefore DORMANT, not broken.
        Wiring this into a scheduled EOD job + the fulfillment webhook is gated
        on a real live-mode integration test (see forensic.md, Remediation Pass
        2026-06-17). The capital-free signal-outcome loop (Phase A,
        ``job_settle_claims``) is the live feedback path today.
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
                # PASS3-3A: bridge settled order → Phase 9 ground truth.
                await self._record_outcome_for_evolution(o, pnl, tenant_id=tenant_id)
                # Phase D (S1/S2): bridge settled order → Execution Intelligence.
                await self._record_execution_intel(o, pnl, tenant_id=tenant_id)
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

    async def _record_outcome_for_evolution(
        self,
        outcome: OrderOutcome,
        pnl: Decimal,
        *,
        tenant_id: str | None,
    ) -> None:
        """PASS3-3A: bridge Phase 6 settlement to Phase 9 evolution.

        Builds a `TradeOutcome` ground-truth label from the settled order and
        records it via `OutcomeRecorder`, then publishes an `outcome_recorded`
        event onto the evolve stream. Best-effort end to end: settlement must
        never fail because the evolution layer is missing or unhealthy.

        Prediction score/confidence are not stored on `execution_plans`; they
        are resolved via plan → intent → alert. Missing links degrade to a
        neutral 0.5 — the label (ROI/PnL) is the valuable part.
        """
        if self._pool is None:
            return
        try:
            from aegis.evolve.outcomes import OutcomeRecorder
            from aegis.evolve.schemas import TradeOutcome

            trend_id, score, confidence = await self._fetch_plan_context(
                outcome.plan_id, tenant_id=tenant_id
            )

            cost = Decimal(str(outcome.unit_cost_usd)) * outcome.quantity
            roi_pct = float(pnl / max(cost, Decimal("0.01"))) * 100.0

            if pnl > 0:
                status = "successful"
            elif outcome.refund_usd >= outcome.revenue_usd and outcome.refund_usd > 0:
                status = "full_refund"
            elif outcome.refund_usd > 0:
                status = "partial_refund"
            else:
                # Unprofitable, no refund — must be a value allowed by the
                # prediction_outcomes CHECK constraint ("failed" is not).
                status = "dispute"

            trade = TradeOutcome(
                execution_plan_id=outcome.plan_id,
                trend_id=trend_id,
                prediction_score=score,
                prediction_confidence=confidence,
                actual_roi_pct=Decimal(str(round(roi_pct, 4))),
                pnl_usd=pnl,
                units_sold=outcome.quantity,
                total_cost=cost,
                shipping_cost=Decimal(str(outcome.shipping_usd)),
                settlement_timestamp=datetime.now(UTC),
                resolution_status=status,
            )

            recorder = (
                OutcomeRecorder(self._pool, tenant_id=tenant_id)  # type: ignore[arg-type]
                if tenant_id
                else OutcomeRecorder(self._pool)  # type: ignore[arg-type]
            )
            recorded = await recorder.record_outcome(trade)

            from aegis.core.event_bus import STREAM_EVOLVE, publish_event

            await publish_event(
                STREAM_EVOLVE,
                {
                    "event": "outcome_recorded",
                    "plan_id": outcome.plan_id,
                    "order_id": outcome.order_id,
                    "trend_id": trend_id,
                    "roi_pct": round(roi_pct, 4),
                    "pnl_usd": round(float(pnl), 2),
                    "status": status,
                    "recorded": recorded,
                },
            )
        except Exception as exc:
            # INTENTIONAL: settlement succeeds regardless of evolution recording.
            _log.debug(
                "execute.settlement.evolution_record_skipped",
                order_id=outcome.order_id,
                plan_id=outcome.plan_id,
                reason=str(exc),
            )

    async def _record_execution_intel(
        self,
        outcome: OrderOutcome,
        pnl: Decimal,
        *,
        tenant_id: str | None,
    ) -> None:
        """Phase D: bridge a settled order → Execution Knowledge Engine (S1/S2).

        Builds an ``ExecutionRecord`` from the settled order (real ground truth)
        and, when the supplier is known, increments its measured fulfillment
        reliability. Best-effort: settlement never fails because the execution
        intelligence layer is missing or unhealthy.
        """
        if self._pool is None:
            return
        try:
            from aegis.execution_intel.memory import ExecutionMemory
            from aegis.execution_intel.supplier import SupplierIntel
            from aegis.execution_intel.taxonomy import (
                ExecutionFailureCategory,
                ExecutionOutcome,
            )

            trend_id, _, _ = await self._fetch_plan_context(
                outcome.plan_id, tenant_id=tenant_id
            )

            cost = float(outcome.unit_cost_usd) * outcome.quantity
            cancelled = (
                outcome.refund_usd >= outcome.revenue_usd and outcome.refund_usd > 0
            )
            succeeded = pnl > 0

            if cancelled:
                exec_outcome = ExecutionOutcome.CANCELLED
                failure_cat = ExecutionFailureCategory.PAYMENT_ISSUE
            elif succeeded:
                exec_outcome = ExecutionOutcome.SUCCEEDED
                failure_cat = ExecutionFailureCategory.NONE
            else:
                exec_outcome = ExecutionOutcome.FAILED
                failure_cat = ExecutionFailureCategory.MARGIN_EVAPORATED

            supplier_name = outcome.__dict__.get("supplier_name") or None
            tid = tenant_id or "00000000-0000-0000-0000-000000000001"

            mem = ExecutionMemory(self._pool, tenant_id=tid)
            await mem.upsert_settled(
                outcome.plan_id,
                exec_outcome,
                trend_id=trend_id,
                supplier_name=supplier_name,
                realized_units=outcome.quantity,
                realized_pnl_usd=round(float(pnl), 4),
                realized_cost_usd=round(cost, 4),
                failure_category=failure_cat,
                failure_detail="" if succeeded else "unprofitable settled order",
            )

            if supplier_name:
                intel = SupplierIntel(self._pool, tenant_id=tid)
                await intel.record_fulfillment(
                    supplier_name,
                    succeeded=succeeded and not cancelled,
                    cancelled=cancelled,
                )
        except Exception as exc:
            # INTENTIONAL: settlement succeeds regardless of execution-intel recording.
            _log.debug(
                "execute.settlement.execution_intel_skipped",
                order_id=outcome.order_id,
                plan_id=outcome.plan_id,
                reason=str(exc),
            )

    async def _fetch_plan_context(
        self,
        plan_id: str,
        *,
        tenant_id: str | None,
    ) -> tuple[str, float, float]:
        """Resolve (trend_id, score, confidence) for a plan via its alert.

        Falls back to ("unknown", 0.5, 0.5) when any link is missing.
        """
        trend_id, score, confidence = "unknown", 0.5, 0.5
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                if tenant_id:
                    await conn.execute(
                        "SELECT set_config('app.current_tenant', $1, false)",
                        tenant_id,
                    )
                row = await conn.fetchrow(
                    """
                    SELECT p.trend_id, a.score, a.confidence
                    FROM execution_plans p
                    LEFT JOIN execution_intents i ON i.intent_id = p.intent_id
                    LEFT JOIN alerts a ON a.alert_id = i.alert_id
                    WHERE p.plan_id = $1::uuid
                    """,
                    plan_id,
                )
            if row:
                trend_id = str(row["trend_id"] or "unknown")
                if row["score"] is not None:
                    score = float(row["score"])
                if row["confidence"] is not None:
                    confidence = float(row["confidence"])
        except Exception as exc:
            _log.debug(
                "execute.settlement.plan_context_fallback",
                plan_id=plan_id,
                reason=str(exc),
            )
        return trend_id, score, confidence

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
                # audit P2-4: parameterized set_config (not f-string SET) — this
                # value drives RLS, so string interpolation was a cross-tenant
                # injection vector. Matches the safe idiom used everywhere else.
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", tenant_id
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
