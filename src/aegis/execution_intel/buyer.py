"""
aegis.execution_intel.buyer — Buyer Intelligence (Rule 4), honestly scoped.

Reality First (Rule 1): AEGIS has NO real buyer/order/customer data. So this
module does NOT invent buyers. It tracks DEMAND as a proxy keyed by
(region, category), derived from real signal velocity/engagement, and is
explicit that this is a proxy — never verified purchase demand.

``buyer_trust`` is always ``None`` (UNVERIFIED) until a real order source is
wired and ``record_order`` starts being called with real data. The
``record_order`` API exists so the day real order data arrives, buyer trust
becomes measurable without a schema change — but nothing synthetic ever feeds it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from aegis.execution_intel.schemas import BuyerDemandProxy

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.execution_intel.buyer")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class BuyerIntel:
    """Demand-proxy memory keyed by (region, category). Buyer trust = UNVERIFIED."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    @staticmethod
    def _norm(region: str, category: str) -> tuple[str, str]:
        return (region.strip().upper() or "GLOBAL", category.strip().lower() or "general")

    async def _set_tenant(self, conn: Any) -> None:
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
        )

    async def record_demand_observation(
        self, region: str, category: str, intensity: float,
    ) -> bool:
        """Fold one demand-proxy observation into the running mean.

        ``intensity`` is a signal-derived proxy in [0,1]. The running mean is
        maintained server-side so repeated observations converge honestly.
        """
        if self._pool is None:
            return False
        reg, cat = self._norm(region, category)
        clamped = max(0.0, min(1.0, float(intensity)))
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                await conn.execute(
                    """
                    INSERT INTO buyer_demand (
                        region, category, demand_intensity, n_demand_observations
                    ) VALUES ($1,$2,$3,1)
                    ON CONFLICT (tenant_id, region, category) DO UPDATE SET
                        demand_intensity = (
                            COALESCE(buyer_demand.demand_intensity, 0)
                                * buyer_demand.n_demand_observations + $3
                        ) / (buyer_demand.n_demand_observations + 1),
                        n_demand_observations = buyer_demand.n_demand_observations + 1,
                        updated_at = NOW()
                    """,
                    reg, cat, clamped,
                )
            return True
        except Exception as exc:
            _log.error("execution_intel.buyer_demand_failed", error=str(exc))
            return False

    async def record_order(
        self, region: str, category: str, *, fulfilled: bool, cancelled: bool = False,
    ) -> bool:
        """Record a REAL buyer order outcome (Rule 4).

        Only call this with genuine order data. Until a real order source exists
        this is never invoked, so buyer trust correctly stays UNVERIFIED.
        """
        if self._pool is None:
            return False
        reg, cat = self._norm(region, category)
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                await conn.execute(
                    """
                    INSERT INTO buyer_demand (region, category, n_orders, n_fulfilled, n_cancelled)
                    VALUES ($1,$2,1,$3,$4)
                    ON CONFLICT (tenant_id, region, category) DO UPDATE SET
                        n_orders = buyer_demand.n_orders + 1,
                        n_fulfilled = buyer_demand.n_fulfilled + $3,
                        n_cancelled = buyer_demand.n_cancelled + $4,
                        updated_at = NOW()
                    """,
                    reg, cat, 1 if fulfilled else 0, 1 if cancelled else 0,
                )
            return True
        except Exception as exc:
            _log.error("execution_intel.buyer_order_failed", error=str(exc))
            return False

    async def demand_proxy(self, region: str, category: str) -> BuyerDemandProxy:
        """Return the demand proxy for (region, category). Buyer trust UNVERIFIED."""
        reg, cat = self._norm(region, category)
        if self._pool is None:
            return BuyerDemandProxy(region=reg, category=cat)
        try:
            async with self._pool.acquire() as conn:
                await self._set_tenant(conn)
                row = await conn.fetchrow(
                    "SELECT * FROM buyer_demand WHERE region = $1 AND category = $2",
                    reg, cat,
                )
        except Exception as exc:
            _log.error("execution_intel.buyer_proxy_failed", error=str(exc))
            return BuyerDemandProxy(region=reg, category=cat)
        if row is None:
            return BuyerDemandProxy(region=reg, category=cat)
        return self._row_to_proxy(row)

    @staticmethod
    def _row_to_proxy(row: Any) -> BuyerDemandProxy:
        n_orders = int(row["n_orders"])
        return BuyerDemandProxy(
            region=row["region"],
            category=row["category"],
            demand_intensity=row["demand_intensity"],
            # Rule 1: trust stays None until real orders exist.
            buyer_trust=(row["n_fulfilled"] / n_orders) if n_orders > 0 else None,
            n_demand_observations=int(row["n_demand_observations"]),
            n_orders=n_orders,
            n_fulfilled=int(row["n_fulfilled"]),
            updated_at=row["updated_at"],
        )
