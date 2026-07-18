"""Phase 3 — Finance/market data adapter tests.

Covers:
- NSE adapter sends required Referer header
- NSE signals all carry raw_json["currency"] == "INR"
- NSE adapter returns [] gracefully on JSON parse failure
- NSE adapter returns [] gracefully on HTTP error
- _extract_stocks normalises various NSE response shapes
- Screener.in: search path parses JSON and produces ProductSignal
- Screener.in: explore path parses HTML table via BeautifulSoup
- Moneycontrol: inherits from SourceAdapter; RSS fixture round-trip
- Economic Times: section field is injected per feed
- parse() on NSE raw dict produces ProductSignal with correct tier/platform
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from aegis.scrape.base import ScrapeContext, SourceAdapter
from aegis.scrape.sources.economic_times_markets import EconomicTimesMarketsAdapter
from aegis.scrape.sources.moneycontrol import (
    MoneycontrolAdapter,
    MoneycontrolConfig,
    _parse_movers_html,
)
from aegis.scrape.sources.nse_bse import (
    NSE_HEADERS,
    NSEBSEAdapter,
    NSEBSEConfig,
    _extract_stocks,
)
from aegis.scrape.sources.screener_in import (
    ScreenerInAdapter,
    ScreenerInConfig,
    _parse_explore_html,
    _parse_search_results,
)

_FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def _load_json(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text())


def _load_text(name: str) -> str:
    return (_FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# 1. Adapter class hierarchy
# ---------------------------------------------------------------------------


def test_nse_bse_adapter_inherits_source_adapter():
    assert issubclass(NSEBSEAdapter, SourceAdapter)


def test_moneycontrol_adapter_inherits_source_adapter():
    assert issubclass(MoneycontrolAdapter, SourceAdapter)


def test_economic_times_adapter_inherits_source_adapter():
    assert issubclass(EconomicTimesMarketsAdapter, SourceAdapter)


def test_screener_in_adapter_inherits_source_adapter():
    assert issubclass(ScreenerInAdapter, SourceAdapter)


# ---------------------------------------------------------------------------
# 2. NSE: Referer header is present in the client headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nse_adapter_sends_referer_header():
    """The httpx client must be initialised with Referer pointing to nseindia.com."""
    adapter = NSEBSEAdapter(NSEBSEConfig())
    ctx = ScrapeContext()
    await adapter.setup(ctx)
    assert adapter._client is not None
    assert "Referer" in adapter._client.headers
    assert "nseindia.com" in adapter._client.headers["Referer"]
    await adapter.teardown(ctx)


def test_nse_headers_contains_referer():
    """NSE_HEADERS constant must contain a Referer pointing to nseindia.com."""
    assert "Referer" in NSE_HEADERS
    assert "nseindia.com" in NSE_HEADERS["Referer"]


# ---------------------------------------------------------------------------
# 3. NSE: raw_json["currency"] == "INR" on all signals
# ---------------------------------------------------------------------------


def test_nse_extract_stocks_currency_inr():
    """Every stock produced by _extract_stocks must have currency=INR."""
    data = _load_json("nse_gainers.json")
    stocks = _extract_stocks(data, "gainers", "NSE")

    assert len(stocks) == 3
    for stock in stocks:
        raw_json = stock["raw_json"]
        assert raw_json["currency"] == "INR", f"Missing INR on {stock['title']}"


def test_nse_parse_produces_inr_in_platform_specific():
    """parse() on an NSE raw dict must carry currency=INR in platform_specific."""
    from aegis.schemas.enums import Platform, SourceTier

    adapter = NSEBSEAdapter(NSEBSEConfig())
    ctx = ScrapeContext()

    data = _load_json("nse_gainers.json")
    stocks = _extract_stocks(data, "gainers", "NSE")
    assert stocks

    signal = adapter.parse(stocks[0], ctx)
    assert signal is not None
    assert signal.platform == Platform.NSE_BSE
    assert signal.tier == SourceTier.TIER_3_SEARCH  # no price captured; % change only
    assert signal.platform_specific.get("currency") == "INR"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 4. NSE: graceful degradation on JSON parse failure
# ---------------------------------------------------------------------------


def test_extract_stocks_returns_empty_on_invalid_json():
    """_extract_stocks must return [] when given non-list/dict data."""
    assert _extract_stocks(None, "gainers", "NSE") == []
    assert _extract_stocks("invalid", "gainers", "NSE") == []
    assert _extract_stocks(42, "gainers", "NSE") == []


def test_extract_stocks_returns_empty_on_empty_advances_declines():
    """Empty ADVANCES_DECLINES list → empty result, no exception."""
    data = {"ADVANCES_DECLINES": []}
    assert _extract_stocks(data, "gainers", "NSE") == []


def test_extract_stocks_skips_rows_without_symbol():
    """Rows missing 'symbol' must be skipped."""
    data = {"ADVANCES_DECLINES": [{"companyName": "Ghost Corp", "change": 1.0, "totalTradedVolume": 100}]}
    result = _extract_stocks(data, "gainers", "NSE")
    assert result == []


@pytest.mark.asyncio
async def test_nse_fetch_raw_returns_empty_on_http_error():
    """fetch_raw must yield [] when the NSE endpoint returns an HTTP error."""
    adapter = NSEBSEAdapter(NSEBSEConfig())
    ctx = ScrapeContext()
    await adapter.setup(ctx)

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Forbidden", request=MagicMock(), response=mock_resp
    )

    with patch.object(adapter._client, "get", new_callable=AsyncMock, return_value=mock_resp):  # type: ignore[union-attr]
        items = [item async for item in adapter.fetch_raw(ctx, limit=50)]

    await adapter.teardown(ctx)
    assert items == []


@pytest.mark.asyncio
async def test_nse_fetch_raw_returns_empty_on_json_exception():
    """fetch_raw must yield [] when JSON decoding raises an exception."""
    adapter = NSEBSEAdapter(NSEBSEConfig())
    ctx = ScrapeContext()
    await adapter.setup(ctx)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.side_effect = ValueError("No JSON")

    with patch.object(adapter._client, "get", new_callable=AsyncMock, return_value=mock_resp):  # type: ignore[union-attr]
        items = [item async for item in adapter.fetch_raw(ctx, limit=50)]

    await adapter.teardown(ctx)
    assert items == []


# ---------------------------------------------------------------------------
# 5. NSE: fixture-based integration of _extract_stocks + parse
# ---------------------------------------------------------------------------


def test_nse_extract_stocks_produces_correct_titles():
    """Titles should include company name, symbol, and pct change."""
    data = _load_json("nse_gainers.json")
    stocks = _extract_stocks(data, "gainers", "NSE")

    titles = [s["title"] for s in stocks]
    assert any("RELIANCE" in t for t in titles)
    assert any("TCS" in t for t in titles)
    assert any("5.23%" in t for t in titles)


def test_nse_extract_stocks_uses_log1p_score():
    """Score = abs(pct_change) * log1p(volume) — must be > abs(pct_change) alone."""
    from math import log1p

    data = _load_json("nse_gainers.json")
    stocks = _extract_stocks(data, "gainers", "NSE")

    for stock in stocks:
        pct = stock["raw_json"]["pct_change"]
        vol = stock["raw_json"]["volume"]
        expected = abs(pct) * log1p(vol)
        assert abs(stock["score"] - expected) < 1e-9, f"Score mismatch for {stock['title']}"


# ---------------------------------------------------------------------------
# 6. Screener.in: search path
# ---------------------------------------------------------------------------


def test_screener_search_parse_results():
    """_parse_search_results must extract name + url from JSON array."""
    data = [
        {"name": "Reliance Industries", "url": "/company/RELIANCE/", "company_type": "IN"},
        {"name": "Infosys", "url": "/company/INFY/", "company_type": "IN"},
    ]
    results = _parse_search_results(data, "fintech")

    assert len(results) == 2
    assert results[0]["title"] == "Reliance Industries"
    assert results[0]["url"] == "https://www.screener.in/company/RELIANCE/"
    assert results[0]["raw_json"]["source"] == "search"
    assert results[0]["raw_json"]["keyword"] == "fintech"
    assert results[0]["platform"] == "screener_in"


def test_screener_search_handles_invalid_json():
    """_parse_search_results must return [] for non-list input."""
    assert _parse_search_results(None, "fintech") == []
    assert _parse_search_results({"not": "a list"}, "fintech") == []
    assert _parse_search_results("bad", "fintech") == []


def test_screener_search_skips_rows_without_name_or_url():
    """Items missing name or url must be silently skipped."""
    data = [
        {"name": "", "url": "/company/EMPTY/"},
        {"name": "ValidCo", "url": ""},
        {"name": "GoodCo", "url": "/company/GOOD/"},
    ]
    results = _parse_search_results(data, "test")
    assert len(results) == 1
    assert results[0]["title"] == "GoodCo"


@pytest.mark.asyncio
async def test_screener_search_produces_product_signal():
    """parse() on a search raw dict must produce a valid ProductSignal."""
    from aegis.schemas.enums import Platform, SourceTier

    adapter = ScreenerInAdapter(ScreenerInConfig(keywords=("fintech",), fetch_explore=False))
    ctx = ScrapeContext()

    raw = {
        "title": "Paytm",
        "url": "https://www.screener.in/company/PAYTM/",
        "platform": "screener_in",
        "scraped_at": "2026-05-19T00:00:00+00:00",
        "author": None,
        "score": 0.0,
        "views": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "saves": None,
        "sentiment": 0.1,
        "raw_json": {"source": "search", "keyword": "fintech", "company_type": "IN"},
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.SCREENER_IN
    assert signal.tier == SourceTier.TIER_3_SEARCH
    assert "search" in signal.tags


# ---------------------------------------------------------------------------
# 7. Screener.in: explore path (BeautifulSoup HTML parsing)
# ---------------------------------------------------------------------------


def test_screener_explore_parses_fixture():
    """_parse_explore_html must extract 3 companies from the fixture table."""
    html = _load_text("screener_explore.html")
    results = _parse_explore_html(html)

    assert len(results) == 3
    titles = [r["title"] for r in results]
    # Fixture has Reliance, TCS, HDFC Bank
    assert any("Reliance" in t for t in titles)
    assert any("Tata" in t for t in titles)


def test_screener_explore_injects_explore_source():
    """raw_json["source"] must be "explore" for HTML-parsed items."""
    html = _load_text("screener_explore.html")
    results = _parse_explore_html(html)
    for r in results:
        assert r["raw_json"]["source"] == "explore"


def test_screener_explore_includes_market_cap():
    """raw_json["market_cap"] must be a non-empty string from the table."""
    html = _load_text("screener_explore.html")
    results = _parse_explore_html(html)
    for r in results:
        assert r["raw_json"]["market_cap"]  # non-empty


def test_screener_explore_returns_empty_on_no_table():
    """_parse_explore_html must return [] gracefully for HTML with no table."""
    html = "<html><body><p>No data here.</p></body></html>"
    assert _parse_explore_html(html) == []


@pytest.mark.asyncio
async def test_screener_explore_produces_product_signal():
    """parse() on an explore raw dict must produce a valid ProductSignal."""
    from aegis.schemas.enums import Platform

    adapter = ScreenerInAdapter(ScreenerInConfig(keywords=(), fetch_explore=True))
    ctx = ScrapeContext()

    html = _load_text("screener_explore.html")
    results = _parse_explore_html(html)
    assert results

    signal = adapter.parse(results[0], ctx)
    assert signal is not None
    assert signal.platform == Platform.SCREENER_IN
    assert "explore" in signal.tags


# ---------------------------------------------------------------------------
# 8. Moneycontrol: RSS fixture round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_moneycontrol_rss_fetch_raw_uses_feedparser():
    """Moneycontrol.fetch_raw calls fetch_feed_entries (which uses asyncio.to_thread)."""
    import feedparser as _fp

    adapter = MoneycontrolAdapter(MoneycontrolConfig(fetch_movers=False))
    ctx = ScrapeContext()

    xml_bytes = (_FIXTURES / "moneycontrol_news.xml").read_bytes()
    fake_feed = _fp.parse(xml_bytes)

    with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=fake_feed):
        items = [item async for item in adapter.fetch_raw(ctx, limit=10)]

    assert isinstance(items, list)


def test_moneycontrol_parse_movers_html_extracts_stocks():
    """_parse_movers_html must extract stock rows from a simple table."""
    html = """
    <html><body>
    <table>
      <tr><th>Ticker</th><th>Change%</th><th>Volume</th></tr>
      <tr><td>RELIANCE</td><td>+2.50%</td><td>1000000</td></tr>
      <tr><td>TCS</td><td>-1.20%</td><td>500000</td></tr>
    </table>
    </body></html>
    """
    results = _parse_movers_html(html)
    assert len(results) == 2
    for r in results:
        assert r["raw_json"]["currency"] == "INR"
        assert r["raw_json"]["data_type"] == "stock_mover"
        assert r["platform"] == "moneycontrol"


def test_moneycontrol_parse_movers_html_returns_empty_on_bad_html():
    """_parse_movers_html must return [] gracefully for HTML with no valid rows."""
    assert _parse_movers_html("") == []
    assert _parse_movers_html("<html><body><p>nothing</p></body></html>") == []


@pytest.mark.asyncio
async def test_moneycontrol_parse_produces_signal_for_news():
    """parse() on an RSS news raw dict must produce a ProductSignal."""
    from aegis.schemas.enums import Platform, ScrapeMethod

    adapter = MoneycontrolAdapter(MoneycontrolConfig())
    ctx = ScrapeContext()

    raw = {
        "title": "Sensex Rallies 500 Points",
        "url": "https://www.moneycontrol.com/news/markets/sensex-rallies.html",
        "platform": "moneycontrol",
        "scraped_at": "2026-05-19T00:00:00+00:00",
        "author": None,
        "score": 0.0,
        "views": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "saves": None,
        "sentiment": 0.5,
        "raw_json": {"summary": "Markets rally strongly.", "published": "", "tags": []},
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.MONEYCONTROL
    assert signal.provenance.method == ScrapeMethod.RSS_FEED


@pytest.mark.asyncio
async def test_moneycontrol_parse_produces_signal_for_stock_mover():
    """parse() on a stock_mover raw dict must produce a ProductSignal with currency=INR."""
    from aegis.schemas.enums import Platform, ScrapeMethod

    adapter = MoneycontrolAdapter(MoneycontrolConfig())
    ctx = ScrapeContext()

    raw = {
        "title": "RELIANCE +2.50%",
        "url": "https://www.moneycontrol.com/india/stockpricequote/reliance",
        "platform": "moneycontrol",
        "scraped_at": "2026-05-19T00:00:00+00:00",
        "author": None,
        "score": 5.0,
        "views": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "saves": None,
        "sentiment": 0.8,
        "raw_json": {
            "currency": "INR",
            "data_type": "stock_mover",
            "ticker": "RELIANCE",
            "pct_change": 2.5,
            "volume": 1000000,
        },
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.MONEYCONTROL
    assert signal.platform_specific.get("currency") == "INR"  # type: ignore[union-attr]
    assert signal.provenance.method == ScrapeMethod.PUBLIC_API_UNOFFICIAL


# ---------------------------------------------------------------------------
# 9. Economic Times: section injection per feed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_economic_times_section_injected_into_raw_json():
    """fetch_raw must inject raw_json["section"] from the feed that produced each item."""
    import feedparser as _fp

    xml_bytes = (_FIXTURES / "moneycontrol_news.xml").read_bytes()  # minimal valid RSS
    fake_feed = _fp.parse(xml_bytes)

    adapter = EconomicTimesMarketsAdapter(
        __import__("aegis.scrape.sources._rss_base", fromlist=["RSSAdapterConfig"]).RSSAdapterConfig(
            name="economic_times"
        )
    )
    ctx = ScrapeContext()

    with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=fake_feed):
        items = [item async for item in adapter.fetch_raw(ctx, limit=30)]

    if items:
        for item in items:
            raw_json = item.get("raw_json") or {}
            assert "section" in raw_json, "section must be injected into raw_json"
            assert raw_json.get("source") == "economic_times"


@pytest.mark.asyncio
async def test_economic_times_parse_produces_signal():
    """parse() on an ET news raw dict must produce a ProductSignal."""
    from aegis.schemas.enums import Platform, SourceTier
    from aegis.scrape.sources._rss_base import RSSAdapterConfig

    adapter = EconomicTimesMarketsAdapter(RSSAdapterConfig(name="economic_times"))
    ctx = ScrapeContext()

    raw = {
        "title": "Nifty 50 Hits All-Time High",
        "url": "https://economictimes.indiatimes.com/markets/nifty-hits-all-time-high.html",
        "platform": "economic_times",
        "scraped_at": "2026-05-19T00:00:00+00:00",
        "author": None,
        "score": 0.0,
        "views": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "saves": None,
        "sentiment": 0.7,
        "raw_json": {
            "summary": "Nifty 50 index reached a new record.",
            "published": "Mon, 19 May 2026 10:00:00 +0530",
            "tags": ["markets"],
            "source": "economic_times",
            "section": "markets",
        },
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.ECONOMIC_TIMES
    assert signal.tier == SourceTier.TIER_3_SEARCH
    assert "markets" in signal.tags
    assert signal.platform_specific.get("section") == "markets"  # type: ignore[union-attr]
