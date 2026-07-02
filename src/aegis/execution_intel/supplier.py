"""
aegis.execution_intel.supplier — Supplier Intelligence (Rule 3).

Builds a Supplier Trust Score from REAL events only:
  * verification attempts (inventory/cost checks against a supplier API), and
  * settled fulfillment outcomes (from SettlementManager / execution_records).

Identity + a base trust scalar are reused from ``EntityMemory`` (EntityKind.
SUPPLIER) — we do not duplicate supplier identity. The reliability counters
live in ``supplier_reliability`` and are incremented as events happen.

Reality First (Rule 1): with no settled fulfillment history, ``trust`` is
``None`` (UNVERIFIED) — never a guessed number. A verification-only rate is
reported separately and earlier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from aegis.execution_intel.schemas import SupplierTrustScore
from aegis.memory.entity import EntityMemory
from aegis.memory.taxonomy import EntityKind

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.execution_intel.supplier")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class SupplierIntel:
    """Track supplier reliability from verification + fulfillment events."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id
        self._entities = EntityMemory(db_pool, tenant_id=tenant_id)

    @staticmethod
    def _norm(name: str) -> str:
        return name.strip().lower()

    async def _set_tenant(self, conn: Any) -> None:
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
        )

    async def _bump(self, name: str, sets: str, *args: Any) -> bool:
        """Upsert a supplier_reliability row with the given counter increments."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                # `sets` is a hardcoded internal SQL fragment from _bump callers,
                # never user input — S608 is a false positive here.
                await conn.execute(
                    f"""
                    INSERT INTO supplier_reliability (supplier_name)
                    VALUES ($1)
                    ON CONFLICT (tenant_id, supplier_name) DO UPDATE SET
                        {sets}, updated_at = NOW()
                    """,  # noqa: S608
                    name, *args,
                )
            return True
        except Exception as exc:
            _log.error("execution_intel.supplier_bump_failed", error=str(exc))
            return False

    async def record_verification(
        self, supplier_name: str, *, verified: bool, response_ms: float | None = None,
    ) -> bool:
        """Record one real verification call against a supplier API (Rule 3)."""
        name = self._norm(supplier_name)
        await self._entities.upsert(EntityKind.SUPPLIER, name)
        rms = int(response_ms) if response_ms is not None else 0
        has_rms = 1 if response_ms is not None else 0
        return await self._bump(
            name,
            "n_verifications = supplier_reliability.n_verifications + 1, "
            "n_verified = supplier_reliability.n_verified + $2, "
            "total_response_ms = supplier_reliability.total_response_ms + $3, "
            "n_responses = supplier_reliability.n_responses + $4",
            1 if verified else 0,
            rms,
            has_rms,
        )

    async def record_fulfillment(
        self, supplier_name: str, *, succeeded: bool,
        delayed: bool = False, cancelled: bool = False,
    ) -> bool:
        """Record one settled fulfillment outcome attributed to a supplier."""
        name = self._norm(supplier_name)
        await self._entities.upsert(EntityKind.SUPPLIER, name)
        return await self._bump(
            name,
            "n_fulfillments = supplier_reliability.n_fulfillments + 1, "
            "n_fulfilled_ok = supplier_reliability.n_fulfilled_ok + $2, "
            "n_delays = supplier_reliability.n_delays + $3, "
            "n_cancellations = supplier_reliability.n_cancellations + $4",
            1 if succeeded else 0,
            1 if delayed else 0,
            1 if cancelled else 0,
        )

    async def trust_score(self, supplier_name: str) -> SupplierTrustScore:
        """Compute the Supplier Trust Score from measured counters (Rule 3).

        Returns a score with ``trust=None`` (UNVERIFIED) when no fulfillment
        history exists — we never assert reliability we have not observed.
        """
        name = self._norm(supplier_name)
        if self._pool is None:
            return SupplierTrustScore(supplier_name=name)
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                row = await conn.fetchrow(
                    "SELECT * FROM supplier_reliability WHERE supplier_name = $1",
                    name,
                )
        except Exception as exc:
            _log.error("execution_intel.supplier_trust_failed", error=str(exc))
            return SupplierTrustScore(supplier_name=name)

        if row is None:
            return SupplierTrustScore(supplier_name=name)
        return self._row_to_score(row)

    @staticmethod
    def _row_to_score(row: Any) -> SupplierTrustScore:
        n_ver = int(row["n_verifications"])
        n_ful = int(row["n_fulfillments"])
        n_resp = int(row["n_responses"])
        return SupplierTrustScore(
            supplier_name=row["supplier_name"],
            trust=(row["n_fulfilled_ok"] / n_ful) if n_ful > 0 else None,
            verification_rate=(row["n_verified"] / n_ver) if n_ver > 0 else None,
            delay_rate=(row["n_delays"] / n_ful) if n_ful > 0 else None,
            cancellation_rate=(row["n_cancellations"] / n_ful) if n_ful > 0 else None,
            avg_response_ms=(row["total_response_ms"] / n_resp) if n_resp > 0 else None,
            n_verifications=n_ver,
            n_fulfillments=n_ful,
            updated_at=row["updated_at"],
        )

    async def top_suppliers(self, *, limit: int = 20) -> list[SupplierTrustScore]:
        """Return suppliers ranked by measured fulfillment trust (verified first)."""
        if self._pool is None:
            return []
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                rows = await conn.fetch(
                    """
                    SELECT * FROM supplier_reliability
                    ORDER BY (n_fulfillments > 0) DESC,
                             (CASE WHEN n_fulfillments > 0
                                   THEN n_fulfilled_ok::float / n_fulfillments
                                   ELSE -1 END) DESC,
                             n_fulfillments DESC
                    LIMIT $1
                    """,
                    limit,
                )
            return [self._row_to_score(r) for r in rows]
        except Exception as exc:
            _log.error("execution_intel.supplier_top_failed", error=str(exc))
            return []
