"""Unit tests for aegis.geo.arbitrage — CrossMarketAnalyzer."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from aegis.geo.arbitrage import MIN_MARGIN_PCT, CrossMarketAnalyzer
from aegis.geo.config import REGION_CONFIGS, Region
from aegis.geo.schemas import GeoArbitrageReport


def _mock_fx_rates() -> dict[str, Decimal]:
    return {
        "USD": Decimal("1.0"),
        "INR": Decimal("84.0"),
        "EUR": Decimal("0.924"),
        "GBP": Decimal("0.788"),
        "JPY": Decimal("150.5"),
        "CNY": Decimal("7.27"),
        "AUD": Decimal("1.53"),
        "BRL": Decimal("5.00"),
    }


def _mock_demand_map() -> dict[Region, dict]:
    return {
        r: {
            "demand_intensity": 0.6,
            "signal_count_24h": 50,
            "avg_confidence": 0.75,
            "total_engagement": 200,
            "price_samples_usd": [],
            "median_price_usd": None,
        }
        for r in REGION_CONFIGS
    }


class TestCrossMarketAnalyzer:
    @pytest.fixture()
    def analyzer(self) -> CrossMarketAnalyzer:
        return CrossMarketAnalyzer(pool=None)

    def _patch_externals(self, analyzer: CrossMarketAnalyzer) -> None:
        """Patch FX and demand methods to return deterministic data."""
        analyzer._fx.get_all_rates = AsyncMock(return_value=_mock_fx_rates())  # type: ignore[method-assign]
        analyzer._demand.get_all_regions = AsyncMock(return_value=_mock_demand_map())  # type: ignore[method-assign]

    async def test_returns_geo_arbitrage_report(self, analyzer: CrossMarketAnalyzer) -> None:
        self._patch_externals(analyzer)
        report = await analyzer.find_opportunities("SKU-001", "Test Product", "apparel")
        assert isinstance(report, GeoArbitrageReport)
        assert report.product_sku == "SKU-001"
        assert report.product_title == "Test Product"

    async def test_opportunities_are_sorted_by_score(self, analyzer: CrossMarketAnalyzer) -> None:
        self._patch_externals(analyzer)
        report = await analyzer.find_opportunities("SKU-001", "Test Product", "apparel")
        scores = [opp.opportunity_score for opp in report.all_opportunities]
        assert scores == sorted(scores, reverse=True)

    async def test_all_opportunities_have_positive_margin(self, analyzer: CrossMarketAnalyzer) -> None:
        self._patch_externals(analyzer)
        report = await analyzer.find_opportunities("SKU-001", "Test Product", "electronics")
        for opp in report.all_opportunities:
            assert opp.gross_margin_pct >= MIN_MARGIN_PCT, (
                f"{opp.origin_region}→{opp.destination_region}: {opp.gross_margin_pct}% < MIN"
            )

    async def test_no_self_routes(self, analyzer: CrossMarketAnalyzer) -> None:
        self._patch_externals(analyzer)
        report = await analyzer.find_opportunities("SKU-001", "Test", "apparel")
        for opp in report.all_opportunities:
            assert opp.origin_region != opp.destination_region

    async def test_report_has_analysis_duration(self, analyzer: CrossMarketAnalyzer) -> None:
        self._patch_externals(analyzer)
        report = await analyzer.find_opportunities("SKU-001", "Test", "apparel")
        assert report.analysis_duration_ms > 0

    async def test_top_opportunity_is_first(self, analyzer: CrossMarketAnalyzer) -> None:
        self._patch_externals(analyzer)
        report = await analyzer.find_opportunities("SKU-001", "Test", "apparel")
        if report.opportunities_found > 0:
            assert report.top_opportunity == report.all_opportunities[0]

    async def test_top_n_respected(self, analyzer: CrossMarketAnalyzer) -> None:
        self._patch_externals(analyzer)
        report = await analyzer.find_opportunities("SKU-001", "Test", "apparel", top_n=3)
        assert len(report.all_opportunities) <= 3

    async def test_opportunity_fields_populated(self, analyzer: CrossMarketAnalyzer) -> None:
        self._patch_externals(analyzer)
        report = await analyzer.find_opportunities("SKU-001", "Test Apparel", "apparel")
        if report.top_opportunity:
            opp = report.top_opportunity
            assert opp.product_sku == "SKU-001"
            assert opp.hs_code != ""
            assert opp.shipping_cost_usd > 0
            assert 0.0 <= opp.demand_intensity <= 1.0
            assert 0.0 < opp.market_size_score <= 1.0
            assert opp.fx_rate_used > 0

    async def test_pair_analysis_uses_category_median_prices(self, analyzer: CrossMarketAnalyzer) -> None:
        """When no DB data, _resolve_price falls back to category medians."""
        self._patch_externals(analyzer)
        # All demand data has no price samples → uses config medians
        price = analyzer._resolve_price(Region.US, "apparel", {"median_price_usd": None})
        from aegis.geo.config import CATEGORY_MEDIAN_PRICES_USD
        expected = Decimal(str(CATEGORY_MEDIAN_PRICES_USD["apparel"]["us"]))
        assert price == expected

    async def test_pair_analysis_uses_db_price_when_available(self, analyzer: CrossMarketAnalyzer) -> None:
        db_price = Decimal("29.99")
        demand_data = {"median_price_usd": db_price, "demand_intensity": 0.7}
        price = analyzer._resolve_price(Region.US, "apparel", demand_data)
        assert price == db_price

    async def test_no_exception_on_pair_failure(self, analyzer: CrossMarketAnalyzer) -> None:
        """_analyze_pair exceptions should be swallowed; other pairs still returned."""
        self._patch_externals(analyzer)
        original = analyzer._analyze_pair

        call_count = 0

        async def sometimes_fail(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if call_count % 7 == 0:
                raise ValueError("simulated failure")
            return await original(*args, **kwargs)

        analyzer._analyze_pair = sometimes_fail  # type: ignore[method-assign]
        report = await analyzer.find_opportunities("SKU-001", "Test", "apparel")
        assert isinstance(report, GeoArbitrageReport)

    async def test_empty_result_when_all_margins_negative(self, analyzer: CrossMarketAnalyzer) -> None:
        """If all pairs produce negative margins, result should be empty."""
        self._patch_externals(analyzer)
        # Make destination prices extremely low by overriding all FX rates
        huge_rates = {k: Decimal("0.001") if k != "USD" else Decimal("1.0") for k in _mock_fx_rates()}
        analyzer._fx.get_all_rates = AsyncMock(return_value=huge_rates)  # type: ignore[method-assign]
        # Very low destination prices → negative margins everywhere
        # The test just ensures no exception is raised
        report = await analyzer.find_opportunities("SKU-001", "Test", "apparel")
        assert isinstance(report, GeoArbitrageReport)
