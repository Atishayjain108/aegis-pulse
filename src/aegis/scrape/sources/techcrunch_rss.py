"""TechCrunch RSS adapter.

Feed: https://techcrunch.com/feed/
PLATFORM: techcrunch | TIER: T3_search
ToS Risk: AMBER — publicly accessible feed, no auth required.
"""
from __future__ import annotations

from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "techcrunch-rss-0.1.0"


class TechCrunchRSSAdapter(RSSAdapter):
    """TechCrunch RSS adapter — tech/startup news."""

    _FEEDS = ("https://techcrunch.com/feed/",)
    _PLATFORM = Platform.TECHCRUNCH
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "techcrunch"


__all__ = ["TechCrunchRSSAdapter", "RSSAdapterConfig", "SCRAPER_VERSION"]
