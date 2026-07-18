"""Unit tests for aegis.geo.demand — regional demand analyzer."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.geo.config import REGION_CONFIGS, Region
from aegis.geo.demand import RegionalDemandAnalyzer


class TestRegionalDemandAnalyzerNoDB:
    """Tests without a DB connection — uses synthetic fallback."""

    @pytest.fixture()
    def analyzer(self) -> RegionalDemandAnalyzer:
        return RegionalDemandAnalyzer(pool=None)

    async def test_synthetic_demand_returns_dict(self, analyzer: RegionalDemandAnalyzer) -> None:
        result = await analyzer.get_demand(Region.US, "00000000-0000-0000-0000-000000000001")
        assert isinstance(result, dict)
        assert "demand_intensity" in result
        assert "signal_count_24h" in result
        assert "price_samples_usd" in result

    async def test_demand_intensity_bounded(self, analyzer: RegionalDemandAnalyzer) -> None:
        for region in Region:
            result = await analyzer.get_demand(region, "00000000-0000-0000-0000-000000000001")
            assert 0.0 <= result["demand_intensity"] <= 1.0

    async def test_synthetic_has_price_samples(self, analyzer: RegionalDemandAnalyzer) -> None:
        result = await analyzer.get_demand(Region.US, "00000000-0000-0000-0000-000000000001", category="apparel")
        assert len(result["price_samples_usd"]) > 0
        assert all(isinstance(p, Decimal) for p in result["price_samples_usd"])

    async def test_synthetic_median_price_positive(self, analyzer: RegionalDemandAnalyzer) -> None:
        result = await analyzer.get_demand(Region.US, "00000000-0000-0000-0000-000000000001")
        assert result["median_price_usd"] is not None
        assert result["median_price_usd"] > 0

    async def test_all_regions_returns_dict_of_all(self, analyzer: RegionalDemandAnalyzer) -> None:
        all_regions = await analyzer.get_all_regions("00000000-0000-0000-0000-000000000001")
        assert len(all_regions) == len(REGION_CONFIGS)
        for region in REGION_CONFIGS:
            assert region in all_regions

    async def test_electronics_price_higher_than_books(self, analyzer: RegionalDemandAnalyzer) -> None:
        elec = await analyzer.get_demand(Region.US, "00000000-0000-0000-0000-000000000001", category="smartphones")
        books = await analyzer.get_demand(Region.US, "00000000-0000-0000-0000-000000000001", category="books")
        assert elec["median_price_usd"] > books["median_price_usd"]

    async def test_us_has_higher_intensity_proxy_than_br(self, analyzer: RegionalDemandAnalyzer) -> None:
        us = await analyzer.get_demand(Region.US, "00000000-0000-0000-0000-000000000001")
        br = await analyzer.get_demand(Region.BR, "00000000-0000-0000-0000-000000000001")
        assert us["demand_intensity"] > br["demand_intensity"]


class TestRegionalDemandAnalyzerWithDB:
    """Tests with a mocked asyncpg pool."""

    def _make_pool(self, rows: list[dict], price_rows: list[dict]) -> MagicMock:
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock()

        def side_effect_fetch(sql, *args):
            if "price_amount" in sql:
                return price_rows
            return rows

        mock_conn.fetch.side_effect = side_effect_fetch
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_ctx)
        return mock_pool

    async def test_db_demand_uses_signal_count(self) -> None:
        rows = [
            {"platform": "amazon", "signal_count": 100, "avg_confidence": 0.8, "total_engagement": 500}
        ]
        price_rows: list = []
        pool = self._make_pool(rows, price_rows)
        analyzer = RegionalDemandAnalyzer(pool=pool)
        result = await analyzer.get_demand(Region.US, "test-tenant-id")
        assert result["signal_count_24h"] == 100
        assert result["avg_confidence"] == pytest.approx(0.8)
        assert result["demand_intensity"] > 0

    async def test_db_demand_computes_median_price(self) -> None:
        rows: list = []
        price_rows = [
            {"price_amount": "10.00", "currency": "USD"},
            {"price_amount": "20.00", "currency": "USD"},
            {"price_amount": "30.00", "currency": "USD"},
        ]
        pool = self._make_pool(rows, price_rows)

        # Patch at the source module since it's imported inside the method body
        with patch("aegis.geo.fx.FXRateFetcher") as MockFX:
            mock_fx_inst = AsyncMock()
            mock_fx_inst.get_rate = AsyncMock(return_value=Decimal("1.0"))
            MockFX.return_value = mock_fx_inst

            analyzer = RegionalDemandAnalyzer(pool=pool)
            result = await analyzer.get_demand(Region.US, "test-tenant-id")

        # Median of [10, 20, 30] = 20
        assert result["median_price_usd"] == Decimal("20.00")

    async def test_db_demand_computes_even_count_median(self) -> None:
        """Even number of price rows → median = average of two middle values."""
        rows: list = []
        price_rows = [
            {"price_amount": "10.00", "currency": "USD"},
            {"price_amount": "20.00", "currency": "USD"},
            {"price_amount": "30.00", "currency": "USD"},
            {"price_amount": "40.00", "currency": "USD"},
        ]
        pool = self._make_pool(rows, price_rows)

        with patch("aegis.geo.fx.FXRateFetcher") as MockFX:
            mock_fx_inst = AsyncMock()
            mock_fx_inst.get_rate = AsyncMock(return_value=Decimal("1.0"))
            MockFX.return_value = mock_fx_inst

            analyzer = RegionalDemandAnalyzer(pool=pool)
            result = await analyzer.get_demand(Region.US, "test-tenant-id")

        # Median of [10, 20, 30, 40] = (20 + 30) / 2 = 25
        assert result["median_price_usd"] == Decimal("25.00")

    async def test_db_failure_falls_back_to_synthetic(self) -> None:
        pool = MagicMock()
        pool.acquire.side_effect = Exception("DB connection failed")
        analyzer = RegionalDemandAnalyzer(pool=pool)
        result = await analyzer.get_demand(Region.US, "test-tenant-id")
        # Should still return valid data (synthetic fallback)
        assert result["demand_intensity"] >= 0

    async def test_zero_signals_gives_zero_intensity(self) -> None:
        rows: list = []  # no signals
        price_rows: list = []
        pool = self._make_pool(rows, price_rows)
        analyzer = RegionalDemandAnalyzer(pool=pool)
        result = await analyzer.get_demand(Region.US, "test-tenant-id")
        assert result["signal_count_24h"] == 0
        assert result["demand_intensity"] == 0.0
