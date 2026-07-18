"""Business Standard RSS adapter — top stories and markets.

business-standard.com direct RSS feeds return 403 as of 2026.
Replaced with Google News RSS search filtered to Business Standard articles.

PLATFORM: business_standard | TIER: T3_search
ToS Risk: AMBER — Google News RSS search (publicly documented).
"""
from __future__ import annotations

from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "business-standard-rss-0.2.0"


class BusinessStandardRSSAdapter(RSSAdapter):
    """Business Standard via Google News RSS — direct feeds blocked as of 2026."""

    _FEEDS = (
        "https://news.google.com/rss/search?q=site:business-standard.com+markets&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=site:business-standard.com+economy&hl=en-IN&gl=IN&ceid=IN:en",
    )
    _PLATFORM = Platform.BUSINESS_STANDARD
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "business_standard"


__all__ = ["SCRAPER_VERSION", "BusinessStandardRSSAdapter", "RSSAdapterConfig"]
