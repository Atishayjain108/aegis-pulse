"""Investing.com RSS adapter — news and market overview opinions.

Feeds (two concurrent):
  https://www.investing.com/rss/news.rss
  https://www.investing.com/rss/market_overview_opinions.rss

PLATFORM: investing_com | TIER: T3_search
ToS Risk: AMBER — publicly listed Investing.com RSS endpoints.
"""
from __future__ import annotations

from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "investing-com-rss-0.1.0"


class InvestingComRSSAdapter(RSSAdapter):
    """Investing.com RSS adapter — news + opinions, deduplicated by URL."""

    _FEEDS = (
        "https://www.investing.com/rss/news.rss",
        "https://www.investing.com/rss/market_overview_opinions.rss",
    )
    _PLATFORM = Platform.INVESTING_COM
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "investing_com"


__all__ = ["SCRAPER_VERSION", "InvestingComRSSAdapter", "RSSAdapterConfig"]
