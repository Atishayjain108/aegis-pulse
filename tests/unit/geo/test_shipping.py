"""Unit tests for aegis.geo.shipping — shipping cost resolver."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from aegis.geo.config import SHIPPING_MATRIX_USD, Region
from aegis.geo.shipping import ShippingQuote, ShippingResolver


class TestShippingResolver:
    @pytest.fixture()
    def resolver(self) -> ShippingResolver:
        return ShippingResolver()

    async def test_known_route_uses_matrix(self, resolver: ShippingResolver) -> None:
        quote = await resolver.get_quote(Region.US, Region.IN)
        expected = SHIPPING_MATRIX_USD[(Region.US, Region.IN)]
        assert quote.cost_usd == expected
        assert quote.source == "matrix"
        assert quote.carrier == "EMS/Postal"

    def test_unknown_route_uses_default(self, resolver: ShippingResolver) -> None:
        # _matrix_quote is a synchronous method
        quote = resolver._matrix_quote(Region.BR, Region.AU, 0.5)
        assert quote.cost_usd > 0

    async def test_weight_surcharge_applied(self, resolver: ShippingResolver) -> None:
        light = await resolver.get_quote(Region.US, Region.IN, weight_kg=0.5)
        heavy = await resolver.get_quote(Region.US, Region.IN, weight_kg=2.0)
        # 2.0 kg = 0.5 base + 1.5 kg × $1.50 = $2.25 extra
        assert heavy.cost_usd > light.cost_usd
        diff = heavy.cost_usd - light.cost_usd
        assert diff == Decimal("2.25")  # 1.5 kg × $1.50

    async def test_no_surcharge_for_base_weight(self, resolver: ShippingResolver) -> None:
        quote05 = await resolver.get_quote(Region.US, Region.EU, weight_kg=0.5)
        quote03 = await resolver.get_quote(Region.US, Region.EU, weight_kg=0.3)
        # Both ≤ 0.5 kg → same base cost
        assert quote05.cost_usd == quote03.cost_usd

    async def test_transit_days_populated(self, resolver: ShippingResolver) -> None:
        quote = await resolver.get_quote(Region.IN, Region.US)
        assert quote.transit_days_estimate > 0

    async def test_china_us_cheapest_major_route(self, resolver: ShippingResolver) -> None:
        cn_us = await resolver.get_quote(Region.CN, Region.US)
        in_us = await resolver.get_quote(Region.IN, Region.US)
        eu_us = await resolver.get_quote(Region.EU, Region.US)
        assert cn_us.cost_usd < in_us.cost_usd
        assert cn_us.cost_usd < eu_us.cost_usd

    async def test_shipengine_not_called_without_key(self, resolver: ShippingResolver) -> None:
        with patch("aegis.geo.shipping._SHIPENGINE_KEY", ""):
            quote = await resolver.get_quote(Region.US, Region.IN)
        assert quote.source == "matrix"

    async def test_falls_back_to_matrix_on_shipengine_error(self, resolver: ShippingResolver) -> None:
        with (
            patch("aegis.geo.shipping._SHIPENGINE_KEY", "fake-key"),
            patch.object(resolver, "_shipengine_quote", side_effect=Exception("API down")),
        ):
            quote = await resolver.get_quote(Region.US, Region.IN)
        assert quote.source == "matrix"

    def test_quote_is_namedtuple(self, resolver: ShippingResolver) -> None:
        quote = resolver._matrix_quote(Region.US, Region.EU, 0.5)
        assert isinstance(quote, ShippingQuote)
        assert isinstance(quote.cost_usd, Decimal)

    async def test_all_region_pairs_resolvable(self, resolver: ShippingResolver) -> None:
        """Every O×D pair should return a positive cost without raising."""
        for origin in Region:
            for dest in Region:
                if origin == dest:
                    continue
                quote = await resolver.get_quote(origin, dest)
                assert quote.cost_usd > 0, f"Zero cost for {origin}→{dest}"
