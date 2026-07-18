"""BBC News RSS adapter — business + technology feeds.

Feeds:
  https://feeds.bbci.co.uk/news/business/rss.xml
  https://feeds.bbci.co.uk/news/technology/rss.xml

Both feeds are fetched concurrently via asyncio.gather and merged with
URL-level deduplication in the base RSSAdapter.

PLATFORM: bbc_news | TIER: T3_search
ToS Risk: AMBER — publicly listed BBC RSS feeds.
"""
from __future__ import annotations

from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "bbc-rss-0.1.0"


class BBCBusinessAdapter(RSSAdapter):
    """BBC News adapter — business + technology RSS, deduplicated by URL."""

    _FEEDS = (
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://feeds.bbci.co.uk/news/technology/rss.xml",
    )
    _PLATFORM = Platform.BBC_NEWS
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "bbc_news"


__all__ = ["SCRAPER_VERSION", "BBCBusinessAdapter", "RSSAdapterConfig"]
