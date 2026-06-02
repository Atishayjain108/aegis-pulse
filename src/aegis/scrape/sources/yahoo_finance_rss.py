"""Yahoo Finance RSS adapter — top financial news + per-ticker headlines.

Feeds:
  1. Top stories: https://finance.yahoo.com/news/rssindex
  2. Per-ticker feed for the first 10 tickers in settings.finance_tickers_global

All feeds are fetched concurrently (via asyncio.gather in the base class) and
merged with URL-level deduplication.

PLATFORM: yahoo_finance | TIER: T3_search
ToS Risk: AMBER — publicly accessible Yahoo Finance RSS feeds.
"""
from __future__ import annotations

from aegis.config import settings
from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "yahoo-finance-rss-0.1.0"

_TOP_STORIES_URL = "https://finance.yahoo.com/news/rssindex"
_TICKER_FEED_TPL = "https://finance.yahoo.com/rss/headline?s={ticker}"


class YahooFinanceRSSAdapter(RSSAdapter):
    """Yahoo Finance RSS adapter — top stories + per-ticker headlines, deduped."""

    _FEEDS = (_TOP_STORIES_URL,)
    _PLATFORM = Platform.YAHOO_FINANCE
    _TIER = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "yahoo_finance"

    def _all_feeds(self) -> tuple[str, ...]:
        tickers = settings().finance_tickers_global[:10]
        ticker_feeds = tuple(_TICKER_FEED_TPL.format(ticker=t) for t in tickers)
        return self._FEEDS + ticker_feeds + self._rss_config.extra_feed_urls


__all__ = ["SCRAPER_VERSION", "RSSAdapterConfig", "YahooFinanceRSSAdapter"]
