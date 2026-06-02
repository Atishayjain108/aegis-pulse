"""Unit tests for aegis.geo.phase6_bridge."""

from __future__ import annotations

from decimal import Decimal

import pytest

from aegis.geo.config import Region
from aegis.geo.phase6_bridge import (
    _PHASE6_AVAILABLE,
    geo_opportunity_to_execution_intent,
)
from aegis.geo.schemas import GeoOpportunity


def _make_opportunity(**overrides: object) -> GeoOpportunity:
    defaults: dict[str, object] = {
        "product_sku": "SKU-001",
        "product_title": "Test Product",
        "category": "apparel",
        "hs_code": "610910",
        "origin_region": Region.CN,
        "origin_price_local": Decimal("80.00"),
        "origin_currency": "CNY",
        "destination_region": Region.US,
        "destination_price_local": Decimal("24.99"),
        "destination_currency": "USD",
        "destination_price_usd": Decimal("24.99"),
        "shipping_cost_usd": Decimal("3.50"),
        "duty_cost_usd": Decimal("2.64"),
        "platform_fee_usd": Decimal("3.75"),
        "total_landed_cost_usd": Decimal("16.14"),
        "gross_margin_usd": Decimal("8.85"),
        "gross_margin_pct": Decimal("35.41"),
        "demand_intensity": 0.7,
        "market_size_score": 0.92,
        "opportunity_score": 22.8,
        "fx_rate_used": Decimal("0.137"),
        "metadata": {"shipping_carrier": "EMS/Postal", "transit_days": 14},
    }
    defaults.update(overrides)  # type: ignore[arg-type]
    return GeoOpportunity(**defaults)  # type: ignore[arg-type]


class TestPhase6Bridge:
    def test_returns_none_when_phase6_unavailable(self) -> None:
        if _PHASE6_AVAILABLE:
            pytest.skip("Phase 6 is available — skipping unavailability test")
        opp = _make_opportunity()
        result = geo_opportunity_to_execution_intent(opp)
        assert result is None

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_intent_created_when_phase6_available(self) -> None:
        opp = _make_opportunity()
        intent = geo_opportunity_to_execution_intent(opp)
        assert intent is not None

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_intent_id_prefixed_with_geo(self) -> None:
        opp = _make_opportunity()
        intent = geo_opportunity_to_execution_intent(opp)
        assert intent is not None
        assert intent.intent_id.startswith("geo-")  # type: ignore[union-attr]

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_high_margin_gets_enter_position(self) -> None:
        from aegis.execute.schemas.intent import IntentKind  # type: ignore[import-not-found]

        opp = _make_opportunity(gross_margin_pct=Decimal("45.0"))
        intent = geo_opportunity_to_execution_intent(opp)
        assert intent is not None
        assert intent.kind == IntentKind.ENTER_POSITION  # type: ignore[union-attr]

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_medium_margin_gets_enter_position(self) -> None:
        from aegis.execute.schemas.intent import IntentKind  # type: ignore[import-not-found]

        opp = _make_opportunity(gross_margin_pct=Decimal("25.0"))
        intent = geo_opportunity_to_execution_intent(opp)
        assert intent is not None
        assert intent.kind == IntentKind.ENTER_POSITION  # type: ignore[union-attr]

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_low_margin_gets_hold_position(self) -> None:
        from aegis.execute.schemas.intent import IntentKind  # type: ignore[import-not-found]

        opp = _make_opportunity(gross_margin_pct=Decimal("8.0"))
        intent = geo_opportunity_to_execution_intent(opp)
        assert intent is not None
        assert intent.kind == IntentKind.HOLD_POSITION  # type: ignore[union-attr]

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_intent_rationale_contains_geo_info(self) -> None:
        import json

        opp = _make_opportunity()
        intent = geo_opportunity_to_execution_intent(opp)
        assert intent is not None
        rationale = json.loads(intent.rationale)  # type: ignore[union-attr]
        assert "geo_opportunity_id" in rationale
        assert rationale["origin_region"] == "CN"
        assert rationale["destination_region"] == "US"
        assert "shipping_cost_usd" in rationale
        assert "hs_code" in rationale

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_advised_units_positive(self) -> None:
        opp = _make_opportunity()
        intent = geo_opportunity_to_execution_intent(opp)
        assert intent is not None
        assert intent.advised_units >= 1  # type: ignore[union-attr]

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_advised_capital_positive(self) -> None:
        opp = _make_opportunity()
        intent = geo_opportunity_to_execution_intent(opp)
        assert intent is not None
        assert intent.advised_capital_usd >= 0  # type: ignore[union-attr]

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_loss_probability_bounded(self) -> None:
        opp = _make_opportunity(demand_intensity=0.9, market_size_score=0.9)
        intent = geo_opportunity_to_execution_intent(opp)
        assert intent is not None
        assert intent.loss_probability is not None  # type: ignore[union-attr]
        assert 0.0 <= intent.loss_probability <= 1.0  # type: ignore[union-attr]

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_trend_id_contains_region_arrow(self) -> None:
        opp = _make_opportunity()
        intent = geo_opportunity_to_execution_intent(opp)
        assert intent is not None
        assert "CN→US" in intent.trend_id  # type: ignore[union-attr]

    @pytest.mark.skipif(not _PHASE6_AVAILABLE, reason="aegis-phase4 not installed")
    def test_custom_tenant_id_used(self) -> None:
        import uuid

        opp = _make_opportunity()
        tid = uuid.UUID("12345678-0000-0000-0000-000000000001")
        intent = geo_opportunity_to_execution_intent(opp, tenant_id=tid)
        assert intent is not None
        assert intent.tenant_id == tid  # type: ignore[union-attr]
