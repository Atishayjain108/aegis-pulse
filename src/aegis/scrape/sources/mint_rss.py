"""Mint (LiveMint) RSS adapter — Indian markets, companies, technology.

Feeds (three concurrent):
  https://www.livemint.com/rss/markets
  https://www.livemint.com/rss/companies
  https://www.livemint.com/rss/technology

PLATFORM: mint | TIER: T3_search
ToS Risk: AMBER — publicly listed LiveMint RSS feeds.
"""
from __future__ import annotations

from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "mint-rss-0.1.0"


class MintRSSAdapter(RSSAdapter):
    """Mint RSS adapter — three Indian financial feeds merged and deduplicated."""

    _FEEDS = (
        "https://www.livemint.com/rss/markets",
        "https://www.livemint.com/rss/companies",
        "https://www.livemint.com/rss/technology",
    )
    _PLATFORM = Platform.MINT
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "mint"


__all__ = ["MintRSSAdapter", "RSSAdapterConfig", "SCRAPER_VERSION"]
