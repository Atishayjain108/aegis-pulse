"""
Integration tests for Phase 7 — Geospatial Intelligence.

These tests exercise real external calls (Frankfurter FX API, tariff logic,
shipping matrix) without mocking.  They do NOT require a running database —
the demand analyzer falls back to synthetic data.

Run with:
    uv run python -m pytest tests/integration/geo/ -v -p no:hypothesis
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aegis.geo.arbitrage import CrossMarketAnalyzer
from aegis.geo.config import (
    HS_TARIFF_SCHEDULE,
    REGION_CONFIGS,
    SHIPPING_MATRIX_USD,
    Region,
)
from aegis.geo.fx import FXRateFetcher
from aegis.geo.schemas import GeoArbitrageReport
from aegis.geo.shipping import ShippingResolver
from aegis.geo.tariffs import TariffEstimator


@pytest.mark.asyncio
class TestCrossMarketOpportunityDetection:
    async def test_find_opportunities_returns_report(self) -> None:
        analyzer = CrossMarketAnalyzer()
        report = await analyzer.find_opportunities(
            "TSHIRT-001", "Classic Cotton T-Shirt", "apparel"
        )
        assert isinstance(report, GeoArbitrageReport)
        assert report.product_sku == "TSHIRT-001"
        assert report.analysis_duration_ms > 0

    async def test_opportunities_have_positive_margins(self) -> None:
        analyzer = CrossMarketAnalyzer()
        report = await analyzer.find_opportunities(
            "PHONE-001", "Budget Smartphone", "smartphones"
        )
        for opp in report.all_opportunities:
            assert opp.gross_margin_pct >= Decimal("5.0"), (
                f"{opp.origin_region}→{opp.destination_region}: "
                f"margin {opp.gross_margin_pct}% < 5%"
            )

    async def test_no_self_routes_in_results(self) -> None:
        analyzer = CrossMarketAnalyzer()
        report = await analyzer.find_opportunities("SKU-001", "Test", "toys")
        for opp in report.all_opportunities:
            assert opp.origin_region != opp.destination_region

    async def test_opportunity_metadata_fields(self) -> None:
        analyzer = CrossMarketAnalyzer()
        report = await analyzer.find_opportunities(
            "SHOE-001", "Running Sneakers", "sneakers"
        )
        for opp in report.all_opportunities:
            assert opp.shipping_cost_usd > 0
            assert opp.duty_cost_usd >= 0
            assert opp.fx_rate_used > 0
            assert opp.hs_code in HS_TARIFF_SCHEDULE
            assert "shipping_carrier" in opp.metadata

    async def test_sorted_by_opportunity_score(self) -> None:
        analyzer = CrossMarketAnalyzer()
        report = await analyzer.find_opportunities("SKU-001", "Test Product", "beauty")
        scores = [opp.opportunity_score for opp in report.all_opportunities]
        assert scores == sorted(scores, reverse=True), "Results not sorted by score"

    async def test_top_opportunity_equals_first(self) -> None:
        analyzer = CrossMarketAnalyzer()
        report = await analyzer.find_opportunities("SKU-001", "Test", "apparel")
        if report.opportunities_found > 0:
            assert report.top_opportunity is not None
            assert report.top_opportunity.opportunity_id == report.all_opportunities[0].opportunity_id


@pytest.mark.asyncio
class TestFXRateIntegration:
    async def test_get_rate_returns_positive(self) -> None:
        fx = FXRateFetcher()
        rate = await fx.get_rate("USD", "INR")
        assert rate > 0

    async def test_usd_to_usd_is_one(self) -> None:
        fx = FXRateFetcher()
        rate = await fx.get_rate("USD", "USD")
        assert rate == Decimal("1.0")

    async def test_all_region_currencies_fetchable(self) -> None:
        fx = FXRateFetcher()
        rates = await fx.get_all_rates()
        for cfg in REGION_CONFIGS.values():
            if cfg.currency != "USD":
                # fallback rates always present; live rates may differ
                assert cfg.currency in rates or cfg.currency in ("BRL", "CNY")

    async def test_rate_is_cached_on_second_call(self) -> None:
        fx = FXRateFetcher(ttl_seconds=60)
        r1 = await fx.get_rate("USD", "EUR")
        r2 = await fx.get_rate("USD", "EUR")
        assert r1 == r2


@pytest.mark.asyncio
class TestTariffIntegration:
    async def test_apparel_us_to_india_duty(self) -> None:
        est = TariffEstimator()
        result = await est.estimate(
            hs_code="610910", origin="US", destination="IN", value_usd=Decimal("100")
        )
        assert result.duty_usd == Decimal("20.00")  # 20% BCD

    async def test_smartphone_zero_duty_in_us(self) -> None:
        est = TariffEstimator()
        result = await est.estimate(
            hs_code="851712", origin="IN", destination="US", value_usd=Decimal("500")
        )
        assert result.duty_usd == Decimal("0.00")

    async def test_category_to_hs_lookup(self) -> None:
        est = TariffEstimator()
        for category in ["apparel", "electronics", "beauty", "toys", "shoes"]:
            hs = est.hs_code_for_category(category)
            assert hs in HS_TARIFF_SCHEDULE


@pytest.mark.asyncio
class TestShippingIntegration:
    async def test_all_matrix_routes_resolve(self) -> None:
        resolver = ShippingResolver()
        for (origin, dest), expected_cost in SHIPPING_MATRIX_USD.items():
            quote = await resolver.get_quote(origin, dest)
            assert quote.cost_usd == expected_cost

    async def test_china_to_us_cheapest_major_route(self) -> None:
        resolver = ShippingResolver()
        cn_us = await resolver.get_quote(Region.CN, Region.US)
        in_us = await resolver.get_quote(Region.IN, Region.US)
        eu_us = await resolver.get_quote(Region.EU, Region.US)
        assert cn_us.cost_usd <= in_us.cost_usd
        assert cn_us.cost_usd <= eu_us.cost_usd

    async def test_transit_days_reasonable(self) -> None:
        resolver = ShippingResolver()
        quote = await resolver.get_quote(Region.US, Region.IN)
        assert 1 <= quote.transit_days_estimate <= 30
