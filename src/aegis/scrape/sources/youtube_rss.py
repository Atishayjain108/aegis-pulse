"""YouTube RSS adapter — channel feeds (primary) + trending page (secondary).

Path A — Channel RSS (primary, reliable):
    https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}
    Parsed as Atom feed via feedparser.

Path B — Trending HTML (secondary, fragile):
    https://www.youtube.com/feed/trending
    Parsed with BeautifulSoup. Wrapped in try/except — failure skips silently.

No YouTube Data API v3 key required.
ToS Risk: AMBER — public RSS; HTML scraping is tolerated but fragile.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import feedparser
import httpx
import structlog

from aegis.schemas.enums import (
    ContentModality,
    IntentType,
    Platform,
    ScrapeMethod,
    SourceTier,
    ToSRisk,
)
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

_log = structlog.get_logger("aegis.scrape.youtube_rss")

SCRAPER_VERSION = "youtube-rss-0.1.0"

_CHANNEL_FEED_TMPL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
_TRENDING_URL = "https://www.youtube.com/feed/trending"
_HEADERS = {
    "User-Agent": "AegisPulse/0.3 (market-intelligence; contact@aegis.dev)",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


def _clean_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def _parse_atom_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    with contextlib.suppress(Exception):
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    return None


@dataclass(frozen=True, slots=True)
class YouTubeRSSConfig(AdapterConfig):
    name: str = "youtube_rss"
    per_source_rps: float = 0.5
    timeout_seconds: float = 25.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    include_trending: bool = True
    """Also scrape the trending page (Path B)."""


class YouTubeRSSAdapter(SourceAdapter[dict[str, Any]]):
    """YouTube channel RSS + trending page adapter. No API key required."""

    def __init__(self, config: YouTubeRSSConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._yt_config = (
            config if isinstance(config, YouTubeRSSConfig) else YouTubeRSSConfig()
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "youtube_rss"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._yt_config.timeout_seconds),
            headers=_HEADERS,
            follow_redirects=True,
            http2=False,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Path A: channel RSS via feedparser
    # ------------------------------------------------------------------

    async def _fetch_channel(self, channel_id: str) -> list[dict[str, Any]]:
        url = _CHANNEL_FEED_TMPL.format(channel_id=channel_id)
        try:
            feed = await asyncio.to_thread(feedparser.parse, url)
        except Exception as e:
            _log.warning("youtube_rss.channel_feed.failed", channel_id=channel_id, error=str(e))
            return []

        raws: list[dict[str, Any]] = []
        for entry in feed.entries or []:
            title = (entry.get("title") or "").strip()
            video_url = _clean_url(entry.get("link") or "")
            if not video_url:
                continue
            author = entry.get("author") or entry.get("author_detail", {}).get("name")
            published = entry.get("published") or entry.get("updated") or ""
            description = (entry.get("summary") or "").strip()
            raws.append({
                "title": title,
                "url": video_url,
                "platform": Platform.YOUTUBE_RSS.value,
                "scraped_at": datetime.now(UTC).isoformat(),
                "author": author,
                "score": 0.0,
                "views": None,
                "likes": None,
                "comments": None,
                "shares": None,
                "saves": None,
                "sentiment": score_text(f"{title} {description}"),
                "raw_json": {
                    "channel_id": channel_id,
                    "description": description[:2000],
                    "published": published,
                    "source": "channel_rss",
                },
            })
        return raws

    # ------------------------------------------------------------------
    # Path B: trending page HTML (fallback — may break with YouTube changes)
    # ------------------------------------------------------------------

    async def _fetch_trending(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        try:
            await self._rate_limit()
            self._record_request_metric(method="html")
            resp = await self._client.get(_TRENDING_URL)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            _log.warning("youtube_rss.trending.fetch_failed", error=str(e))
            return []

        try:
            from bs4 import BeautifulSoup  # type: ignore[import-untyped]

            soup = BeautifulSoup(html, "lxml")
            raws: list[dict[str, Any]] = []
            # YouTube renders titles in <a id="video-title"> or <yt-formatted-string>
            for tag in soup.find_all("a", id="video-title", href=True)[:50]:
                title = (tag.get_text(strip=True) or "").strip()
                href = str(tag.get("href") or "")
                if not title or not href:
                    continue
                video_url = f"https://www.youtube.com{href}" if href.startswith("/") else href
                raws.append({
                    "title": title,
                    "url": _clean_url(video_url),
                    "platform": Platform.YOUTUBE_RSS.value,
                    "scraped_at": datetime.now(UTC).isoformat(),
                    "author": None,
                    "score": 0.0,
                    "views": None,
                    "likes": None,
                    "comments": None,
                    "shares": None,
                    "saves": None,
                    "sentiment": score_text(title),
                    "raw_json": {"source": "trending_html"},
                })
            return raws
        except Exception as e:
            _log.warning("youtube_rss.trending.parse_failed", error=str(e))
            return []

    # ------------------------------------------------------------------
    # fetch_raw: Path A first, Path B appended if enabled
    # ------------------------------------------------------------------

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        from aegis.config import settings as _settings

        channel_ids = _settings().youtube_channel_ids

        # Path A — gather all channel feeds concurrently
        channel_results = await asyncio.gather(
            *[self._fetch_channel(cid) for cid in channel_ids],
            return_exceptions=True,
        )

        raws: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for result in channel_results:
            if isinstance(result, BaseException):
                _log.warning("youtube_rss.channel_gather_error", error=str(result))
                continue
            for item in result:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    raws.append(item)

        # Path B — trending page (append; failures are silent per spec)
        if self._yt_config.include_trending:
            try:
                trending = await self._fetch_trending()
            except Exception as e:
                _log.warning("youtube_rss.trending.unexpected_error", error=str(e))
                trending = []
            for item in trending:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    raws.append(item)

        valid = validate_batch(raws, Platform.YOUTUBE_RSS.value)
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

            raw_json = raw.get("raw_json") or {}
            description = raw_json.get("description") or ""
            published_str = raw_json.get("published") or ""
            channel_id = raw_json.get("channel_id") or ""
            author = raw.get("author")
            sentiment_val: float = raw.get("sentiment") or 0.0

            posted_at = _parse_atom_date(published_str)

            external_id = hashlib.sha1(url.encode("utf-8", errors="replace")).hexdigest()[:24]  # noqa: S324

            tags: frozenset[str] = frozenset({"youtube-rss"})
            if channel_id:
                tags = tags | {f"channel-{channel_id[:16].lower()}"}

            h = compute_content_hash(
                platform=Platform.YOUTUBE_RSS,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=description[:2000] if description else None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.YOUTUBE_RSS,
                tier=SourceTier.TIER_1_INTENT,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=description[:2000] if description else None,
                modality=ContentModality.VIDEO,
                tags=tags,
                intent=IntentType.PASSIVE,
                engagement=EngagementMetrics(),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.RSS_FEED,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.70 if (title and posted_at) else 0.45,
                    source_confidence=0.80,
                ),
                content_hash=h,
                platform_specific={
                    "channel_id": channel_id,
                    "author": author,
                    "sentiment": sentiment_val,
                    "source": raw_json.get("source", "channel_rss"),
                },
            )
        except Exception as e:
            _log.warning("youtube_rss.parse.failed", error=str(e))
            return None


__all__ = ["YouTubeRSSAdapter", "YouTubeRSSConfig", "SCRAPER_VERSION"]
