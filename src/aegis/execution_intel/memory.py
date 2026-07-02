"""
aegis.execution_intel.memory — Execution Knowledge Engine (Rule 2).

Durable memory linking an execution *plan* to its *outcome*, *cost*, *failure*
and the *assumptions* it rested on (Rule 1). Everything is best-effort: a write
failure is logged and swallowed — the live execution / settlement path must
never fail because the knowledge ledger is unavailable.

Reality First (Rule 1): ``record_plan`` always logs a ``supplier_exists``
assumption with status ``UNVERIFIED`` unless the caller passes evidence that a
real supplier was verified. ``record_outcome`` only ever fills outcome fields
from a settled order — it invents nothing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from aegis.execution_intel.schemas import (
    ExecutionAssumption,
    ExecutionRecord,
)
from aegis.execution_intel.taxonomy import (
    ExecutionFailureCategory,
    ExecutionOutcome,
)

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.execution_intel.memory")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class ExecutionMemory:
    """Record and recall execution plans, outcomes, and assumptions."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def _set_tenant(self, conn: Any) -> None:
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
        )

    async def record_plan(self, record: ExecutionRecord) -> bool:
        """Persist a recommended plan (idempotent on plan_id)."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                await conn.execute(
                    """
                    INSERT INTO execution_records (
                        plan_id, trend_id, opportunity_type, region, supplier_name,
                        planned_units, planned_unit_cost_usd, planned_margin_pct,
                        outcome, metadata, settlement_timestamp
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, NOW())
                    ON CONFLICT (tenant_id, plan_id, settlement_timestamp)
                        DO NOTHING
                    """,
                    record.plan_id,
                    record.trend_id,
                    record.opportunity_type,
                    record.region,
                    record.supplier_name,
                    record.planned_units,
                    record.planned_unit_cost_usd,
                    record.planned_margin_pct,
                    record.outcome.value,
                    json.dumps(record.metadata),
                )
            return True
        except Exception as exc:
            _log.error("execution_intel.record_plan_failed", error=str(exc))
            return False

    async def log_assumption(self, assumption: ExecutionAssumption) -> bool:
        """Persist one assumption a plan rests on (Rule 1)."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                await conn.execute(
                    """
                    INSERT INTO execution_assumptions (
                        plan_id, kind, claim, status, evidence, verified_via
                    ) VALUES ($1,$2,$3,$4,$5,$6)
                    """,
                    assumption.plan_id,
                    assumption.kind.value,
                    assumption.claim,
                    assumption.status.value,
                    json.dumps(assumption.evidence),
                    assumption.verified_via,
                )
            return True
        except Exception as exc:
            _log.error("execution_intel.log_assumption_failed", error=str(exc))
            return False

    async def record_outcome(
        self,
        plan_id: str,
        outcome: ExecutionOutcome,
        *,
        realized_units: int | None = None,
        realized_pnl_usd: float | None = None,
        realized_cost_usd: float | None = None,
        delay_hours: float | None = None,
        failure_category: ExecutionFailureCategory = ExecutionFailureCategory.NONE,
        failure_detail: str = "",
    ) -> bool:
        """Fill in the settled truth for a previously-recorded plan.

        Only updates an existing record — never fabricates one. The plan must
        have been recommended (``record_plan``) first.
        """
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                result = await conn.execute(
                    """
                    UPDATE execution_records SET
                        outcome = $2,
                        realized_units = $3,
                        realized_pnl_usd = $4,
                        realized_cost_usd = $5,
                        delay_hours = $6,
                        failure_category = $7,
                        failure_detail = $8,
                        settled_at = NOW()
                    WHERE plan_id = $1
                    """,
                    plan_id,
                    outcome.value,
                    realized_units,
                    realized_pnl_usd,
                    realized_cost_usd,
                    delay_hours,
                    failure_category.value,
                    failure_detail,
                )
            # asyncpg returns e.g. "UPDATE 1"; treat 0 rows as a miss.
            updated = result.endswith("1") if isinstance(result, str) else True
            if not updated:
                _log.warning("execution_intel.outcome_no_plan", plan_id=plan_id)
            return updated
        except Exception as exc:
            _log.error("execution_intel.record_outcome_failed", error=str(exc))
            return False

    async def upsert_settled(
        self,
        plan_id: str,
        outcome: ExecutionOutcome,
        *,
        trend_id: str = "unknown",
        supplier_name: str | None = None,
        realized_units: int | None = None,
        realized_pnl_usd: float | None = None,
        realized_cost_usd: float | None = None,
        delay_hours: float | None = None,
        failure_category: ExecutionFailureCategory = ExecutionFailureCategory.NONE,
        failure_detail: str = "",
    ) -> bool:
        """Create-or-update a record from a SETTLED order (Phase C doctrine).

        Used by the settlement hook: a real settled order is the ground truth,
        so the record may be born already-settled. Idempotent on plan_id.
        """
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                await conn.execute(
                    """
                    INSERT INTO execution_records (
                        plan_id, trend_id, supplier_name, outcome,
                        realized_units, realized_pnl_usd, realized_cost_usd,
                        delay_hours, failure_category, failure_detail,
                        settled_at, settlement_timestamp
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, NOW(), NOW())
                    ON CONFLICT (tenant_id, plan_id, settlement_timestamp)
                        DO UPDATE SET
                            outcome = EXCLUDED.outcome,
                            supplier_name = COALESCE(
                                EXCLUDED.supplier_name, execution_records.supplier_name
                            ),
                            realized_units = EXCLUDED.realized_units,
                            realized_pnl_usd = EXCLUDED.realized_pnl_usd,
                            realized_cost_usd = EXCLUDED.realized_cost_usd,
                            delay_hours = EXCLUDED.delay_hours,
                            failure_category = EXCLUDED.failure_category,
                            failure_detail = EXCLUDED.failure_detail,
                            settled_at = NOW()
                    """,
                    plan_id, trend_id, supplier_name, outcome.value,
                    realized_units, realized_pnl_usd, realized_cost_usd,
                    delay_hours, failure_category.value, failure_detail,
                )
            return True
        except Exception as exc:
            _log.error("execution_intel.upsert_settled_failed", error=str(exc))
            return False

    async def fetch_recent(
        self, *, limit: int = 50, outcome: ExecutionOutcome | None = None,
    ) -> list[ExecutionRecord]:
        """Return the most recent execution records (optionally by outcome)."""
        if self._pool is None:
            return []
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                if outcome is not None:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM execution_records
                        WHERE outcome = $1
                        ORDER BY created_at DESC LIMIT $2
                        """,
                        outcome.value, limit,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM execution_records "
                        "ORDER BY created_at DESC LIMIT $1",
                        limit,
                    )
            return [self._row_to_record(r) for r in rows]
        except Exception as exc:
            _log.error("execution_intel.fetch_recent_failed", error=str(exc))
            return []

    async def failure_base_rates(self) -> dict[str, float | int]:
        """Measured per-mode failure rates over settled records (Rule 8 input).

        Returns ``{"n_settled": int, "<failure_category>": rate, ...}`` where
        each rate is failures-of-that-category / settled-records. Empty (only
        ``n_settled=0``) when nothing has settled — the caller then abstains
        rather than inventing a base rate (Rule 1).
        """
        empty: dict[str, float | int] = {"n_settled": 0}
        if self._pool is None:
            return empty
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                total_row = await conn.fetchrow(
                    "SELECT COUNT(*)::int AS n FROM execution_records "
                    "WHERE outcome != 'pending'"
                )
                rows = await conn.fetch(
                    """
                    SELECT failure_category, COUNT(*)::int AS n
                    FROM execution_records
                    WHERE outcome = 'failed' AND failure_category != 'none'
                    GROUP BY failure_category
                    """
                )
        except Exception as exc:
            _log.error("execution_intel.failure_rates_failed", error=str(exc))
            return empty

        n_settled = int(total_row["n"]) if total_row else 0
        out: dict[str, float | int] = {"n_settled": n_settled}
        if n_settled <= 0:
            return out
        for r in rows:
            out[r["failure_category"]] = int(r["n"]) / n_settled
        return out

    async def success_rate(self) -> float | None:
        """Measured success rate over settled records (the 'execution' metric).

        Returns succeeded / (settled non-pending), or ``None`` (UNVERIFIED) when
        nothing has settled — never a guessed default.
        """
        if self._pool is None:
            return None
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                row = await conn.fetchrow(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE outcome != 'pending')::int AS n_settled,
                        COUNT(*) FILTER (WHERE outcome = 'succeeded')::int AS n_ok
                    FROM execution_records
                    """
                )
        except Exception as exc:
            _log.error("execution_intel.success_rate_failed", error=str(exc))
            return None
        if row is None:
            return None
        n_settled = int(row["n_settled"])
        return (int(row["n_ok"]) / n_settled) if n_settled > 0 else None

    @staticmethod
    def _row_to_record(row: Any) -> ExecutionRecord:
        meta = row["metadata"]
        return ExecutionRecord(
            record_id=str(row["record_id"]),
            plan_id=row["plan_id"],
            trend_id=row["trend_id"],
            opportunity_type=row["opportunity_type"],
            region=row["region"],
            supplier_name=row["supplier_name"],
            planned_units=int(row["planned_units"]),
            planned_unit_cost_usd=row["planned_unit_cost_usd"],
            planned_margin_pct=row["planned_margin_pct"],
            outcome=ExecutionOutcome(row["outcome"]),
            realized_units=row["realized_units"],
            realized_pnl_usd=row["realized_pnl_usd"],
            realized_cost_usd=row["realized_cost_usd"],
            delay_hours=row["delay_hours"],
            failure_category=ExecutionFailureCategory(row["failure_category"]),
            failure_detail=row["failure_detail"],
            created_at=row["created_at"],
            settled_at=row["settled_at"],
            settlement_timestamp=row["settlement_timestamp"],
            metadata=json.loads(meta) if isinstance(meta, str) else (meta or {}),
        )
