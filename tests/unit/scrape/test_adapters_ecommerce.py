"""Phase 4 — Indian e-commerce adapter tests.

Covers:
- Adapter class hierarchy (all inherit SourceAdapter)
- flipkart: FlareSolverr called when plain request returns 403
- flipkart: FlareSolverr semaphore used (flaresolverr_get called, not raw httpx.post)
- amazon_in: parallel category fetch uses asyncio.gather
- meesho: JSON path is primary, HTML is fallback on 401/403
- All adapters: raw_json["currency"] == "INR" in platform_specific
- Parse round-trips against fixture files
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from aegis.scrape.base import ScrapeContext, SourceAdapter
from aegis.scrape.sources.ajio import AjioAdapter, AjioConfig, _extract_ajio_products
from aegis.scrape.sources.amazon_in import AmazonINAdapter, AmazonINConfig, _parse_amazon_in_page
from aegis.scrape.sources.flipkart import FlipkartAdapter, FlipkartConfig, _parse_flipkart_html
from aegis.scrape.sources.indiamart import (
    IndiaMartAdapter,
    IndiaMartConfig,
    _extract_categories_html,
    _extract_search_results,
)
from aegis.scrape.sources.meesho import MeeshoAdapter, MeeshoConfig, _extract_json_products
from aegis.scrape.sources.myntra import MyntraAdapter
from aegis.scrape.sources.nykaa import NykaaAdapter, NykaaConfig
from aegis.scrape.sources.snapdeal import SnapdealAdapter, SnapdealConfig, _parse_rss_feed

_FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def _load_json(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text())


def _load_text(name: str) -> str:
    return (_FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# 1. Adapter class hierarchy
# ---------------------------------------------------------------------------


def test_amazon_in_inherits_source_adapter():
    assert issubclass(AmazonINAdapter, SourceAdapter)


def test_flipkart_inherits_source_adapter():
    assert issubclass(FlipkartAdapter, SourceAdapter)


def test_meesho_inherits_source_adapter():
    assert issubclass(MeeshoAdapter, SourceAdapter)


def test_myntra_inherits_source_adapter():
    assert issubclass(MyntraAdapter, SourceAdapter)


def test_ajio_inherits_source_adapter():
    assert issubclass(AjioAdapter, SourceAdapter)


def test_nykaa_inherits_source_adapter():
    assert issubclass(NykaaAdapter, SourceAdapter)


def test_snapdeal_inherits_source_adapter():
    assert issubclass(SnapdealAdapter, SourceAdapter)


def test_indiamart_inherits_source_adapter():
    assert issubclass(IndiaMartAdapter, SourceAdapter)


# ---------------------------------------------------------------------------
# 2. flipkart: FlareSolverr called when plain request returns 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flipkart_flaresolverr_called_on_403():
    """When the plain httpx request returns 403, flaresolverr_get must be invoked."""
    adapter = FlipkartAdapter(FlipkartConfig())
    ctx = ScrapeContext()
    await adapter.setup(ctx)

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Access Denied"
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "403", request=MagicMock(), response=MagicMock(status_code=403)
        )
    )

    with (
        patch.object(adapter._client, "get", new_callable=AsyncMock, return_value=mock_resp),  # type: ignore[union-attr]
        # Keep the test hermetic: no live session harvest, no live browser fetch.
        patch(
            "aegis.scrape.sources.flipkart.get_session_bundle",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "aegis.scrape.sources.flipkart._playwright_fetch",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "aegis.scrape.sources.flipkart.flaresolverr_get",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_flare,
    ):
        items = [item async for item in adapter.fetch_raw(ctx, limit=10)]

    await adapter.teardown(ctx)
    mock_flare.assert_called()
    assert items == []


# ---------------------------------------------------------------------------
# 3. flipkart: FlareSolverr semaphore used (flaresolverr_get called, not raw http)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flipkart_flaresolverr_semaphore_used():
    """Verify flaresolverr_get (which uses the governor semaphore) is called,
    not a raw httpx.post, when a 403 is returned."""
    adapter = FlipkartAdapter(FlipkartConfig())
    ctx = ScrapeContext()
    await adapter.setup(ctx)

    mock_403 = MagicMock()
    mock_403.status_code = 403
    mock_403.text = "bot detected"

    with (
        patch.object(adapter._client, "get", new_callable=AsyncMock, return_value=mock_403),  # type: ignore[union-attr]
        patch(
            "aegis.scrape.sources.flipkart.flaresolverr_get",
            new_callable=AsyncMock,
            return_value=_load_text("flipkart_bestsellers.html"),
        ) as mock_flare,
    ):
        [item async for item in adapter.fetch_raw(ctx, limit=5)]  # noqa: RUF100

    await adapter.teardown(ctx)
    # flaresolverr_get (which wraps the semaphore) must be the call path
    mock_flare.assert_called()
    assert mock_flare.call_count >= 1


# ---------------------------------------------------------------------------
# 4. amazon_in: parallel category fetch uses asyncio.gather
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amazon_in_parallel_category_fetch_uses_gather():
    """fetch_raw must call asyncio.gather (parallel) for category pages."""
    import asyncio

    adapter = AmazonINAdapter(AmazonINConfig())
    ctx = ScrapeContext()
    await adapter.setup(ctx)

    html = _load_text("amazon_in_bestsellers.html")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    with (
        patch.object(adapter._client, "get", new_callable=AsyncMock, return_value=mock_resp),  # type: ignore[union-attr]
        patch("aegis.scrape.sources.amazon_in.asyncio.gather", wraps=asyncio.gather) as mock_gather,
    ):
        items = [item async for item in adapter.fetch_raw(ctx, limit=50)]

    await adapter.teardown(ctx)
    mock_gather.assert_called_once()
    assert len(items) > 0


# ---------------------------------------------------------------------------
# 5. meesho: JSON path is primary, HTML is fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meesho_json_primary_returns_data():
    """When JSON API returns 200, HTML fallback must NOT be called."""
    adapter = MeeshoAdapter(MeeshoConfig())
    ctx = ScrapeContext()
    await adapter.setup(ctx)

    json_data = _load_json("meesho_trending.json")
    mock_json_resp = MagicMock()
    mock_json_resp.status_code = 200
    mock_json_resp.raise_for_status = MagicMock()
    mock_json_resp.json = MagicMock(return_value=json_data)

    with (
        patch.object(adapter._client, "get", new_callable=AsyncMock, return_value=mock_json_resp),  # type: ignore[union-attr]
        patch.object(adapter, "_fetch_html", new_callable=AsyncMock) as mock_html,
    ):
        items = [item async for item in adapter.fetch_raw(ctx, limit=50)]

    await adapter.teardown(ctx)
    mock_html.assert_not_called()
    assert len(items) == 3


@pytest.mark.asyncio
async def test_meesho_html_fallback_on_401():
    """When JSON API returns 401, the HTML fallback must be invoked."""
    adapter = MeeshoAdapter(MeeshoConfig())
    ctx = ScrapeContext()
    await adapter.setup(ctx)

    mock_401_resp = MagicMock()
    mock_401_resp.status_code = 401
    mock_401_resp.raise_for_status = MagicMock()

    html_items = [
        {
            "title": "Fashion Top",
            "url": "https://meesho.com/product/999",
            "raw_json": {
                "currency": "INR",
                "price_inr": 299.0,
                "order_count": 0,
                "score": 0.0,
                "source": "html_fallback",
            },
        },
    ]

    with (
        patch.object(adapter._client, "get", new_callable=AsyncMock, return_value=mock_401_resp),  # type: ignore[union-attr]
        patch.object(adapter, "_fetch_html", new_callable=AsyncMock, return_value=html_items) as mock_html,
    ):
        items = [item async for item in adapter.fetch_raw(ctx, limit=50)]

    await adapter.teardown(ctx)
    mock_html.assert_called_once()
    assert len(items) == 1


# ---------------------------------------------------------------------------
# 6. Parse round-trips: fixture → parse → signal with currency = "INR"
# ---------------------------------------------------------------------------


def test_amazon_in_parse_currency_inr():
    """ProductSignal from amazon_in must have platform_specific['currency'] == 'INR'."""
    html = _load_text("amazon_in_bestsellers.html")
    items = _parse_amazon_in_page(html, category="electronics")
    assert len(items) >= 1

    adapter = AmazonINAdapter(AmazonINConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(items[0], ctx)
    assert signal is not None
    assert signal.platform_specific.get("currency") == "INR"


def test_flipkart_parse_currency_inr():
    """ProductSignal from flipkart must have platform_specific['currency'] == 'INR'."""
    html = _load_text("flipkart_bestsellers.html")
    items = _parse_flipkart_html(html)
    assert len(items) >= 1

    adapter = FlipkartAdapter(FlipkartConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(items[0], ctx)
    assert signal is not None
    assert signal.platform_specific.get("currency") == "INR"


def test_meesho_parse_currency_inr():
    """ProductSignal from meesho JSON must have platform_specific['currency'] == 'INR'."""
    data = _load_json("meesho_trending.json")
    items = _extract_json_products(data)
    assert len(items) >= 1

    adapter = MeeshoAdapter(MeeshoConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(items[0], ctx)
    assert signal is not None
    assert signal.platform_specific.get("currency") == "INR"


def test_ajio_parse_currency_inr():
    """ProductSignal from ajio must have platform_specific['currency'] == 'INR'."""
    data = _load_json("ajio_search.json")
    items = _extract_ajio_products(data)
    assert len(items) >= 1

    adapter = AjioAdapter(AjioConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(items[0], ctx)
    assert signal is not None
    assert signal.platform_specific.get("currency") == "INR"


def test_indiamart_parse_currency_inr():
    """ProductSignal from indiamart must have platform_specific['currency'] == 'INR'."""
    search_data = [
        {
            "SUBJECT": "Industrial Pump 5HP",
            "SUP_COUNT": 145,
            "GLF": "320",
            "PRICE_RANGE": "₹8,000 - ₹25,000",
        }
    ]
    items = _extract_search_results(search_data, "Industrial Machinery")
    assert len(items) == 1

    adapter = IndiaMartAdapter(IndiaMartConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(items[0], ctx)
    assert signal is not None
    assert signal.platform_specific.get("currency") == "INR"
    assert signal.platform_specific.get("market_type") == "B2B"


# ---------------------------------------------------------------------------
# 7. Fixture parsing correctness
# ---------------------------------------------------------------------------


def test_amazon_in_parse_page_extracts_asins():
    """_parse_amazon_in_page must extract at least 2 products from the fixture."""
    html = _load_text("amazon_in_bestsellers.html")
    items = _parse_amazon_in_page(html, category="electronics")
    assert len(items) >= 2
    for item in items:
        assert item["asin"]
        assert len(item["asin"]) == 10
        assert item["raw_json"]["currency"] == "INR"


def test_flipkart_parse_html_extracts_products():
    """_parse_flipkart_html must extract at least 2 products from the fixture."""
    html = _load_text("flipkart_bestsellers.html")
    items = _parse_flipkart_html(html)
    assert len(items) >= 2
    for item in items:
        assert item["title"]
        assert item["raw_json"]["currency"] == "INR"


def test_meesho_extract_json_products():
    """_extract_json_products must parse all 3 products from the fixture."""
    data = _load_json("meesho_trending.json")
    items = _extract_json_products(data)
    assert len(items) == 3
    for item in items:
        assert item["title"]
        assert item["raw_json"]["currency"] == "INR"
        assert item["raw_json"]["order_count"] > 0


def test_ajio_extract_products():
    """_extract_ajio_products must parse all 3 products from the fixture."""
    data = _load_json("ajio_search.json")
    items = _extract_ajio_products(data)
    assert len(items) == 3
    for item in items:
        assert item["title"]
        assert item["raw_json"]["currency"] == "INR"
        assert item["raw_json"]["brand"]


def test_indiamart_extract_categories_html():
    """_extract_categories_html must return at least 3 categories from the fixture."""
    html = _load_text("indiamart_trade_intel.html")
    categories = _extract_categories_html(html)
    assert len(categories) >= 3
    assert all(isinstance(c, str) and len(c) > 2 for c in categories)


def test_indiamart_extract_search_results():
    """_extract_search_results must parse supplier_count and buyer_inquiry_count."""
    search_data = [
        {
            "SUBJECT": "Industrial Pump 5HP",
            "SUP_COUNT": 145,
            "GLF": "320",
            "PRICE_RANGE": "₹8,000 - ₹25,000",
        },
        {
            "SUBJECT": "CNC Machine Parts",
            "SUP_COUNT": 230,
            "GLF": "180",
            "PRICE_RANGE": "₹500 - ₹5,000",
        },
    ]
    items = _extract_search_results(search_data, "Industrial Machinery")
    assert len(items) == 2
    assert items[0]["raw_json"]["supplier_count"] == 145
    assert items[0]["raw_json"]["buyer_inquiry_count"] == 320
    assert items[0]["raw_json"]["market_type"] == "B2B"
    assert items[0]["raw_json"]["currency"] == "INR"


def test_snapdeal_rss_parse_invalid_xml():
    """_parse_rss_feed must return [] gracefully on invalid XML."""
    result = _parse_rss_feed("not xml at all")
    assert result == []


# ---------------------------------------------------------------------------
# 8. Platform + Tier correctness
# ---------------------------------------------------------------------------


def test_amazon_in_signal_platform_and_tier():
    from aegis.schemas.enums import Platform, SourceTier

    html = _load_text("amazon_in_bestsellers.html")
    items = _parse_amazon_in_page(html, category="electronics")
    assert items

    adapter = AmazonINAdapter(AmazonINConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(items[0], ctx)
    assert signal is not None
    assert signal.platform == Platform.AMAZON_IN
    assert signal.tier == SourceTier.TIER_3_SEARCH


def test_flipkart_signal_platform_and_tier():
    from aegis.schemas.enums import Platform, SourceTier

    html = _load_text("flipkart_bestsellers.html")
    items = _parse_flipkart_html(html)
    assert items

    adapter = FlipkartAdapter(FlipkartConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(items[0], ctx)
    assert signal is not None
    assert signal.platform == Platform.FLIPKART
    assert signal.tier == SourceTier.TIER_3_SEARCH


def test_meesho_signal_platform_and_tier():
    from aegis.schemas.enums import Platform, SourceTier

    data = _load_json("meesho_trending.json")
    items = _extract_json_products(data)
    assert items

    adapter = MeeshoAdapter(MeeshoConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(items[0], ctx)
    assert signal is not None
    assert signal.platform == Platform.MEESHO
    assert signal.tier == SourceTier.TIER_3_SEARCH


def test_ajio_signal_platform_and_tier():
    from aegis.schemas.enums import Platform, SourceTier

    data = _load_json("ajio_search.json")
    items = _extract_ajio_products(data)
    assert items

    adapter = AjioAdapter(AjioConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(items[0], ctx)
    assert signal is not None
    assert signal.platform == Platform.AJIO
    assert signal.tier == SourceTier.TIER_3_SEARCH


def test_indiamart_signal_market_type_b2b():
    search_data = [
        {"SUBJECT": "Steel Rods", "SUP_COUNT": 90, "GLF": "75", "PRICE_RANGE": "₹50/kg"}
    ]
    items = _extract_search_results(search_data, "steel")
    adapter = IndiaMartAdapter(IndiaMartConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(items[0], ctx)
    assert signal is not None
    assert signal.platform_specific.get("market_type") == "B2B"


# ---------------------------------------------------------------------------
# 9. Graceful degradation
# ---------------------------------------------------------------------------


def test_amazon_in_parse_returns_none_for_empty_raw():
    adapter = AmazonINAdapter(AmazonINConfig())
    ctx = ScrapeContext()
    assert adapter.parse({}, ctx) is None


def test_flipkart_parse_returns_none_for_no_title():
    adapter = FlipkartAdapter(FlipkartConfig())
    ctx = ScrapeContext()
    assert adapter.parse({"title": "", "url": "https://www.flipkart.com"}, ctx) is None


def test_meesho_extract_json_returns_empty_on_bad_data():
    assert _extract_json_products(None) == []
    assert _extract_json_products("not a dict") == []
    assert _extract_json_products(42) == []


def test_ajio_extract_products_returns_empty_on_bad_data():
    assert _extract_ajio_products(None) == []
    assert _extract_ajio_products([]) == []


def test_indiamart_extract_search_skips_rows_without_subject():
    data = [{"SUP_COUNT": 10, "GLF": "5"}]  # no SUBJECT
    items = _extract_search_results(data, "test")
    assert items == []


@pytest.mark.asyncio
async def test_nykaa_setup_teardown():
    """Nykaa adapter must create and close httpx client cleanly."""
    adapter = NykaaAdapter(NykaaConfig())
    ctx = ScrapeContext()
    await adapter.setup(ctx)
    assert adapter._client is not None
    await adapter.teardown(ctx)
    assert adapter._client is None


@pytest.mark.asyncio
async def test_snapdeal_returns_empty_on_http_error():
    """Snapdeal must return [] gracefully when both RSS and HTML fail."""
    adapter = SnapdealAdapter(SnapdealConfig())
    ctx = ScrapeContext()
    await adapter.setup(ctx)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock(status_code=503)
        )
    )

    with patch.object(adapter._client, "get", new_callable=AsyncMock, return_value=mock_resp):  # type: ignore[union-attr]
        items = [item async for item in adapter.fetch_raw(ctx, limit=20)]

    await adapter.teardown(ctx)
    assert items == []


# ---------------------------------------------------------------------------
# Nykaa HTML parser and parse() method unit tests
# ---------------------------------------------------------------------------

_NYKAA_ONE_CARD_HTML = """\
<html><body>
<div class="productCard">
  <span class="brandName">Lakme</span>
  <div class="productName">Foundation</div>
  <span class="productPrice">&#8377;499</span>
  <div class="averageRating">4.5</div>
  <span class="reviewCount">1,234</span>
  <a href="/products/lakme-foundation">Link</a>
</div>
</body></html>
"""

_NYKAA_FULL_HREF_HTML = """\
<html><body>
<div class="productCard">
  <div class="productName">Full URL Product</div>
  <a href="https://www.nykaa.com/product/full/123">Link</a>
</div>
</body></html>
"""

_NYKAA_NO_TITLE_HTML = """\
<html><body>
<div class="productCard">
  <span class="productPrice">&#8377;299</span>
</div>
</body></html>
"""


def test_parse_nykaa_html_empty_returns_empty() -> None:
    from aegis.scrape.sources.nykaa import _parse_nykaa_html
    assert _parse_nykaa_html("") == []


def test_parse_nykaa_html_full_card_extracts_product() -> None:
    from aegis.scrape.sources.nykaa import _parse_nykaa_html
    results = _parse_nykaa_html(_NYKAA_ONE_CARD_HTML)
    assert len(results) == 1
    r = results[0]
    assert "Lakme" in r["title"]
    assert "Foundation" in r["title"]
    assert r["raw_json"]["price_inr"] == 499.0
    assert r["raw_json"]["rating"] == 4.5
    assert r["raw_json"]["review_count"] == 1234


def test_parse_nykaa_html_full_href_kept_as_is() -> None:
    from aegis.scrape.sources.nykaa import _parse_nykaa_html
    results = _parse_nykaa_html(_NYKAA_FULL_HREF_HTML)
    assert len(results) == 1
    assert results[0]["url"].startswith("https://www.nykaa.com/product/full/123")


def test_parse_nykaa_html_no_title_skips_card() -> None:
    from aegis.scrape.sources.nykaa import _parse_nykaa_html
    assert _parse_nykaa_html(_NYKAA_NO_TITLE_HTML) == []


def test_nykaa_parse_returns_product_signal() -> None:
    from aegis.schemas.enums import Platform
    raw: dict[str, Any] = {
        "title": "Lakme Foundation",
        "url": "https://www.nykaa.com/products/lakme",
        "scraped_at": "2026-05-19T00:00:00",
        "raw_json": {
            "currency": "INR",
            "brand": "Lakme",
            "price_inr": 499.0,
            "rating": 4.5,
            "review_count": 1234,
            "score": 8.0,
        },
    }
    adapter = NykaaAdapter(NykaaConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.NYKAA
    assert "nykaa" in signal.tags
    assert signal.confidence.completeness == 0.65


def test_nykaa_parse_no_title_returns_none() -> None:
    adapter = NykaaAdapter(NykaaConfig())
    ctx = ScrapeContext()
    result = adapter.parse({"title": "", "url": "https://example.com", "raw_json": {}}, ctx)
    assert result is None


def test_nykaa_parse_no_price_lower_completeness() -> None:
    adapter = NykaaAdapter(NykaaConfig())
    ctx = ScrapeContext()
    raw: dict[str, Any] = {"title": "Test Product", "url": "https://www.nykaa.com/p", "raw_json": {}}
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.confidence.completeness == 0.35


# ---------------------------------------------------------------------------
# Snapdeal RSS and HTML parser unit tests
# ---------------------------------------------------------------------------

_SNAPDEAL_RSS_WITH_PRICE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss><channel>
  <item>
    <title>Sony MDR Headphones</title>
    <link>https://www.snapdeal.com/product/sony-mdr/12345</link>
    <description>Quality sound. Rs. 1,299 only. Limited offer.</description>
  </item>
  <item>
    <title>Another item no price</title>
    <link>https://www.snapdeal.com/product/other/67890</link>
    <description>No price here.</description>
  </item>
</channel></rss>
"""

_SNAPDEAL_RSS_NO_CHANNEL = """<?xml version="1.0"?><rss/>"""

_SNAPDEAL_HTML_ONE_CARD = """\
<html><body>
<div class="product-desc-rating">
  <p class="product-title">Sony Headphones</p>
  <span class="product-price">Rs. 1,299</span>
  <a href="https://www.snapdeal.com/product/sony/123">Link</a>
</div>
</body></html>
"""

_SNAPDEAL_HTML_RELATIVE_HREF = """\
<html><body>
<div class="product-desc-rating">
  <p class="product-title">Budget Earphones</p>
  <a href="/product/earphones/456">Link</a>
</div>
</body></html>
"""


def test_parse_snapdeal_rss_valid_with_price() -> None:
    result = _parse_rss_feed(_SNAPDEAL_RSS_WITH_PRICE)
    assert len(result) == 2
    assert result[0]["title"] == "Sony MDR Headphones"
    assert result[0]["raw_json"]["price_inr"] == 1299.0
    assert result[1]["raw_json"]["price_inr"] is None


def test_parse_snapdeal_rss_no_channel_returns_empty() -> None:
    result = _parse_rss_feed(_SNAPDEAL_RSS_NO_CHANNEL)
    assert result == []


def test_parse_snapdeal_html_empty_returns_empty() -> None:
    from aegis.scrape.sources.snapdeal import _parse_snapdeal_html
    assert _parse_snapdeal_html("") == []


def test_parse_snapdeal_html_full_card_extracts_product() -> None:
    from aegis.scrape.sources.snapdeal import _parse_snapdeal_html
    result = _parse_snapdeal_html(_SNAPDEAL_HTML_ONE_CARD)
    assert len(result) == 1
    assert result[0]["title"] == "Sony Headphones"
    assert result[0]["raw_json"]["price_inr"] == 1299.0
    assert result[0]["url"].startswith("https://www.snapdeal.com/product/sony/123")


def test_parse_snapdeal_html_relative_href_prefixed() -> None:
    from aegis.scrape.sources.snapdeal import _parse_snapdeal_html
    result = _parse_snapdeal_html(_SNAPDEAL_HTML_RELATIVE_HREF)
    assert len(result) == 1
    assert result[0]["url"].startswith("https://www.snapdeal.com/product/earphones/456")


# ---------------------------------------------------------------------------
# SnapdealAdapter.parse() unit tests
# ---------------------------------------------------------------------------


def test_snapdeal_parse_rss_signal_returns_product() -> None:
    from aegis.schemas.enums import Platform
    raw: dict[str, Any] = {
        "title": "Sony MDR Headphones",
        "url": "https://www.snapdeal.com/product/sony-mdr/12345",
        "scraped_at": "2026-05-19T00:00:00",
        "raw_json": {
            "currency": "INR",
            "price_inr": 1299.0,
            "description": "Quality sound headphones",
            "source": "rss",
        },
    }
    adapter = SnapdealAdapter(SnapdealConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.SNAPDEAL
    assert "snapdeal" in signal.tags
    assert signal.confidence.completeness == 0.65


def test_snapdeal_parse_html_signal_returns_product() -> None:
    raw: dict[str, Any] = {
        "title": "Budget Earphones",
        "url": "https://www.snapdeal.com/product/earphones/456",
        "scraped_at": "2026-05-19T00:00:00",
        "raw_json": {"currency": "INR", "price_inr": None, "description": None, "source": "html"},
    }
    adapter = SnapdealAdapter(SnapdealConfig())
    ctx = ScrapeContext()
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.confidence.completeness == 0.35


def test_snapdeal_parse_no_title_returns_none() -> None:
    adapter = SnapdealAdapter(SnapdealConfig())
    ctx = ScrapeContext()
    result = adapter.parse({"title": "", "url": "https://example.com", "raw_json": {}}, ctx)
    assert result is None


def test_join_brand_title_dedupes_brand_prefix() -> None:
    from aegis.scrape.ecommerce_utils import join_brand_title

    # name already starts with brand → no doubling.
    assert join_brand_title("ETUDE", "ETUDE Dear Darling Lip Gloss") == "ETUDE Dear Darling Lip Gloss"
    # case-insensitive prefix dedupe.
    assert join_brand_title("boAt", "boat Rockerz 450") == "boat Rockerz 450"
    # genuine brand + name still joins.
    assert join_brand_title("Nike", "Air Max 90") == "Nike Air Max 90"
    # empty brand returns name alone.
    assert join_brand_title("", "Air Max") == "Air Max"


def test_flipkart_parse_populates_structured_price():
    """Audit P10-1: a parsed price must land in the STRUCTURED price field (not
    just raw_json) and mark the signal as commerce, so price_amount persists and
    the arbitrage engine has real prices."""
    adapter = FlipkartAdapter(FlipkartConfig())
    ctx = ScrapeContext()
    raw = {
        "title": "Wireless Earbuds Pro",
        "url": "https://www.flipkart.com/p/itm123",
        "raw_json": {
            "disc_price": 1299.0,
            "orig_price": 1999.0,
            "currency": "INR",
            "discount_pct": 35.0,
            "score": 10,
        },
    }
    sig = adapter.parse(raw, ctx)
    assert sig is not None
    assert sig.price is not None, "structured price must be populated"
    assert float(sig.price.amount) == 1299.0
    assert sig.price.currency == "INR"
    assert float(sig.price.original_amount) == 1999.0
    assert sig.price.is_on_sale is True
    # tier stays T3_search (platform→tier validator), but price now persists.
    from aegis.schemas.signal import SourceTier

    assert sig.tier == SourceTier.TIER_3_SEARCH


def test_flipkart_parse_without_price_stays_search_tier():
    adapter = FlipkartAdapter(FlipkartConfig())
    ctx = ScrapeContext()
    raw = {"title": "Some Product", "url": "https://www.flipkart.com/p/x", "raw_json": {}}
    sig = adapter.parse(raw, ctx)
    assert sig is not None
    assert sig.price is None
    from aegis.schemas.signal import SourceTier

    assert sig.tier == SourceTier.TIER_3_SEARCH


def test_amazon_in_parse_populates_structured_price():
    """audit P10-1: amazon_in must promote price_inr into the structured price."""
    adapter = AmazonINAdapter(AmazonINConfig())
    ctx = ScrapeContext()
    raw = {
        "title": "USB-C Cable",
        "url": "https://www.amazon.in/dp/B0TEST1234",
        "asin": "B0TEST1234",
        "raw_json": {"price_inr": 499.0, "currency": "INR"},
    }
    sig = adapter.parse(raw, ctx)
    assert sig is not None
    assert sig.price is not None and float(sig.price.amount) == 499.0
    assert sig.price.currency == "INR"


def test_myntra_parse_populates_structured_price():
    from aegis.scrape.sources.myntra import MyntraConfig

    adapter = MyntraAdapter(MyntraConfig())
    ctx = ScrapeContext()
    raw = {
        "title": "Nike Running Shoes",
        "url": "https://www.myntra.com/123",
        "raw_json": {
            "price_inr": 2499.0,
            "orig_price_inr": 3999.0,
            "currency": "INR",
        },
    }
    sig = adapter.parse(raw, ctx)
    assert sig is not None
    assert sig.price is not None and float(sig.price.amount) == 2499.0
    assert float(sig.price.original_amount) == 3999.0
    assert sig.price.is_on_sale is True
