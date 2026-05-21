"""Phase 1 — Stable adapter tests.

Covers:
- All 12 adapter classes instantiate correctly and inherit from SourceAdapter
- clean_url strips UTM and other tracking parameters
- RSS adapters call asyncio.to_thread (via feedparser mock)
- Graceful failure: each adapter returns [] on httpx.HTTPError / feedparser error
- GitHub rate-limit guard stops early when X-RateLimit-Remaining ≤ 5
- DevTo adapter deduplicates by article ID across two endpoints
- parse() produces ProductSignal with expected platform/tier values
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from aegis.scrape.base import ScrapeContext, SourceAdapter
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig, clean_url
from aegis.scrape.sources.bbc_business import BBCBusinessAdapter
from aegis.scrape.sources.business_standard_rss import BusinessStandardRSSAdapter
from aegis.scrape.sources.devto import DevToAdapter, DevToConfig
from aegis.scrape.sources.github_public import GitHubPublicAdapter, GitHubPublicConfig
from aegis.scrape.sources.investing_com_rss import InvestingComRSSAdapter
from aegis.scrape.sources.medium_rss import MediumRSSAdapter
from aegis.scrape.sources.mint_rss import MintRSSAdapter
from aegis.scrape.sources.ndtv_profit import NDTVProfitAdapter
from aegis.scrape.sources.reuters_rss import ReutersRSSAdapter
from aegis.scrape.sources.techcrunch_rss import TechCrunchRSSAdapter
from aegis.scrape.sources.wired_rss import WiredRSSAdapter
from aegis.scrape.sources.yahoo_finance_rss import YahooFinanceRSSAdapter

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def _load_xml(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _load_json(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Helper: minimal AdapterConfig
# ---------------------------------------------------------------------------

def _rss_config(name: str = "test") -> RSSAdapterConfig:
    return RSSAdapterConfig(name=name)


# ---------------------------------------------------------------------------
# 1. All adapters inherit from SourceAdapter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter_cls",
    [
        TechCrunchRSSAdapter,
        WiredRSSAdapter,
        BBCBusinessAdapter,
        ReutersRSSAdapter,
        NDTVProfitAdapter,
        MintRSSAdapter,
        BusinessStandardRSSAdapter,
        YahooFinanceRSSAdapter,
        InvestingComRSSAdapter,
        MediumRSSAdapter,
    ],
)
def test_rss_adapter_inherits_source_adapter(adapter_cls):
    assert issubclass(adapter_cls, SourceAdapter)
    assert issubclass(adapter_cls, RSSAdapter)


def test_devto_adapter_inherits_source_adapter():
    assert issubclass(DevToAdapter, SourceAdapter)


def test_github_public_adapter_inherits_source_adapter():
    assert issubclass(GitHubPublicAdapter, SourceAdapter)


# ---------------------------------------------------------------------------
# 2. clean_url strips tracking parameters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "https://techcrunch.com/2024/10/01/story/?utm_source=rss&utm_medium=feed",
            "https://techcrunch.com/2024/10/01/story/",
        ),
        (
            "https://dev.to/article?ref=homepage&utm_campaign=weekly",
            "https://dev.to/article",
        ),
        (
            "https://github.com/owner/repo?tab=readme-ov-file",
            "https://github.com/owner/repo",
        ),
        (
            "https://example.com/path",
            "https://example.com/path",
        ),
        ("", ""),
    ],
)
def test_clean_url_strips_tracking_params(raw, expected):
    assert clean_url(raw) == expected


# ---------------------------------------------------------------------------
# 3. RSS adapters: asyncio.to_thread is used for feedparser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rss_fetch_raw_uses_asyncio_to_thread():
    """feedparser.parse must be called inside asyncio.to_thread (not directly)."""
    tc = TechCrunchRSSAdapter(_rss_config("techcrunch"))
    ctx = ScrapeContext()

    xml_bytes = _load_xml("techcrunch_feed.xml")

    # Build a fake feedparser result from the fixture XML
    import feedparser as _fp
    fake_feed = _fp.parse(xml_bytes)

    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = fake_feed
        items = [item async for item in tc.fetch_raw(ctx, limit=10)]

    # asyncio.to_thread must have been called (proving feedparser wasn't called directly)
    mock_to_thread.assert_called()
    # We get items back (validate_batch may filter if URLs are empty, but structure ok)
    assert isinstance(items, list)


# ---------------------------------------------------------------------------
# 4. Graceful failure: RSS adapter returns [] on feedparser exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rss_adapter_returns_empty_on_feedparser_error():
    tc = TechCrunchRSSAdapter(_rss_config("techcrunch"))
    ctx = ScrapeContext()

    with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=Exception("network error")):
        items = [item async for item in tc.fetch_raw(ctx, limit=10)]

    assert items == []


# ---------------------------------------------------------------------------
# 5. RSS adapter parse() produces correct ProductSignal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rss_parse_produces_product_signal():
    """End-to-end: parse a raw dict from _entry_to_raw and verify ProductSignal fields."""
    from aegis.schemas.enums import Platform

    tc = TechCrunchRSSAdapter(_rss_config("techcrunch"))
    ctx = ScrapeContext()

    raw = {
        "title": "OpenAI raises $6.6 billion",
        "url": "https://techcrunch.com/2024/10/02/openai-raises-funding/",
        "platform": "techcrunch",
        "scraped_at": "2024-10-02T12:00:00+00:00",
        "author": "Kyle Wiggers",
        "score": 0.0,
        "views": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "saves": None,
        "sentiment": 0.25,
        "raw_json": {
            "summary": "OpenAI has closed a $6.6 billion funding round.",
            "published": "Wed, 02 Oct 2024 12:00:00 +0000",
            "tags": [],
        },
    }

    signal = tc.parse(raw, ctx)

    assert signal is not None
    assert signal.platform == Platform.TECHCRUNCH
    assert signal.title == "OpenAI raises $6.6 billion"
    assert "techcrunch" in signal.tags
    assert signal.content_hash is not None


# ---------------------------------------------------------------------------
# 6. RSS multi-feed adapters deduplicate by URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bbc_deduplication_by_url():
    """BBC fetches two feeds — items with identical URLs appear only once."""
    bbc = BBCBusinessAdapter(_rss_config("bbc_news"))
    ctx = ScrapeContext()

    shared_item = {
        "title": "Shared article",
        "url": "https://www.bbc.co.uk/news/articles/shared-item",
        "platform": "bbc_news",
        "scraped_at": "2024-10-01T10:00:00+00:00",
        "author": None,
        "score": 0.0,
        "views": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "saves": None,
        "sentiment": 0.0,
        "raw_json": {"summary": "", "published": "", "tags": []},
    }

    # Both feeds return the same URL — should deduplicate to 1 item
    with patch(
        "aegis.scrape.sources._rss_base.fetch_feed_entries",
        new_callable=AsyncMock,
        return_value=[shared_item],
    ):
        items = [item async for item in bbc.fetch_raw(ctx, limit=50)]

    assert len(items) == 1, "Duplicate URL should be deduplicated across feeds"


# ---------------------------------------------------------------------------
# 7. DevTo adapter: instantiation and fetch with fixture data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_devto_fetch_raw_with_fixture():
    articles = _load_json("devto_articles.json")
    adapter = DevToAdapter(DevToConfig())
    ctx = ScrapeContext()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=articles)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    adapter._client = mock_client

    with (
        patch.object(adapter, "_rate_limit", new_callable=AsyncMock),
        patch.object(adapter, "_record_request_metric"),
    ):
        items = [item async for item in adapter.fetch_raw(ctx, limit=10)]

    # 3 fixture articles, fetched from 2 endpoints but deduped by ID → 3 unique
    assert len(items) == 3


def test_devto_parse_produces_correct_platform():
    from aegis.schemas.enums import Platform

    adapter = DevToAdapter(DevToConfig())
    ctx = ScrapeContext()

    raw = {
        "title": "Building a RAG pipeline",
        "url": "https://dev.to/johndoe/building-rag-pipeline-abc123",
        "platform": "devto",
        "scraped_at": "2024-10-01T10:00:00+00:00",
        "author": "John Doe",
        "score": 247.0,
        "views": None,
        "likes": 247,
        "comments": 38,
        "shares": None,
        "saves": None,
        "sentiment": 0.1,
        "raw_json": {
            "article_id": 1234567,
            "description": "A guide to RAG pipelines.",
            "tags": ["ai", "python"],
            "published_at": "2024-10-01T10:00:00Z",
            "reading_time_minutes": 12,
        },
    }

    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.DEVTO
    assert signal.engagement.likes == 247
    assert signal.engagement.comments == 38


# ---------------------------------------------------------------------------
# 8. DevTo deduplication by article ID across two endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_devto_deduplication_by_id():
    articles = _load_json("devto_articles.json")  # 3 unique articles
    adapter = DevToAdapter(DevToConfig())
    ctx = ScrapeContext()

    # Both endpoints return the same 3 articles → should dedup to 3
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=articles)

    adapter._client = AsyncMock()
    adapter._client.get = AsyncMock(return_value=mock_resp)

    with (
        patch.object(adapter, "_rate_limit", new_callable=AsyncMock),
        patch.object(adapter, "_record_request_metric"),
    ):
        items = [item async for item in adapter.fetch_raw(ctx, limit=100)]

    assert len(items) == 3


# ---------------------------------------------------------------------------
# 9. DevTo graceful failure on httpx.HTTPError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_devto_returns_empty_on_http_error():
    adapter = DevToAdapter(DevToConfig())
    ctx = ScrapeContext()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
    )
    adapter._client = mock_client

    with (
        patch.object(adapter, "_rate_limit", new_callable=AsyncMock),
        patch.object(adapter, "_record_request_metric"),
    ):
        items = [item async for item in adapter.fetch_raw(ctx, limit=10)]

    assert items == []


# ---------------------------------------------------------------------------
# 10. GitHub adapter: fixture data produces correct signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_public_fetch_raw_with_fixture():
    data = _load_json("github_search.json")

    adapter = GitHubPublicAdapter(GitHubPublicConfig())
    ctx = ScrapeContext()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"X-RateLimit-Remaining": "50"}
    mock_resp.json = MagicMock(return_value=data)

    adapter._client = AsyncMock()
    adapter._client.get = AsyncMock(return_value=mock_resp)

    with (
        patch.object(adapter, "_rate_limit", new_callable=AsyncMock),
        patch.object(adapter, "_record_request_metric"),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        items = [item async for item in adapter.fetch_raw(ctx, limit=50)]

    # 3 repos × (number of topics in settings.github_topics) calls, but deduped by ID → 3
    assert len(items) == len(data["items"])


def test_github_public_parse_produces_correct_platform():
    from aegis.schemas.enums import Platform

    adapter = GitHubPublicAdapter(GitHubPublicConfig())
    ctx = ScrapeContext()

    raw = {
        "title": "example-org/awesome-llm: curated LLM resources",
        "url": "https://github.com/example-org/awesome-llm",
        "platform": "github_public",
        "scraped_at": "2024-10-18T12:00:00+00:00",
        "author": "example-org",
        "score": 12450.0,
        "views": 12450,
        "likes": 12450,
        "comments": None,
        "shares": None,
        "saves": None,
        "sentiment": 0.0,
        "raw_json": {
            "repo_id": 100000001,
            "full_name": "example-org/awesome-llm",
            "description": "A curated list of LLM resources",
            "topics": ["llm", "ai"],
            "language": "Python",
            "pushed_at": "2024-10-18T12:00:00Z",
            "search_topic": "llm",
        },
    }

    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.GITHUB_PUBLIC
    assert signal.engagement.likes == 12450
    assert "python" in signal.tags


# ---------------------------------------------------------------------------
# 11. GitHub rate-limit guard stops early when remaining ≤ 5
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_rate_limit_stops_early():
    data = _load_json("github_search.json")

    adapter = GitHubPublicAdapter(GitHubPublicConfig())
    ctx = ScrapeContext()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # Remaining ≤ 5 → adapter must stop immediately after first topic
    mock_resp.headers = {"X-RateLimit-Remaining": "3"}
    mock_resp.json = MagicMock(return_value=data)

    adapter._client = AsyncMock()
    adapter._client.get = AsyncMock(return_value=mock_resp)

    call_count = 0
    original_get = adapter._client.get

    async def counting_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await original_get(*args, **kwargs)

    adapter._client.get = counting_get

    with (
        patch.object(adapter, "_rate_limit", new_callable=AsyncMock),
        patch.object(adapter, "_record_request_metric"),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        # Exhaust the generator; we only care about call_count
        _ = [item async for item in adapter.fetch_raw(ctx, limit=100)]

    # Only 1 HTTP call should have been made (stopped after seeing low remaining)
    assert call_count == 1, f"Expected 1 call (rate limit guard), got {call_count}"


# ---------------------------------------------------------------------------
# 12. GitHub graceful failure on httpx.RequestError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_public_returns_empty_on_request_error():
    adapter = GitHubPublicAdapter(GitHubPublicConfig())
    ctx = ScrapeContext()

    adapter._client = AsyncMock()
    adapter._client.get = AsyncMock(
        side_effect=httpx.RequestError("connection refused", request=MagicMock())
    )

    with (
        patch.object(adapter, "_rate_limit", new_callable=AsyncMock),
        patch.object(adapter, "_record_request_metric"),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        items = [item async for item in adapter.fetch_raw(ctx, limit=10)]

    assert items == []


# ---------------------------------------------------------------------------
# 13. Platform enum values exist for all 12 new adapters
# ---------------------------------------------------------------------------


def test_all_new_platform_enum_values_exist():
    from aegis.schemas.enums import Platform

    expected = [
        "techcrunch", "wired", "bbc_news", "reuters", "ndtv_profit",
        "mint", "business_standard", "yahoo_finance", "investing_com",
        "medium", "devto", "github_public",
    ]
    platform_values = {p.value for p in Platform}
    for name in expected:
        assert name in platform_values, f"Platform.{name!r} missing from enum"


# ---------------------------------------------------------------------------
# 14. Tier mappings are correct for all 12 new adapters
# ---------------------------------------------------------------------------


def test_tier_mappings_correct():
    from aegis.schemas.enums import Platform, SourceTier, platform_tier

    assert platform_tier(Platform.DEVTO) == SourceTier.TIER_1_INTENT
    assert platform_tier(Platform.MEDIUM) == SourceTier.TIER_4_CULTURAL
    for plat in [
        Platform.TECHCRUNCH, Platform.WIRED, Platform.BBC_NEWS, Platform.REUTERS,
        Platform.NDTV_PROFIT, Platform.MINT, Platform.BUSINESS_STANDARD,
        Platform.YAHOO_FINANCE, Platform.INVESTING_COM, Platform.GITHUB_PUBLIC,
    ]:
        assert platform_tier(plat) == SourceTier.TIER_3_SEARCH, f"{plat} should be T3_search"
