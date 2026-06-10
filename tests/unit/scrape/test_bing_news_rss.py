"""Unit tests for the Bing News RSS adapter (coverage + correctness)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from aegis.schemas.enums import Platform
from aegis.scrape.base import ScrapeContext
from aegis.scrape.sources.bing_news_rss import (
    BingNewsRSSAdapter,
    BingNewsRSSConfig,
)

_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>bitcoin - Bing News</title>
    <item>
      <title>Bitcoin hits new high</title>
      <link>https://example.com/btc</link>
      <description>&lt;b&gt;Markets&lt;/b&gt; rally</description>
      <pubDate>Wed, 04 Jun 2025 10:00:00 GMT</pubDate>
      <source>CoinDesk</source>
      <guid>bing-001</guid>
    </item>
    <item>
      <title>Second story</title>
      <link>https://example.com/two</link>
      <description>plain</description>
      <pubDate>Wed, 04 Jun 2025 11:00:00 GMT</pubDate>
      <source>BBC</source>
      <guid>bing-002</guid>
    </item>
  </channel>
</rss>"""


def _adapter() -> BingNewsRSSAdapter:
    return BingNewsRSSAdapter(BingNewsRSSConfig())


def _mock_client(adapter: BingNewsRSSAdapter, *, content: bytes) -> None:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.content = content
    adapter._client = AsyncMock()
    adapter._client.get = AsyncMock(return_value=resp)


async def test_name_property():
    assert _adapter().name == "bing-news-rss"


async def test_setup_teardown_lifecycle():
    adapter = _adapter()
    ctx = ScrapeContext()
    await adapter.setup(ctx)
    assert adapter._client is not None
    await adapter.teardown(ctx)
    assert adapter._client is None


async def test_fetch_raw_requires_setup():
    adapter = _adapter()
    ctx = ScrapeContext()
    with pytest.raises(RuntimeError):
        _ = [r async for r in adapter.fetch_raw(ctx, query="x")]


async def test_fetch_raw_empty_query_yields_nothing():
    adapter = _adapter()
    _mock_client(adapter, content=_RSS)
    ctx = ScrapeContext()
    with patch.object(adapter, "_rate_limit", new_callable=AsyncMock):
        items = [r async for r in adapter.fetch_raw(ctx, query="")]
    assert items == []


async def test_fetch_raw_parses_items_and_respects_limit():
    adapter = _adapter()
    _mock_client(adapter, content=_RSS)
    ctx = ScrapeContext()
    with (
        patch.object(adapter, "_rate_limit", new_callable=AsyncMock),
        patch.object(adapter, "_record_request_metric"),
    ):
        items = [r async for r in adapter.fetch_raw(ctx, query="bitcoin", limit=1)]
    assert len(items) == 1
    assert items[0]["title"] == "Bitcoin hits new high"


async def test_fetch_raw_http_error_degrades():
    adapter = _adapter()
    resp = MagicMock()
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("403", request=MagicMock(), response=MagicMock(status_code=403))
    )
    adapter._client = AsyncMock()
    adapter._client.get = AsyncMock(return_value=resp)
    ctx = ScrapeContext()
    with patch.object(adapter, "_rate_limit", new_callable=AsyncMock):
        items = [r async for r in adapter.fetch_raw(ctx, query="x")]
    assert items == []


async def test_fetch_raw_request_error_degrades():
    adapter = _adapter()
    adapter._client = AsyncMock()
    adapter._client.get = AsyncMock(side_effect=httpx.RequestError("boom", request=MagicMock()))
    ctx = ScrapeContext()
    with patch.object(adapter, "_rate_limit", new_callable=AsyncMock):
        items = [r async for r in adapter.fetch_raw(ctx, query="x")]
    assert items == []


async def test_fetch_raw_xml_parse_error_degrades():
    adapter = _adapter()
    _mock_client(adapter, content=b"<<<not xml>>>")
    ctx = ScrapeContext()
    with patch.object(adapter, "_rate_limit", new_callable=AsyncMock):
        items = [r async for r in adapter.fetch_raw(ctx, query="x")]
    assert items == []


def test_parse_produces_bing_news_signal():
    adapter = _adapter()
    ctx = ScrapeContext()
    raw = {
        "title": "Bitcoin hits new high",
        "link": "https://example.com/btc",
        "description": "<b>Markets</b> rally",
        "pubDate": "Wed, 04 Jun 2025 10:00:00 GMT",
        "source": "CoinDesk",
        "guid": "bing-001",
        "_query": "bitcoin",
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.BING_NEWS
    assert signal.title == "Bitcoin hits new high"
    assert "<" not in (signal.raw_text or "")
    assert signal.posted_at is not None


def test_parse_empty_returns_none():
    adapter = _adapter()
    ctx = ScrapeContext()
    assert adapter.parse({"title": "", "link": ""}, ctx) is None
