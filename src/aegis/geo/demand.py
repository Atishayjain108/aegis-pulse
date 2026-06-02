"""
Regional demand analyzer — queries Phase 1 signals DB for real data.

Demand intensity (0–1) per region is derived from:
  1. Signal velocity: count of signals in last 24h from platforms mapped to that region.
  2. Engagement-weighted confidence: avg(source_confidence) × normalized engagement.
  3. Price samples: median(price_amount) from signals with non-null price data.

All queries use the shared asyncpg pool from aegis.db.pool when available;
falls back to a synthetic estimate (0.5) when no DB connection is present.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from aegis.geo.config import CATEGORY_MEDIAN_PRICES_USD, REGION_CONFIGS, Region

if TYPE_CHECKING:
    from aegis.db.pool import PgPool

_log = structlog.get_logger("aegis.geo.demand")

# Maximum signals expected in 24 h across all platforms for one tenant
# (used as denominator for intensity normalization)
_MAX_SIGNALS_24H = 500

# SQL query to get signal velocity and engagement by platform
_DEMAND_SQL = """
SELECT
    platform,
    COUNT(*)::int                                         AS signal_count,
    COALESCE(AVG(source_confidence), 0.5)::float         AS avg_confidence,
    COALESCE(SUM(views + likes + comments + shares), 0)::bigint
                                                          AS total_engagement
FROM signals
WHERE
    tenant_id = $1
    AND created_at > NOW() - INTERVAL '24 hours'
    AND platform = ANY($2::text[])
GROUP BY platform
"""

_PRICE_SQL = """
SELECT
    price_amount::numeric,
    currency
FROM signals
WHERE
    tenant_id = $1
    AND platform = ANY($2::text[])
    AND price_amount IS NOT NULL
    AND price_amount > 0
    AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC
LIMIT 50
"""


class RegionalDemandAnalyzer:
    """Compute demand intensity per region using Phase 1 signals data."""

    def __init__(self, pool: PgPool | None = None) -> None:
        self._pool = pool

    async def get_demand(
        self,
        region: Region,
        tenant_id: UUID | str,
        *,
        category: str = "general",
    ) -> dict[str, Any]:
        """Return demand metrics for a region.

        Returns a dict with keys:
          - demand_intensity: float 0–1
          - signal_count_24h: int
          - avg_confidence: float
          - total_engagement: int
          - price_samples_usd: list[Decimal]
          - median_price_usd: Decimal | None
        """
        platforms = list(REGION_CONFIGS[region].signal_platforms)

        if self._pool is None:
            return self._synthetic_demand(region, category)

        try:
            return await self._db_demand(region, tenant_id, platforms, category)
        except Exception as exc:
            _log.warning("demand.db_failed", region=region.value, error=str(exc))
            return self._synthetic_demand(region, category)

    async def get_all_regions(
        self,
        tenant_id: UUID | str,
        *,
        category: str = "general",
    ) -> dict[Region, dict[str, Any]]:
        """Fetch demand metrics for all configured regions in parallel."""
        tasks = {
            region: self.get_demand(region, tenant_id, category=category)
            for region in REGION_CONFIGS
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        out: dict[Region, dict[str, Any]] = {}
        for region, result in zip(tasks.keys(), results, strict=False):
            if isinstance(result, Exception):  # pragma: no cover
                out[region] = self._synthetic_demand(region, category)
            else:
                out[region] = result  # type: ignore[assignment]
        return out

    async def _db_demand(
        self,
        region: Region,
        tenant_id: UUID | str,
        platforms: list[str],
        category: str,
    ) -> dict[str, Any]:
        """Query Phase 1 signals table for real demand data."""
        tid = str(tenant_id)

        async with self._pool.acquire(tenant_id=tid) as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(_DEMAND_SQL, tid, platforms)
            price_rows = await conn.fetch(_PRICE_SQL, tid, platforms)

        # Aggregate across all platforms in this region
        total_signals = sum(r["signal_count"] for r in rows)
        avg_conf = (
            sum(r["avg_confidence"] * r["signal_count"] for r in rows) / total_signals
            if total_signals > 0
            else 0.5
        )
        total_engagement = sum(r["total_engagement"] for r in rows)

        # Intensity: signal velocity × confidence, capped at 1.0
        raw_intensity = (total_signals / _MAX_SIGNALS_24H) * avg_conf
        demand_intensity = min(1.0, raw_intensity)

        # Price samples converted to USD
        from aegis.geo.fx import FXRateFetcher  # avoid circular at module level

        fx = FXRateFetcher()
        price_usd_samples: list[Decimal] = []
        for pr in price_rows:
            try:
                amount = Decimal(str(pr["price_amount"]))
                ccy = pr["currency"] or REGION_CONFIGS[region].currency
                rate = await fx.get_rate(ccy, "USD")
                price_usd_samples.append((amount * rate).quantize(Decimal("0.01")))
            except Exception:
                pass

        median_price: Decimal | None = None
        if price_usd_samples:
            sorted_prices = sorted(price_usd_samples)
            mid = len(sorted_prices) // 2
            if len(sorted_prices) % 2 == 0:
                median_price = (sorted_prices[mid - 1] + sorted_prices[mid]) / 2
            else:
                median_price = sorted_prices[mid]

        _log.debug(
            "demand.db_result",
            region=region.value,
            signals=total_signals,
            intensity=demand_intensity,
            price_samples=len(price_usd_samples),
        )

        return {
            "demand_intensity": demand_intensity,
            "signal_count_24h": total_signals,
            "avg_confidence": avg_conf,
            "total_engagement": total_engagement,
            "price_samples_usd": price_usd_samples,
            "median_price_usd": median_price,
        }

    def _synthetic_demand(self, region: Region, category: str) -> dict[str, Any]:
        """Fallback when DB is unavailable.

        Uses real category median prices from published market research
        (config.CATEGORY_MEDIAN_PRICES_USD) and region market_size_score
        as a proxy for demand intensity.
        """
        cfg = REGION_CONFIGS[region]
        cat_key = category.lower() if category.lower() in CATEGORY_MEDIAN_PRICES_USD else "general"
        region_key = region.value.lower()
        price_usd = Decimal(
            str(CATEGORY_MEDIAN_PRICES_USD[cat_key].get(region_key, 20.0))
        )
        # Market size score as demand proxy (0.4–0.95)
        demand_intensity = cfg.market_size_score * 0.6  # scale down — no real data

        return {
            "demand_intensity": demand_intensity,
            "signal_count_24h": 0,
            "avg_confidence": 0.5,
            "total_engagement": 0,
            "price_samples_usd": [price_usd],
            "median_price_usd": price_usd,
        }
