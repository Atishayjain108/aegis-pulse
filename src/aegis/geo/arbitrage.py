"""
Cross-market arbitrage opportunity detector — Phase 7 core engine.

Algorithm per (origin, destination) pair:
  1. Resolve product price in origin region (DB signals → category median fallback).
  2. Resolve product price in destination region (same).
  3. Fetch real-time FX rate via frankfurter.app (ECB).
  4. Get shipping cost from static EMS rate matrix (or ShipEngine API).
  5. Compute WTO MFN import duty from HS code schedule.
  6. Compute gross margin = dest_price_usd - (origin_price_usd + ship + duty).
  7. Score = gross_margin_pct × demand_intensity × market_size_score.
  8. Rank all pairs; return top N.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from aegis.geo.config import (
    CATEGORY_MEDIAN_PRICES_USD,
    REGION_CONFIGS,
    Region,
)
from aegis.geo.demand import RegionalDemandAnalyzer
from aegis.geo.fx import FXRateFetcher
from aegis.geo.schemas import GeoArbitrageReport, GeoOpportunity
from aegis.geo.shipping import ShippingResolver
from aegis.geo.tariffs import TariffEstimator

if TYPE_CHECKING:
    from aegis.db.pool import PgPool

_log = structlog.get_logger("aegis.geo.arbitrage")

# Minimum gross margin % to include an opportunity in results
MIN_MARGIN_PCT = Decimal("5.0")

# Maximum opportunities returned per product
MAX_OPPORTUNITIES = 10


class CrossMarketAnalyzer:
    """Identify cross-market arbitrage opportunities for a product.

    Thread-safe; all state is per-call.  Hold one instance per process
    and reuse across analysis runs to benefit from in-process FX cache.
    """

    def __init__(self, pool: PgPool | None = None) -> None:
        self._fx = FXRateFetcher(ttl_seconds=3600)
        self._tariffs = TariffEstimator()
        self._shipping = ShippingResolver()
        self._demand = RegionalDemandAnalyzer(pool=pool)

    async def find_opportunities(
        self,
        product_sku: str,
        product_title: str,
        category: str = "general",
        *,
        tenant_id: UUID | str = "00000000-0000-0000-0000-000000000001",
        top_n: int = MAX_OPPORTUNITIES,
    ) -> GeoArbitrageReport:
        """Find all viable cross-market arbitrage opportunities for a product.

        Returns a GeoArbitrageReport sorted by opportunity_score descending.
        """
        t0 = time.monotonic()

        hs_code = self._tariffs.hs_code_for_category(category)

        # Prefetch: demand data + FX rates for all regions in parallel
        demand_map, fx_rates = await asyncio.gather(
            self._demand.get_all_regions(tenant_id, category=category),
            self._fx.get_all_rates(),
        )

        tasks = []
        for origin in REGION_CONFIGS:
            for dest in REGION_CONFIGS:
                if origin == dest:
                    continue
                tasks.append(
                    self._analyze_pair(
                        product_sku=product_sku,
                        product_title=product_title,
                        category=category,
                        hs_code=hs_code,
                        origin=origin,
                        destination=dest,
                        demand_map=demand_map,
                        fx_rates=fx_rates,
                    )
                )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        opportunities: list[GeoOpportunity] = []
        for r in results:
            if isinstance(r, Exception):
                _log.debug("geo.pair_failed", error=str(r))
                continue
            opp = r  # type: ignore[assignment]
            if opp is not None and opp.gross_margin_pct >= MIN_MARGIN_PCT:
                opportunities.append(opp)

        opportunities.sort(key=lambda x: x.opportunity_score, reverse=True)
        top = opportunities[:top_n]

        elapsed_ms = (time.monotonic() - t0) * 1000
        _log.info(
            "geo.opportunities_found",
            sku=product_sku,
            category=category,
            total_pairs_evaluated=len(tasks),
            viable=len(top),
            top_score=top[0].opportunity_score if top else 0.0,
            elapsed_ms=round(elapsed_ms, 1),
        )

        return GeoArbitrageReport(
            product_sku=product_sku,
            product_title=product_title,
            category=category,
            opportunities_found=len(top),
            top_opportunity=top[0] if top else None,
            all_opportunities=top,
            analysis_duration_ms=elapsed_ms,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _analyze_pair(
        self,
        *,
        product_sku: str,
        product_title: str,
        category: str,
        hs_code: str,
        origin: Region,
        destination: Region,
        demand_map: dict[Region, dict[str, Any]],
        fx_rates: dict[str, Decimal],
    ) -> GeoOpportunity | None:
        dest_cfg = REGION_CONFIGS[destination]
        origin_cfg = REGION_CONFIGS[origin]

        # --- 1. Resolve prices (returned in USD by _resolve_price) ---
        origin_demand = demand_map.get(origin, {})
        dest_demand = demand_map.get(destination, {})

        origin_price_usd = self._resolve_price(origin, category, origin_demand)
        dest_price_usd = self._resolve_price(destination, category, dest_demand)

        if origin_price_usd <= 0 or dest_price_usd <= 0:
            return None

        # --- 2. FX: compute local-currency display values ---
        # fx_rates = {"USD": 1.0, "INR": 84.0, ...} — units of local per 1 USD
        origin_vs_usd = fx_rates.get(origin_cfg.currency, Decimal("1"))
        dest_vs_usd = fx_rates.get(dest_cfg.currency, Decimal("1"))

        # local price = USD price × (local_units per 1 USD)
        origin_price_local = (origin_price_usd * origin_vs_usd).quantize(Decimal("0.01"))
        dest_price_local = (dest_price_usd * dest_vs_usd).quantize(Decimal("0.01"))

        # FX rate for audit: 1 unit of origin currency → USD
        fx_rate_origin_to_usd = (
            Decimal("1") / origin_vs_usd if origin_vs_usd != 0 else Decimal("1")
        )

        # --- 3. Shipping cost ---
        quote = await self._shipping.get_quote(origin, destination)
        shipping_usd = quote.cost_usd

        # --- 4. Import duty ---
        tariff_result = await self._tariffs.estimate(
            hs_code=hs_code,
            origin=origin.value,
            destination=destination.value,
            value_usd=origin_price_usd,
        )
        duty_usd = tariff_result.duty_usd

        # --- 5. Platform fee at destination ---
        platform_fee_usd = (dest_price_usd * dest_cfg.seller_fee_pct).quantize(
            Decimal("0.01")
        )

        # --- 6. Compute margins ---
        total_landed = (origin_price_usd + shipping_usd + duty_usd).quantize(
            Decimal("0.01")
        )
        gross_margin_usd = (dest_price_usd - total_landed).quantize(Decimal("0.01"))

        if dest_price_usd <= 0:
            return None

        gross_margin_pct = (gross_margin_usd / dest_price_usd * 100).quantize(
            Decimal("0.01")
        )

        # --- 7. Demand + market size ---
        demand_intensity = float(dest_demand.get("demand_intensity", dest_cfg.market_size_score * 0.6))
        demand_source = str(dest_demand.get("demand_source", "synthetic"))
        market_size = dest_cfg.market_size_score

        # --- 8. Composite opportunity score ---
        # Weighting: margin drives the score; demand and market amplify it.
        margin_factor = max(0.0, float(gross_margin_pct))
        opportunity_score = margin_factor * demand_intensity * market_size

        return GeoOpportunity(
            product_sku=product_sku,
            product_title=product_title,
            category=category,
            hs_code=hs_code,
            origin_region=origin,
            origin_price_local=origin_price_local,
            origin_currency=origin_cfg.currency,
            destination_region=destination,
            destination_price_local=dest_price_local,
            destination_currency=dest_cfg.currency,
            destination_price_usd=dest_price_usd.quantize(Decimal("0.01")),
            shipping_cost_usd=shipping_usd,
            duty_cost_usd=duty_usd,
            platform_fee_usd=platform_fee_usd,
            total_landed_cost_usd=total_landed,
            gross_margin_usd=gross_margin_usd,
            gross_margin_pct=gross_margin_pct,
            demand_intensity=demand_intensity,
            demand_source=demand_source,
            market_size_score=market_size,
            opportunity_score=opportunity_score,
            fx_rate_used=fx_rate_origin_to_usd.quantize(Decimal("0.000001")),
            metadata={
                "shipping_carrier": quote.carrier,
                "transit_days": quote.transit_days_estimate,
                "shipping_source": quote.source,
                "tariff_source": tariff_result.source,
                "vat_gst_pct": str(dest_cfg.vat_gst_pct),
                "platform_fee_pct": str(dest_cfg.seller_fee_pct),
            },
        )

    def _resolve_price(
        self, region: Region, category: str, demand_data: dict[str, Any]
    ) -> Decimal:
        """Resolve product price for a region.

        Priority:
          1. Median price from scraped signals (demand_data from Phase 1 DB).
          2. Published category median price from config (real market research).
        """
        # Phase 1 DB median (real data)
        median = demand_data.get("median_price_usd")
        if median is not None and median > 0:
            # median_price_usd is already in USD from the demand analyzer
            return median  # type: ignore[return-value]

        # Fallback: published category median (still real data, not mock)
        cat_key = category.lower() if category.lower() in CATEGORY_MEDIAN_PRICES_USD else "general"
        region_key = region.value.lower()
        price = CATEGORY_MEDIAN_PRICES_USD[cat_key].get(region_key, 20.0)
        return Decimal(str(price))
