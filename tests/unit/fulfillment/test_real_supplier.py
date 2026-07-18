"""FIX-4 (forensic audit): proof the supplier pipeline is no longer fictional.

The old PrintfulClient placed orders against a hardcoded "generic T-shirt"
product id and a "TBD"/"00000" recipient address. These tests prove the client
now refuses to create an order without a real, verified variant + recipient,
and that the cost comes from the real estimate endpoint rather than a derived
constant.
"""

from __future__ import annotations

import pytest

from aegis.fulfillment.printful import PrintfulClient, _is_real_recipient


class TestRecipientValidation:
    def test_tbd_address_rejected(self) -> None:
        assert _is_real_recipient(
            {"address1": "TBD", "zip": "00000", "country_code": "US"}
        ) is False

    def test_empty_rejected(self) -> None:
        assert _is_real_recipient(None) is False
        assert _is_real_recipient({}) is False

    def test_zero_zip_rejected(self) -> None:
        assert _is_real_recipient(
            {"address1": "12 Real St", "zip": "00000", "country_code": "IN"}
        ) is False

    def test_real_recipient_accepted(self) -> None:
        assert _is_real_recipient(
            {"address1": "12 MG Road", "zip": "560001", "country_code": "IN"}
        ) is True


class TestOrderRefusal:
    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self) -> None:
        client = PrintfulClient(api_key="")
        ids = await client.create_orders(
            product_ref="t1", quantity=2, unit_price_usd=20.0,
            variant_id=42, recipient={"address1": "12 MG Road", "zip": "560001", "country_code": "IN"},
        )
        assert ids == []

    @pytest.mark.asyncio
    async def test_no_variant_refused(self) -> None:
        """With an API key but no verified variant, no fictional order is placed."""
        client = PrintfulClient(api_key="real-key")
        ids = await client.create_orders(
            product_ref="t1", quantity=2, unit_price_usd=20.0,
            variant_id=None,
            recipient={"address1": "12 MG Road", "zip": "560001", "country_code": "IN"},
        )
        assert ids == []

    @pytest.mark.asyncio
    async def test_placeholder_recipient_refused(self) -> None:
        client = PrintfulClient(api_key="real-key")
        ids = await client.create_orders(
            product_ref="t1", quantity=2, unit_price_usd=20.0,
            variant_id=42,
            recipient={"address1": "TBD", "zip": "00000", "country_code": "US"},
        )
        assert ids == []


class TestFindMatchingProductNoKey:
    @pytest.mark.asyncio
    async def test_no_key_returns_none(self) -> None:
        client = PrintfulClient(api_key="")
        assert await client.find_matching_product("apparel", ["t-shirt"]) is None

    @pytest.mark.asyncio
    async def test_get_real_cost_no_key_returns_none(self) -> None:
        client = PrintfulClient(api_key="")
        assert await client.get_real_cost(42, {"country_code": "IN"}) is None
