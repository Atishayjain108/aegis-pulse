"""NDTV Profit RSS adapter — Indian business and markets news.

Feed: https://feeds.feedburner.com/ndtvprofit-latest

PLATFORM: ndtv_profit | TIER: T3_search
ToS Risk: AMBER — Feedburner-hosted public RSS feed.
"""
from __future__ import annotations

from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "ndtv-profit-rss-0.1.0"


class NDTVProfitAdapter(RSSAdapter):
    """NDTV Profit RSS adapter — Indian financial markets and business news."""

    _FEEDS = ("https://feeds.feedburner.com/ndtvprofit-latest",)
    _PLATFORM = Platform.NDTV_PROFIT
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "ndtv_profit"


__all__ = ["SCRAPER_VERSION", "NDTVProfitAdapter", "RSSAdapterConfig"]
