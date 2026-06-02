"""Business Standard RSS adapter — top stories and markets.

Feeds (two concurrent):
  https://www.business-standard.com/rss/home_page_top_stories.rss
  https://www.business-standard.com/rss/markets-106.rss

PLATFORM: business_standard | TIER: T3_search
ToS Risk: AMBER — publicly listed Business Standard RSS feeds.
"""
from __future__ import annotations

from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "business-standard-rss-0.1.0"


class BusinessStandardRSSAdapter(RSSAdapter):
    """Business Standard RSS adapter — top stories + markets, deduplicated."""

    _FEEDS = (
        "https://www.business-standard.com/rss/home_page_top_stories.rss",
        "https://www.business-standard.com/rss/markets-106.rss",
    )
    _PLATFORM = Platform.BUSINESS_STANDARD
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "business_standard"


__all__ = ["SCRAPER_VERSION", "BusinessStandardRSSAdapter", "RSSAdapterConfig"]
