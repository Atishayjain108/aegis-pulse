"""Parse-path tests for the free-API commerce adapters (eBay / BestBuy / Etsy).

These were at 0% coverage. They are the key-gated, fail-open real-API path that
gives AEGIS free structured price data — so their parse() (item dict → priced
T2_commerce ProductSignal) is exactly the code that must be correct for the
arbitrage engine. We assert: a valid item → priced T2 signal; a price-less item
→ None (never misclassified); and the eBay price helper.
"""

from __future__ import annotations

from decimal import Decimal

from aegis.schemas.enums import Platform, SourceTier
from aegis.scrape.base import ScrapeContext
from aegis.scrape.sources.bestbuy import BestBuyAdapter, BestBuyConfig
from aegis.scrape.sources.ebay_browse import (
    EbayBrowseAdapter,
    EbayBrowseConfig,
    _parse_price,
)
from aegis.scrape.sources.etsy import EtsyAdapter, EtsyConfig


def _ctx() -> ScrapeContext:
    return ScrapeContext()


# --------------------------------------------------------------------------- ebay
def test_ebay_parse_price_helper():
    val, cur = _parse_price({"price": {"value": "19.99", "currency": "usd"}})
    assert val == Decimal("19.99")
    assert cur == "USD"
    # malformed → (None, default USD)
    val2, cur2 = _parse_price({"price": {"value": None}})
    assert val2 is None and cur2 == "USD"


def test_ebay_parse_valid_item_is_priced_commerce():
    adapter = EbayBrowseAdapter(EbayBrowseConfig())
    sig = adapter.parse(
        {
            "title": "Retro Camera",
            "url": "https://www.ebay.com/itm/123",
            "item_id": "123",
            "raw_json": {"price": 49.5, "currency": "USD", "listing_total": 42},
        },
        _ctx(),
    )
    assert sig is not None
    assert sig.platform == Platform.EBAY
    assert sig.tier == SourceTier.TIER_2_COMMERCE
    assert sig.price is not None and float(sig.price.amount) == 49.5


def test_ebay_parse_priceless_returns_none():
    adapter = EbayBrowseAdapter(EbayBrowseConfig())
    sig = adapter.parse(
        {"title": "No Price", "url": "https://ebay.com/itm/9", "raw_json": {}}, _ctx()
    )
    assert sig is None


# ------------------------------------------------------------------------- bestbuy
def test_bestbuy_parse_valid_item_is_priced_commerce():
    adapter = BestBuyAdapter(BestBuyConfig())
    sig = adapter.parse(
        {
            "title": "4K TV",
            "url": "https://www.bestbuy.com/site/1.p",
            "sku": "1",
            "raw_json": {"price_usd": 399.99, "review_count": 128, "rating": 4.6},
        },
        _ctx(),
    )
    assert sig is not None
    assert sig.platform == Platform.BESTBUY
    assert sig.tier == SourceTier.TIER_2_COMMERCE
    assert float(sig.price.amount) == 399.99


def test_bestbuy_parse_priceless_returns_none():
    adapter = BestBuyAdapter(BestBuyConfig())
    sig = adapter.parse(
        {"title": "x", "url": "https://bestbuy.com/site/2.p", "raw_json": {}}, _ctx()
    )
    assert sig is None


# ---------------------------------------------------------------------------- etsy
def test_etsy_parse_valid_item_is_priced_commerce():
    adapter = EtsyAdapter(EtsyConfig())
    sig = adapter.parse(
        {
            "title": "Handmade Mug",
            "url": "https://www.etsy.com/listing/5",
            "listing_id": "5",
            "raw_json": {
                "price": 24.0,
                "currency": "USD",
                "num_favorers": 12,
                "views": 300,
                "tags": ["ceramic", "gift"],
            },
        },
        _ctx(),
    )
    assert sig is not None
    assert sig.platform == Platform.ETSY
    assert sig.tier == SourceTier.TIER_2_COMMERCE
    assert float(sig.price.amount) == 24.0
    assert "etsy" in sig.tags


def test_etsy_parse_priceless_returns_none():
    adapter = EtsyAdapter(EtsyConfig())
    sig = adapter.parse(
        {"title": "x", "url": "https://etsy.com/listing/6", "raw_json": {}}, _ctx()
    )
    assert sig is None


def test_parse_missing_title_or_url_returns_none():
    for adapter in (
        EbayBrowseAdapter(EbayBrowseConfig()),
        BestBuyAdapter(BestBuyConfig()),
        EtsyAdapter(EtsyConfig()),
    ):
        assert adapter.parse({"title": "", "url": ""}, _ctx()) is None


# --------------------------------------------------------------------------- fail-open
async def test_bestbuy_search_fails_open_without_key(monkeypatch):
    """No API key → _search returns [] (fail-open), never raises."""
    monkeypatch.delenv("AEGIS_BESTBUY_API_KEY", raising=False)
    adapter = BestBuyAdapter(BestBuyConfig())
    ctx = _ctx()
    await adapter.setup(ctx)
    try:
        assert await adapter._search("tv", 5) == []
    finally:
        await adapter.teardown(ctx)


async def test_etsy_search_fails_open_without_key(monkeypatch):
    monkeypatch.delenv("AEGIS_ETSY_API_KEY", raising=False)
    adapter = EtsyAdapter(EtsyConfig())
    ctx = _ctx()
    await adapter.setup(ctx)
    try:
        assert await adapter._search("mug", 5) == []
    finally:
        await adapter.teardown(ctx)


async def test_ebay_search_fails_open_without_credentials(monkeypatch):
    monkeypatch.delenv("AEGIS_EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("AEGIS_EBAY_CLIENT_SECRET", raising=False)
    adapter = EbayBrowseAdapter(EbayBrowseConfig())
    ctx = _ctx()
    await adapter.setup(ctx)
    try:
        assert await adapter._search("camera", 5) == []
    finally:
        await adapter.teardown(ctx)
