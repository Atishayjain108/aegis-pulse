"""Pydantic v2 frozen models for Phase 7 Geospatial Intelligence."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from aegis.geo.config import Region


class GeoOpportunity(BaseModel, frozen=True):
    """Cross-market arbitrage opportunity.

    All monetary values in USD for comparability; local-currency values
    also stored for display/audit.
    """

    opportunity_id: str = Field(default_factory=lambda: str(uuid4()))
    product_sku: str
    product_title: str
    category: str
    hs_code: str

    origin_region: Region
    origin_price_local: Decimal      # in origin currency
    origin_currency: str

    destination_region: Region
    destination_price_local: Decimal  # in destination currency
    destination_currency: str
    destination_price_usd: Decimal    # converted for comparison

    shipping_cost_usd: Decimal
    duty_cost_usd: Decimal
    platform_fee_usd: Decimal
    total_landed_cost_usd: Decimal    # origin_price_usd + shipping + duty

    gross_margin_usd: Decimal         # destination_price_usd - total_landed_cost_usd
    gross_margin_pct: Decimal         # gross_margin / destination_price_usd × 100

    demand_intensity: float = Field(ge=0.0, le=1.0)
    market_size_score: float = Field(ge=0.0, le=1.0)
    opportunity_score: float          # composite rank value

    fx_rate_used: Decimal             # origin_currency → USD rate at analysis time
    analysis_ts: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegionalDemandSnapshot(BaseModel, frozen=True):
    """Demand signal data for a region, derived from Phase 1 signals table."""

    region: Region
    platform: str
    signal_count_24h: int
    signal_count_7d: int
    avg_confidence: float
    total_engagement: int
    demand_intensity: float = Field(ge=0.0, le=1.0)
    price_samples: list[Decimal] = Field(default_factory=list)
    median_price_usd: Decimal | None = None
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FXRateSnapshot(BaseModel, frozen=True):
    """Single FX rate observation from frankfurter.app (ECB rates)."""

    base: str
    quote: str
    rate: Decimal
    source: str = "frankfurter"
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TariffLookupResult(BaseModel, frozen=True):
    """Result of a tariff duty lookup."""

    hs_code: str
    description: str
    origin: str
    destination: str
    duty_rate: Decimal       # fraction, e.g. 0.16 = 16%
    duty_usd: Decimal        # computed on a given value
    value_usd: Decimal
    source: str = "wto_mfn_2024"


class GeoArbitrageReport(BaseModel, frozen=True):
    """Summary report from one CrossMarketAnalyzer.find_opportunities() run."""

    product_sku: str
    product_title: str
    category: str
    opportunities_found: int
    top_opportunity: GeoOpportunity | None
    all_opportunities: list[GeoOpportunity]
    analysis_duration_ms: float
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
