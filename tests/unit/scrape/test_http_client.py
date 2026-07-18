"""PASS5-5B — shared HTTP client factory tests.

Contract: one pooled ``httpx.AsyncClient`` per (loop, host, config);
``get_client`` never closes on exit; ``close_all`` closes and clears;
adapters that adopted the factory share clients and no longer close
them in ``teardown``.
"""

from __future__ import annotations

import httpx
import pytest

from aegis.scrape import http_client
from aegis.scrape.http_client import close_all, get_client, get_or_create_client


@pytest.fixture(autouse=True)
async def _clean_registry():
    """Each test starts and ends with an empty registry for its loop."""
    await close_all()
    yield
    await close_all()


async def test_same_host_same_config_reuses_client():
    a = await get_or_create_client("example.com", http2=False)
    b = await get_or_create_client("example.com", http2=False)
    assert a is b


async def test_different_headers_get_distinct_clients():
    a = await get_or_create_client("example.com", http2=False, headers={"X-A": "1"})
    b = await get_or_create_client("example.com", http2=False, headers={"X-A": "2"})
    assert a is not b


async def test_different_timeout_gets_distinct_client():
    a = await get_or_create_client("example.com", http2=False, timeout=5.0)
    b = await get_or_create_client("example.com", http2=False, timeout=30.0)
    assert a is not b


async def test_http1_only_host_defaults_to_h1():
    # www.reddit.com is in the HTTP/1.1-only set — default key must be h1,
    # identical to requesting http2=False explicitly.
    a = await get_or_create_client("www.reddit.com")
    b = await get_or_create_client("www.reddit.com", http2=False)
    assert a is b


async def test_closed_client_is_replaced():
    a = await get_or_create_client("example.com", http2=False)
    await a.aclose()
    b = await get_or_create_client("example.com", http2=False)
    assert b is not a
    assert not b.is_closed


async def test_get_client_context_manager_does_not_close():
    async with get_client("example.com", http2=False) as client:
        assert isinstance(client, httpx.AsyncClient)
    assert not client.is_closed
    # And it is still the registered instance afterwards.
    again = await get_or_create_client("example.com", http2=False)
    assert again is client


async def test_close_all_closes_and_clears():
    client = await get_or_create_client("example.com", http2=False)
    await close_all()
    assert client.is_closed
    replacement = await get_or_create_client("example.com", http2=False)
    assert replacement is not client


async def test_client_carries_headers_and_redirect_policy():
    client = await get_or_create_client(
        "example.com",
        http2=False,
        headers={"User-Agent": "aegis-test"},
        follow_redirects=True,
    )
    assert client.headers["User-Agent"] == "aegis-test"
    assert client.follow_redirects is True


async def test_h2_import_error_falls_back_to_http1(monkeypatch: pytest.MonkeyPatch):
    real_async_client = httpx.AsyncClient
    calls: list[bool] = []

    class _PickyClient:
        def __new__(cls, *args, **kwargs):
            calls.append(kwargs.get("http2", False))
            if kwargs.get("http2"):
                raise ImportError("h2 not installed")
            return real_async_client(*args, **kwargs)

    monkeypatch.setattr(http_client.httpx, "AsyncClient", _PickyClient)
    client = await get_or_create_client("h2-capable.example.com")
    assert calls == [True, False]  # tried h2, fell back to h1
    assert not client.is_closed


# ---------------------------------------------------------------------------
# Adapter integration — the 5 migrated adapters share pooled clients
# ---------------------------------------------------------------------------


async def test_hacker_news_adapters_share_client():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    ctx = ScrapeContext()
    a1 = HackerNewsAdapter(HackerNewsConfig())
    a2 = HackerNewsAdapter(HackerNewsConfig())
    await a1.setup(ctx)
    await a2.setup(ctx)
    assert a1._client is a2._client


async def test_teardown_releases_reference_without_closing():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.reddit_rss import RedditRSSAdapter, RedditRSSConfig

    ctx = ScrapeContext()
    adapter = RedditRSSAdapter(RedditRSSConfig())
    await adapter.setup(ctx)
    shared = adapter._client
    assert shared is not None
    await adapter.teardown(ctx)
    assert adapter._client is None
    assert not shared.is_closed  # pooled client survives the adapter


async def test_all_five_migrated_adapters_use_factory():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.amazon import AmazonAdapter, AmazonConfig
    from aegis.scrape.sources.github_trending import (
        GitHubTrendingAdapter,
        GitHubTrendingConfig,
    )
    from aegis.scrape.sources.google_news_rss import (
        GoogleNewsRSSAdapter,
        GoogleNewsRSSConfig,
    )
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig
    from aegis.scrape.sources.reddit_rss import RedditRSSAdapter, RedditRSSConfig

    ctx = ScrapeContext()
    adapters = [
        HackerNewsAdapter(HackerNewsConfig()),
        GitHubTrendingAdapter(GitHubTrendingConfig()),
        GoogleNewsRSSAdapter(GoogleNewsRSSConfig()),
        RedditRSSAdapter(RedditRSSConfig()),
        AmazonAdapter(AmazonConfig()),
    ]
    for adapter in adapters:
        await adapter.setup(ctx)
        assert adapter._client is not None

    # Re-running setup must hand back the same pooled instance each time.
    first_clients = [a._client for a in adapters]
    for adapter in adapters:
        await adapter.teardown(ctx)
        await adapter.setup(ctx)
    assert [a._client for a in adapters] == first_clients
