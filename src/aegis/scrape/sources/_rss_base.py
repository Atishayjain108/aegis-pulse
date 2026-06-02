"""Shared helpers for feedparser-based RSS adapters (Phase 1).

All RSS adapters in this package inherit ``RSSAdapter`` which handles:
- ``asyncio.to_thread`` wrapping for feedparser (synchronous library)
- URL tracking-param stripping
- Intermediate-dict construction with schema_guard validation
- ``parse()`` conversion to ``ProductSignal``

Concrete adapters only need to supply ``_FEEDS``, ``PLATFORM``, ``TIER``,
``SCRAPER_VERSION``, and override ``_extra_platform_specific()`` if needed.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import feedparser
import structlog

from aegis.schemas.enums import ContentModality, IntentType, ScrapeMethod, SourceTier, ToSRisk
from aegis.schemas.signal import (
    ConfidenceMetadata,
    EngagementMetrics,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter
from aegis.scrape.schema_guard import validate_batch
from aegis.scrape.sentiment import score_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from aegis.schemas.enums import Platform


def clean_url(url: str) -> str:
    """Strip all query/fragment tracking params — keeps scheme+host+path only."""
    if not url:
        return ""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def _clean_tag(raw_tag: str) -> str | None:
    """Normalise a feedparser tag to the TagString pattern ``^[a-z0-9_\\-\\.]+$``."""
    cleaned = re.sub(r"[^a-z0-9_\-\.]", "-", raw_tag.lower().strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned if cleaned else None


def _parse_date(date_str: str) -> datetime | None:
    """Parse RFC-2822 / ISO-8601 date strings into timezone-aware UTC datetimes."""
    if not date_str:
        return None
    with contextlib.suppress(Exception):
        dt = parsedate_to_datetime(date_str)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def _entry_to_raw(entry: Any, platform_str: str) -> dict[str, Any]:
    """Convert one feedparser entry into a schema-guard-compatible raw dict."""
    title = (entry.get("title") or "").strip()
    raw_link = entry.get("link") or ""
    url = clean_url(raw_link)
    summary = re.sub(r"<[^>]+>", " ", entry.get("summary") or "").strip()
    author = entry.get("author") or None
    published = entry.get("published") or entry.get("updated") or ""
    tags = [
        t
        for t in (_clean_tag(tg.get("term", "")) for tg in entry.get("tags", []))
        if t
    ]
    sentiment = score_text(f"{title} {summary}")
    return {
        "title": title,
        "url": url,
        "platform": platform_str,
        "scraped_at": datetime.now(UTC).isoformat(),
        "author": author,
        "score": 0.0,
        "views": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "saves": None,
        "sentiment": sentiment,
        "raw_json": {"summary": summary[:2000], "published": published, "tags": tags},
    }


async def fetch_feed_entries(url: str, platform_str: str) -> list[dict[str, Any]]:
    """Fetch and parse one RSS feed URL; return list of raw dicts (never raises)."""
    _log = structlog.get_logger("aegis.scrape._rss_base")
    try:
        feed = await asyncio.to_thread(feedparser.parse, url)
        return [_entry_to_raw(e, platform_str) for e in (feed.entries or [])]
    except Exception as e:
        _log.warning("rss_fetch.failed", url=url, error=str(e))
        return []


@dataclass(frozen=True, slots=True)
class RSSAdapterConfig(AdapterConfig):
    """Configuration for all RSS adapters."""

    name: str = "rss"
    per_source_rps: float = 0.5
    timeout_seconds: float = 20.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    extra_feed_urls: tuple[str, ...] = field(default_factory=tuple)
    """Extra feed URLs appended to the adapter's ``_FEEDS`` at runtime."""


class RSSAdapter(SourceAdapter[dict[str, Any]]):
    """Base for all feedparser RSS adapters.

    Subclasses set class-level attributes:
        _FEEDS: tuple[str, ...] — ordered list of RSS feed URLs
        _PLATFORM: Platform — the platform enum member
        _TIER: SourceTier — the source tier
        _SCRAPER_VERSION: str
        _TOS_RISK: ToSRisk (default AMBER)
    """

    _FEEDS: tuple[str, ...] = ()
    _PLATFORM: Platform  # must be set by subclass
    _TIER: SourceTier = SourceTier.TIER_3_SEARCH
    _SCRAPER_VERSION: str = "rss-0.1.0"
    _TOS_RISK: ToSRisk = ToSRisk.AMBER

    def __init__(self, config: RSSAdapterConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._rss_config = config if isinstance(config, RSSAdapterConfig) else RSSAdapterConfig(
            name=self.name
        )
        self._log = structlog.get_logger(f"aegis.scrape.{self.name.replace('-', '_')}")

    @property
    def name(self) -> str:
        return self._PLATFORM.value

    def _all_feeds(self) -> tuple[str, ...]:
        return self._FEEDS + self._rss_config.extra_feed_urls

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        platform_str = self._PLATFORM.value
        feeds = self._all_feeds()
        if not feeds:
            return

        # Fetch all feeds concurrently
        results = await asyncio.gather(
            *[fetch_feed_entries(url, platform_str) for url in feeds],
            return_exceptions=True,
        )

        seen_urls: set[str] = set()
        raws: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                self._log.warning("feed.gather_error", error=str(result))
                continue
            for item in result:
                url = item.get("url", "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                raws.append(item)

        valid = validate_batch(raws, platform_str)
        for item in valid[:limit]:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = (raw.get("title") or "").strip()
            url = raw.get("url") or ""
            if not url:
                return None

            summary = raw.get("raw_json", {}).get("summary") or ""
            published_str = raw.get("raw_json", {}).get("published") or ""
            tags_raw: list[str] = raw.get("raw_json", {}).get("tags") or []
            author = raw.get("author")
            sentiment_val: float = raw.get("sentiment") or 0.0

            posted_at = _parse_date(published_str)

            tag_set: frozenset[str] = frozenset(t for t in tags_raw if t)
            # Always add platform tag
            tag_set = tag_set | {self._PLATFORM.value.replace("_", "-")}

            external_id = hashlib.sha1(  # noqa: S324 — non-security use
                url.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=self._PLATFORM,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=summary[:2000] if summary else None,
                posted_at=posted_at,
            )

            completeness = 0.7 if (title and posted_at) else 0.45

            return ProductSignal(
                platform=self._PLATFORM,
                tier=self._TIER,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=summary[:2000] if summary else None,
                modality=ContentModality.TEXT,
                tags=tag_set,
                intent=IntentType.PASSIVE,
                engagement=EngagementMetrics(),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.RSS_FEED,
                    scraped_at=datetime.now(UTC),
                    scraper_version=self._SCRAPER_VERSION,
                    tos_risk=self._TOS_RISK,
                ),
                confidence=ConfidenceMetadata(
                    completeness=completeness,
                    source_confidence=0.80,
                ),
                content_hash=h,
                platform_specific={
                    "author": author,
                    "sentiment": sentiment_val,
                },
            )
        except Exception as e:
            self._log.warning("rss.parse.failed", error=str(e))
            return None


__all__ = [
    "RSSAdapter",
    "RSSAdapterConfig",
    "_entry_to_raw",
    "clean_url",
    "fetch_feed_entries",
]
