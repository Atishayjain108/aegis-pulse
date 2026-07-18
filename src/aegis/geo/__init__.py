"""
aegis.geo — Phase 7: Geospatial Intelligence & Cross-Market Arbitrage.

Identifies geographic price/demand inefficiencies using:
  - Real-time FX rates (Frankfurter / ECB, no API key required)
  - WTO MFN applied tariff schedule 2024 (real published rates)
  - EMS/postal shipping rate matrix 2024 (economy tier)
  - Phase 1 signals DB for regional demand intensity + price data

Public API::

    from aegis.geo import CrossMarketAnalyzer, Region

    analyzer = CrossMarketAnalyzer()
    report = await analyzer.find_opportunities("SKU-001", "My Product", "apparel")
"""

from __future__ import annotations

from aegis.geo.arbitrage import CrossMarketAnalyzer
from aegis.geo.config import (
    CATEGORY_HS_MAP,
    CATEGORY_MEDIAN_PRICES_USD,
    REGION_CONFIGS,
    SHIPPING_MATRIX_USD,
    Region,
    RegionConfig,
)
from aegis.geo.demand import RegionalDemandAnalyzer
from aegis.geo.fx import FXRateFetcher
from aegis.geo.phase6_bridge import geo_opportunity_to_execution_intent
from aegis.geo.schemas import (
    GeoArbitrageReport,
    GeoOpportunity,
    TariffLookupResult,
)
from aegis.geo.shipping import ShippingResolver
from aegis.geo.tariffs import TariffEstimator

__version__ = "7.0.0"

__all__ = [
    "CrossMarketAnalyzer",
    "FXRateFetcher",
    "GeoArbitrageReport",
    "GeoOpportunity",
    "Region",
    "RegionConfig",
    "REGION_CONFIGS",
    "CATEGORY_HS_MAP",
    "CATEGORY_MEDIAN_PRICES_USD",
    "SHIPPING_MATRIX_USD",
    "RegionalDemandAnalyzer",
    "ShippingResolver",
    "TariffEstimator",
    "TariffLookupResult",
    "geo_opportunity_to_execution_intent",
]
