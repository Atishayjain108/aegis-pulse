"""Tests for adapter fetch_raw paths using mocked HTTP clients."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.scrape.base import ScrapeContext


def ctx() -> ScrapeContext:
    return ScrapeContext()


# ---------------------------------------------------------------------------
# HackerNews fetch_raw mock
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hn_fetch_raw_yields_items():
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "hits": [
            {"objectID": "1", "title": "Post 1", "points": 100, "num_comments": 10},
            {"objectID": "2", "title": "Post 2", "points": 50, "num_comments": 5},
        ],
        "page": 0,
        "nbPages": 1,
    }

    adapter = HackerNewsAdapter(HackerNewsConfig())
    await adapter.setup(ctx())  # sets up httpx client

    with patch.object(adapter._client, "get", return_value=mock_response):
        items = [item async for item in adapter.fetch_raw(ctx(), limit=10)]

    assert len(items) == 2
    assert items[0]["objectID"] == "1"
    await adapter.teardown(ctx())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hn_fetch_raw_handles_429():
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    mock_response = MagicMock()
    mock_response.status_code = 429

    adapter = HackerNewsAdapter(HackerNewsConfig())
    await adapter.setup(ctx())

    with patch.object(adapter._client, "get", return_value=mock_response):
        items = [item async for item in adapter.fetch_raw(ctx(), limit=10)]

    assert items == []
    await adapter.teardown(ctx())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hn_fetch_raw_empty_hits():
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"hits": [], "page": 0, "nbPages": 1}

    adapter = HackerNewsAdapter(HackerNewsConfig())
    await adapter.setup(ctx())

    with patch.object(adapter._client, "get", return_value=mock_response):
        items = [item async for item in adapter.fetch_raw(ctx(), limit=10)]

    assert items == []
    await adapter.teardown(ctx())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hn_setup_teardown():
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    adapter = HackerNewsAdapter(HackerNewsConfig())
    c = ctx()
    await adapter.setup(c)
    assert adapter._client is not None
    await adapter.teardown(c)
    assert adapter._client is None


# ---------------------------------------------------------------------------
# TikTok fetch_raw mock
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tiktok_fetch_raw_hashtags():
    from aegis.scrape.sources.tiktok import TikTokAdapter, TikTokConfig

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "list": [
                {"hashtag_id": "h1", "hashtag_name": "TrendingA", "video_views": 1000000},
                {"hashtag_id": "h2", "hashtag_name": "TrendingB", "video_views": 500000},
            ]
        }
    }

    adapter = TikTokAdapter(TikTokConfig(fetch_videos=False))
    await adapter.setup(ctx())

    with patch.object(adapter._client, "get", return_value=mock_resp):
        items = [item async for item in adapter.fetch_raw(ctx(), limit=10)]

    assert len(items) == 2
    assert items[0]["type"] == "hashtag"
    await adapter.teardown(ctx())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tiktok_fetch_raw_videos():
    from aegis.scrape.sources.tiktok import TikTokAdapter, TikTokConfig

    hashtag_resp = MagicMock()
    hashtag_resp.status_code = 200
    hashtag_resp.raise_for_status = MagicMock()
    hashtag_resp.json.return_value = {"data": {"list": []}}

    video_resp = MagicMock()
    video_resp.status_code = 200
    video_resp.raise_for_status = MagicMock()
    video_resp.json.return_value = {
        "data": {
            "list": [
                {
                    "item_id": "vid001",
                    "author_name": "creator",
                    "video_description": "test video",
                    "play_count": 99999,
                    "digg_count": 1000,
                }
            ]
        }
    }

    adapter = TikTokAdapter(TikTokConfig(fetch_videos=True))
    await adapter.setup(ctx())

    responses = [hashtag_resp, video_resp]
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        r = responses[call_count]
        call_count += 1
        return r

    with patch.object(adapter._client, "get", side_effect=side_effect):
        items = [item async for item in adapter.fetch_raw(ctx(), limit=10)]

    assert len(items) == 1
    assert items[0]["type"] == "video"
    await adapter.teardown(ctx())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tiktok_setup_teardown():
    from aegis.scrape.sources.tiktok import TikTokAdapter, TikTokConfig

    adapter = TikTokAdapter(TikTokConfig())
    c = ctx()
    await adapter.setup(c)
    assert adapter._client is not None
    await adapter.teardown(c)
    assert adapter._client is None


# ---------------------------------------------------------------------------
# Pinterest fetch_raw mock
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pinterest_fetch_raw_yields_pins():
    from aegis.scrape.sources.pinterest import PinterestAdapter, PinterestConfig

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "resource_response": {
            "data": {
                "results": [
                    {"id": "pin1", "description": "Pink roses"},
                    {"id": "pin2", "description": "Sunset vibes"},
                ],
                "bookmark": "-end-",
            }
        }
    }

    adapter = PinterestAdapter(PinterestConfig())
    await adapter.setup(ctx())

    with patch.object(adapter._client, "get", return_value=mock_resp):
        items = [item async for item in adapter.fetch_raw(ctx(), query="home decor", limit=10)]

    assert len(items) == 2
    assert items[0]["id"] == "pin1"
    await adapter.teardown(ctx())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pinterest_fetch_raw_rate_limited():
    from aegis.scrape.sources.pinterest import PinterestAdapter, PinterestConfig

    mock_resp = MagicMock()
    mock_resp.status_code = 429

    adapter = PinterestAdapter(PinterestConfig())
    await adapter.setup(ctx())

    with patch.object(adapter._client, "get", return_value=mock_resp):
        items = [item async for item in adapter.fetch_raw(ctx(), query="test", limit=10)]

    assert items == []
    await adapter.teardown(ctx())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pinterest_setup_teardown():
    from aegis.scrape.sources.pinterest import PinterestAdapter, PinterestConfig

    adapter = PinterestAdapter(PinterestConfig())
    c = ctx()
    await adapter.setup(c)
    assert adapter._client is not None
    await adapter.teardown(c)
    assert adapter._client is None


# ---------------------------------------------------------------------------
# YouTube fetch_raw mock
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_youtube_setup_teardown():
    """YouTube adapter setup/teardown — builds the API client."""
    from unittest.mock import patch

    from aegis.scrape.sources.youtube import YouTubeAdapter, YouTubeConfig

    adapter = YouTubeAdapter(YouTubeConfig(api_key="fake-key-xyz"))
    c = ctx()

    with patch("aegis.scrape.sources.youtube.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        await adapter.setup(c)
        assert adapter._service is not None

    await adapter.teardown(c)
    assert adapter._service is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_youtube_fetch_raw_yields_items():
    from unittest.mock import patch

    from aegis.scrape.sources.youtube import YouTubeAdapter, YouTubeConfig

    adapter = YouTubeAdapter(YouTubeConfig(api_key="fake-key", fetch_video_details=False))
    c = ctx()

    # Mock search.list().execute()
    mock_search_response = {
        "items": [
            {
                "id": {"videoId": "abc123"},
                "snippet": {
                    "title": "Cool Video",
                    "description": "test",
                    "channelId": "ch1",
                    "channelTitle": "Ch1",
                    "publishedAt": "2024-01-15T12:00:00Z",
                    "thumbnails": {},
                },
            }
        ],
        "nextPageToken": None,
    }

    with patch("aegis.scrape.sources.youtube.build") as mock_build:
        mock_youtube = MagicMock()
        mock_build.return_value = mock_youtube
        mock_youtube.search.return_value.list.return_value.execute.return_value = mock_search_response
        await adapter.setup(c)

        items = [item async for item in adapter.fetch_raw(c, query="cool video", limit=5)]

    assert len(items) == 1
    assert items[0]["item"]["id"]["videoId"] == "abc123"
    await adapter.teardown(c)


# ---------------------------------------------------------------------------
# Amazon adapter setup/teardown (httpx-based, no playwright)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_amazon_setup_teardown():
    from aegis.scrape.sources.amazon import AmazonAdapter, AmazonConfig

    adapter = AmazonAdapter(AmazonConfig())
    c = ctx()

    await adapter.setup(c)
    assert adapter._client is not None

    await adapter.teardown(c)
    assert adapter._client is None


@pytest.mark.unit
def test_amazon_parse_bestsellers_page():
    from aegis.scrape.sources.amazon import _parse_bestsellers_page

    html = (
        'data-asin="1234567890" class="foo">'
        '<span class="zg-bdg-text">#1</span>'
        'something'
        'href="/Great-Book-Title/dp/1234567890/ref=zg_bs"'
    )
    items = _parse_bestsellers_page(html, category="books", base_url="https://www.amazon.com")
    assert len(items) == 1
    assert items[0]["asin"] == "1234567890"
    assert items[0]["rank"] == 1
    assert items[0]["category"] == "books"
    assert "Great Book Title" in items[0]["title"]


# ---------------------------------------------------------------------------
# CLI — _run helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_run_success(monkeypatch, tmp_path):
    """_run with a command that exits 0."""
    from aegis.cli.main import _run

    # Use a real command that always exits 0
    rc = _run(["python", "-c", "import sys; sys.exit(0)"], check=False)
    assert rc == 0


@pytest.mark.unit
def test_cli_run_nonzero_with_check_false(monkeypatch, tmp_path):
    """_run with check=False returns the exit code without raising."""
    from aegis.cli.main import _run

    rc = _run(["python", "-c", "import sys; sys.exit(42)"], check=False)
    assert rc == 42
