"""Medium RSS adapter — tag-based articles from settings.medium_tags.

One feed per tag:
  https://medium.com/feed/tag/<tag>

All tag feeds are fetched concurrently via asyncio.gather (handled by the
base class) and merged with URL-level deduplication.

PLATFORM: medium | TIER: T4_cultural
ToS Risk: AMBER — publicly accessible Medium tag feeds.
"""
from __future__ import annotations

from aegis.config import settings
from aegis.schemas.enums import Platform, SourceTier, ToSRisk
from aegis.scrape.sources._rss_base import RSSAdapter, RSSAdapterConfig

SCRAPER_VERSION = "medium-rss-0.1.0"

_TAG_FEED_TPL = "https://medium.com/feed/tag/{tag}"


class MediumRSSAdapter(RSSAdapter):
    """Medium RSS adapter — one feed per tag, all fetched concurrently."""

    _FEEDS: tuple[str, ...] = ()  # populated at runtime from settings
    _PLATFORM = Platform.MEDIUM
    _TIER = SourceTier.TIER_4_CULTURAL
    _SCRAPER_VERSION = SCRAPER_VERSION
    _TOS_RISK = ToSRisk.AMBER

    @property
    def name(self) -> str:
        return "medium"

    def _all_feeds(self) -> tuple[str, ...]:
        tags = settings().medium_tags
        return tuple(_TAG_FEED_TPL.format(tag=t) for t in tags) + self._rss_config.extra_feed_urls


__all__ = ["SCRAPER_VERSION", "MediumRSSAdapter", "RSSAdapterConfig"]
