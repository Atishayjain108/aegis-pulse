"""ADP-3: ETag / Last-Modified conditional-GET tests for the RSS base adapter."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from aegis.scrape.sources import _rss_base
from aegis.scrape.sources._rss_base import clear_feed_cache, fetch_feed_entries

_URL = "https://example.test/feed.xml"


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_feed_cache()
    yield
    clear_feed_cache()


def _feed(*, status: int, entries: list, etag=None, modified=None):
    fp = SimpleNamespace()
    fp.status = status
    fp.entries = entries
    if etag is not None:
        fp.etag = etag
    if modified is not None:
        fp.modified = modified
    return fp


async def test_first_fetch_sends_no_validators_and_caches_them():
    calls = []

    def fake_parse(url, etag=None, modified=None):
        calls.append((etag, modified))
        return _feed(status=200, entries=[{"title": "a", "link": "https://x/a"}],
                     etag='W/"abc"', modified="Wed, 04 Jun 2025 10:00:00 GMT")

    with patch.object(_rss_base.feedparser, "parse", side_effect=fake_parse):
        out = await fetch_feed_entries(_URL, "techcrunch")

    assert len(out) == 1
    # First call: no validators yet.
    assert calls[0] == (None, None)
    # Validators are now cached.
    assert _rss_base._FEED_VALIDATORS[_URL] == ('W/"abc"', "Wed, 04 Jun 2025 10:00:00 GMT")


async def test_second_fetch_replays_cached_validators():
    seq = [
        _feed(status=200, entries=[{"title": "a", "link": "https://x/a"}],
              etag='W/"v1"', modified="Mon, 01 Jan 2024 00:00:00 GMT"),
        _feed(status=200, entries=[{"title": "b", "link": "https://x/b"}], etag='W/"v2"'),
    ]
    calls = []

    def fake_parse(url, etag=None, modified=None):
        calls.append((etag, modified))
        return seq[len(calls) - 1]

    with patch.object(_rss_base.feedparser, "parse", side_effect=fake_parse):
        await fetch_feed_entries(_URL, "techcrunch")
        await fetch_feed_entries(_URL, "techcrunch")

    # Second call replays the validators returned by the first.
    assert calls[1] == ('W/"v1"', "Mon, 01 Jan 2024 00:00:00 GMT")


async def test_304_short_circuits_to_empty():
    with patch.object(_rss_base.feedparser, "parse",
                      return_value=_feed(status=304, entries=[])):
        out = await fetch_feed_entries(_URL, "techcrunch")
    assert out == []


async def test_exception_returns_empty():
    with patch.object(_rss_base.feedparser, "parse", side_effect=RuntimeError("boom")):
        out = await fetch_feed_entries(_URL, "techcrunch")
    assert out == []


async def test_no_validators_when_server_omits_them():
    with patch.object(_rss_base.feedparser, "parse",
                      return_value=_feed(status=200, entries=[{"title": "a", "link": "https://x/a"}])):
        await fetch_feed_entries(_URL, "techcrunch")
    assert _URL not in _rss_base._FEED_VALIDATORS
