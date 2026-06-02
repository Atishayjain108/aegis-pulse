"""Unit tests for aegis.geo.api — FastAPI router."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

try:
    from fastapi.testclient import TestClient
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

from aegis.geo.schemas import GeoArbitrageReport


def _make_report() -> GeoArbitrageReport:
    return GeoArbitrageReport(
        product_sku="SKU-001",
        product_title="Test Product",
        category="apparel",
        opportunities_found=0,
        top_opportunity=None,
        all_opportunities=[],
        analysis_duration_ms=42.0,
    )


@pytest.mark.skipif(not _FASTAPI_AVAILABLE, reason="fastapi not installed")
class TestGeoAPIRouter:
    @pytest.fixture()
    def client(self) -> TestClient:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from aegis.geo.api import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/geo/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["phase"] == "7"

    def test_fx_endpoint_returns_rates(self, client: TestClient) -> None:
        mock_rates = {
            "USD": Decimal("1.0"), "INR": Decimal("84.0"), "EUR": Decimal("0.924"),
            "GBP": Decimal("0.788"), "JPY": Decimal("150.5"),
            "CNY": Decimal("7.27"), "AUD": Decimal("1.53"), "BRL": Decimal("5.00"),
        }
        with patch("aegis.geo.api._fx") as mock_fx:
            mock_fx.get_all_rates = AsyncMock(return_value=mock_rates)
            resp = client.get("/geo/fx")
        assert resp.status_code == 200
        data = resp.json()
        assert data["base"] == "USD"
        assert "INR" in data["rates"]

    def test_analyze_endpoint_calls_analyzer(self, client: TestClient) -> None:
        report = _make_report()
        with patch("aegis.geo.api.CrossMarketAnalyzer") as MockAnalyzer:
            inst = AsyncMock()
            inst.find_opportunities = AsyncMock(return_value=report)
            MockAnalyzer.return_value = inst
            resp = client.post(
                "/geo/analyze",
                json={"product_sku": "SKU-001", "product_title": "My Product"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["product_sku"] == "SKU-001"

    def test_analyze_validates_empty_sku(self, client: TestClient) -> None:
        resp = client.post(
            "/geo/analyze",
            json={"product_sku": "", "product_title": "My Product"},
        )
        assert resp.status_code == 422

    def test_tariff_endpoint_returns_result(self, client: TestClient) -> None:
        resp = client.get("/geo/tariff/610910/IN")
        assert resp.status_code == 200
        data = resp.json()
        assert data["hs_code"] == "610910"
        assert data["destination"] == "IN"
        assert float(data["duty_rate"]) > 0

    def test_shipping_endpoint_returns_quote(self, client: TestClient) -> None:
        resp = client.get("/geo/shipping/US/IN")
        assert resp.status_code == 200
        data = resp.json()
        assert data["origin"] == "US"
        assert data["destination"] == "IN"
        assert data["cost_usd"] > 0

    def test_shipping_invalid_region_returns_422(self, client: TestClient) -> None:
        resp = client.get("/geo/shipping/XX/YY")
        assert resp.status_code == 422

    def test_analyze_server_error_returns_500(self, client: TestClient) -> None:
        with patch("aegis.geo.api.CrossMarketAnalyzer") as MockAnalyzer:
            inst = AsyncMock()
            inst.find_opportunities = AsyncMock(side_effect=RuntimeError("boom"))
            MockAnalyzer.return_value = inst
            resp = client.post(
                "/geo/analyze",
                json={"product_sku": "SKU-001", "product_title": "My Product"},
            )
        assert resp.status_code == 500
