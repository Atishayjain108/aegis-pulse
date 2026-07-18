"""Unit tests for the Google News RSS adapter (coverage + correctness)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from aegis.schemas.enums import Platform
from aegis.scrape.base import ScrapeContext
from aegis.scrape.sources.google_news_rss import (
    GoogleNewsRSSAdapter,
    GoogleNewsRSSConfig,
)

_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>AI chips - Google News</title>
    <item>
      <title>NVIDIA unveils new AI chip</title>
      <link>https://news.google.com/rss/articles/abc?oc=5</link>
      <description>&lt;a href="x"&gt;Some &lt;b&gt;HTML&lt;/b&gt; snippet&lt;/a&gt;</description>
      <pubDate>Wed, 04 Jun 2025 10:00:00 GMT</pubDate>
      <source url="https://reuters.com">Reuters</source>
      <guid>guid-001</guid>
    </item>
    <item>
      <title>Second article</title>
      <link>https://news.google.com/rss/articles/def?oc=5</link>
      <description>plain text</description>
      <pubDate>Wed, 04 Jun 2025 11:00:00 GMT</pubDate>
      <source url="https://bbc.com">BBC</source>
      <guid>guid-002</guid>
    </item>
  </channel>
</rss>"""


def _adapter() -> GoogleNewsRSSAdapter:
    return GoogleNewsRSSAdapter(GoogleNewsRSSConfig())


def _mock_client(adapter: GoogleNewsRSSAdapter, *, content: bytes) -> None:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.content = content
    adapter._client = AsyncMock()
    adapter._client.get = AsyncMock(return_value=resp)


async def test_name_property():
    assert _adapter().name == "google-news-rss"


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
        items = [r async for r in adapter.fetch_raw(ctx, query="AI chips", limit=1)]
    assert len(items) == 1
    assert items[0]["title"] == "NVIDIA unveils new AI chip"
    assert items[0]["_query"] == "AI chips"


async def test_fetch_raw_http_error_degrades(monkeypatch):
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


def test_parse_produces_google_news_signal():
    adapter = _adapter()
    ctx = ScrapeContext()
    raw = {
        "title": "NVIDIA unveils new AI chip",
        "link": "https://news.google.com/rss/articles/abc?oc=5",
        "description": "<a>Some <b>HTML</b> snippet</a>",
        "pubDate": "Wed, 04 Jun 2025 10:00:00 GMT",
        "source": "Reuters",
        "guid": "guid-001",
        "_query": "AI chips",
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.GOOGLE_NEWS
    assert signal.title == "NVIDIA unveils new AI chip"
    # HTML stripped from description.
    assert "<" not in (signal.raw_text or "")
    assert signal.posted_at is not None
    assert signal.platform_specific["source_name"] == "Reuters"


def test_parse_empty_returns_none():
    adapter = _adapter()
    ctx = ScrapeContext()
    assert adapter.parse({"title": "", "link": ""}, ctx) is None


def test_parse_missing_pubdate_lowers_completeness():
    adapter = _adapter()
    ctx = ScrapeContext()
    signal = adapter.parse({"title": "headline only", "link": "https://x.test/a"}, ctx)
    assert signal is not None
    assert signal.confidence.completeness == pytest.approx(0.45)
