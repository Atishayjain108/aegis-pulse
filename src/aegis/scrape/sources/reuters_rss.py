"""Reuters RSS adapter — business, technology, and markets feeds.

Reuters shut down their direct RSS feeds (feeds.reuters.com now returns 401/404).
Replaced with Google News RSS search filtered to Reuters articles, which returns
the same content via a publicly documented feed URL.

PLATFORM: reuters | TIER: T3_search
ToS Risk: AMBER — Google News RSS search (publicly documented).
"""
from __future__ import annotations

from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "reuters-rss-0.2.0"


class ReutersRSSAdapter(RSSAdapter):
    """Reuters via Google News RSS — feeds.reuters.com is dead as of 2026."""

    _FEEDS = (
        "https://news.google.com/rss/search?q=reuters+business&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=reuters+technology&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=reuters+markets&hl=en-US&gl=US&ceid=US:en",
    )
    _PLATFORM = Platform.REUTERS
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "reuters"


__all__ = ["SCRAPER_VERSION", "RSSAdapterConfig", "ReutersRSSAdapter"]
