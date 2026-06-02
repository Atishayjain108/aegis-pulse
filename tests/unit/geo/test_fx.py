"""Unit tests for aegis.geo.fx — FX rate fetcher with mocked HTTP."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.geo.config import Region
from aegis.geo.fx import _FALLBACK_RATES_VS_USD, FXRateFetcher


class TestFXRateFetcher:
    @pytest.fixture()
    def fetcher(self) -> FXRateFetcher:
        return FXRateFetcher(ttl_seconds=60)

    def _make_mock_response(self, rates: dict[str, float]) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"base": "USD", "rates": rates}
        return mock_resp

    async def test_same_currency_returns_one(self, fetcher: FXRateFetcher) -> None:
        rate = await fetcher.get_rate("USD", "USD")
        assert rate == Decimal("1.0")

    async def test_get_rate_calls_api(self, fetcher: FXRateFetcher) -> None:
        mock_rates = {"INR": 83.9, "EUR": 0.924, "GBP": 0.788, "JPY": 150.5, "CNY": 7.27, "AUD": 1.53, "BRL": 5.00}
        mock_resp = self._make_mock_response(mock_rates)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("aegis.geo.fx.httpx.AsyncClient", return_value=mock_client):
            rate = await fetcher.get_rate("USD", "INR")

        assert rate == Decimal("83.9")

    async def test_get_rate_caches_result(self, fetcher: FXRateFetcher) -> None:
        mock_rates = {"INR": 83.9, "EUR": 0.924, "GBP": 0.788, "JPY": 150.5, "CNY": 7.27, "AUD": 1.53, "BRL": 5.00}
        mock_resp = self._make_mock_response(mock_rates)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("aegis.geo.fx.httpx.AsyncClient", return_value=mock_client):
            rate1 = await fetcher.get_rate("USD", "INR")
            rate2 = await fetcher.get_rate("USD", "INR")

        assert rate1 == rate2
        assert mock_client.get.call_count == 1  # only one HTTP call

    async def test_cache_expires_after_ttl(self, fetcher: FXRateFetcher) -> None:
        fetcher._ttl = 0  # zero TTL → always expired
        mock_rates = {"INR": 84.0, "EUR": 0.924, "GBP": 0.788, "JPY": 150.5, "CNY": 7.27, "AUD": 1.53, "BRL": 5.00}
        mock_resp = self._make_mock_response(mock_rates)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("aegis.geo.fx.httpx.AsyncClient", return_value=mock_client):
            await fetcher.get_rate("USD", "INR")
            await fetcher.get_rate("USD", "INR")

        assert mock_client.get.call_count == 2  # two HTTP calls due to expired TTL

    async def test_falls_back_on_network_error(self, fetcher: FXRateFetcher) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        with patch("aegis.geo.fx.httpx.AsyncClient", return_value=mock_client):
            rate = await fetcher.get_rate("USD", "INR")

        assert rate == _FALLBACK_RATES_VS_USD["INR"]

    async def test_cross_rate_computation(self, fetcher: FXRateFetcher) -> None:
        # INR/EUR cross rate: if 1 USD = 83.9 INR and 1 USD = 0.924 EUR,
        # then 1 INR = 0.924/83.9 EUR
        mock_rates = {"INR": 83.9, "EUR": 0.924, "GBP": 0.788, "JPY": 150.5, "CNY": 7.27, "AUD": 1.53, "BRL": 5.00}
        mock_resp = self._make_mock_response(mock_rates)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("aegis.geo.fx.httpx.AsyncClient", return_value=mock_client):
            rate = await fetcher.get_rate("INR", "EUR")

        expected = Decimal("0.924") / Decimal("83.9")
        assert abs(rate - expected) < Decimal("0.000001")

    async def test_to_usd_conversion(self, fetcher: FXRateFetcher) -> None:
        mock_rates = {"INR": 84.0, "EUR": 0.924, "GBP": 0.788, "JPY": 150.5, "CNY": 7.27, "AUD": 1.53, "BRL": 5.00}
        mock_resp = self._make_mock_response(mock_rates)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("aegis.geo.fx.httpx.AsyncClient", return_value=mock_client):
            usd = await fetcher.to_usd(Decimal("840"), "INR")

        # 840 INR / 84 = 10 USD
        assert abs(usd - Decimal("10")) < Decimal("0.01")

    def test_currency_for_region(self, fetcher: FXRateFetcher) -> None:
        assert fetcher.currency_for_region(Region.US) == "USD"
        assert fetcher.currency_for_region(Region.IN) == "INR"
        assert fetcher.currency_for_region(Region.EU) == "EUR"
        assert fetcher.currency_for_region(Region.UK) == "GBP"

    def test_fallback_rates_cover_all_currencies(self) -> None:
        from aegis.geo.config import REGION_CONFIGS
        for cfg in REGION_CONFIGS.values():
            assert cfg.currency in _FALLBACK_RATES_VS_USD, (
                f"No fallback rate for {cfg.currency}"
            )
