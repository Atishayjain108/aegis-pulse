"""Wired RSS adapter.

Feed: https://www.wired.com/feed/rss
PLATFORM: wired | TIER: T3_search
ToS Risk: AMBER — publicly accessible Condé Nast RSS feed.
"""
from __future__ import annotations

from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "wired-rss-0.1.0"


class WiredRSSAdapter(RSSAdapter):
    """Wired RSS adapter — technology and culture journalism."""

    _FEEDS = ("https://www.wired.com/feed/rss",)
    _PLATFORM = Platform.WIRED
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "wired"


__all__ = ["WiredRSSAdapter", "RSSAdapterConfig", "SCRAPER_VERSION"]
