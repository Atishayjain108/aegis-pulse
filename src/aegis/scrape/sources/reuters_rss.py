"""Reuters RSS adapter — business, technology, and wealth feeds.

Feeds (three concurrent):
  https://feeds.reuters.com/reuters/businessNews
  https://feeds.reuters.com/reuters/technologyNews
  https://feeds.reuters.com/reuters/wealth

PLATFORM: reuters | TIER: T3_search
ToS Risk: AMBER — publicly listed Reuters RSS feeds.
"""
from __future__ import annotations

from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "reuters-rss-0.1.0"


class ReutersRSSAdapter(RSSAdapter):
    """Reuters RSS adapter — three feeds merged and deduplicated by URL."""

    _FEEDS = (
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.reuters.com/reuters/technologyNews",
        "https://feeds.reuters.com/reuters/wealth",
    )
    _PLATFORM = Platform.REUTERS
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "reuters"


__all__ = ["SCRAPER_VERSION", "RSSAdapterConfig", "ReutersRSSAdapter"]
