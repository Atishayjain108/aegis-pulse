"""YouTube Data API v3 source adapter.

Uses the official Google YouTube Data API v3 (free tier: 10,000 units/day).

Quota cost:
  - search.list  = 100 units/call  → fetch up to 50 videos per call
  - videos.list  =   1 unit/call   → enrich stats for a batch of IDs

Setup:
  1. Create a project in https://console.cloud.google.com/
  2. Enable "YouTube Data API v3"
  3. Create an API key (no OAuth needed for public data)
  4. Set AEGIS_YOUTUBE_API_KEY=<key> in .env

ToS Risk: GREEN — official API with standard Terms of Service.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aegis.core.logging import get_logger
from aegis.schemas.enums import (
    ContentModality,
    IntentType,
    Platform,
    ScrapeMethod,
    SourceTier,
    ToSRisk,
)
from aegis.schemas.signal import (
    Author,
    ConfidenceMetadata,
    EngagementMetrics,
    MediaRef,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)

SCRAPER_VERSION = "youtube-0.1.0"

# Lazy import kept at module level so tests can patch `aegis.scrape.sources.youtube.build`.
try:
    from googleapiclient.discovery import build  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    build = None  # type: ignore[assignment]
_YT_WATCH = "https://www.youtube.com/watch?v="
_YT_CHANNEL = "https://www.youtube.com/channel/"


@dataclass(frozen=True, slots=True)
class YouTubeConfig(AdapterConfig):
    """YouTube adapter config."""

    name: str = "youtube"
    per_source_rps: float = 1.0
    timeout_seconds: float = 30.0
    max_retries: int = 3
    use_cloudflare_bypass: bool = False

    api_key: str = ""
    """REQUIRED. YouTube Data API v3 key from Google Cloud Console."""

    fetch_video_details: bool = True
    """Fetch statistics (views/likes/comments) via videos.list. Costs 1 unit/call."""

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError(
                "YouTubeConfig.api_key is required. "
                "Set AEGIS_YOUTUBE_API_KEY in .env or environment. "
                "Get a key at https://console.cloud.google.com/"
            )


class YouTubeAdapter(SourceAdapter[dict[str, Any]]):
    """YouTube adapter using the official Data API v3."""

    def __init__(self, config: YouTubeConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        if not isinstance(config, YouTubeConfig):
            raise TypeError(
                "YouTubeAdapter requires a YouTubeConfig instance. "
                "Set AEGIS_YOUTUBE_API_KEY and use YouTubeConfig(api_key=...)."
            )
        self._yt_config: YouTubeConfig = config
        self._service: Any = None

    @property
    def name(self) -> str:
        return "youtube"

    async def setup(self, ctx: ScrapeContext) -> None:
        if build is None:  # pragma: no cover
            raise RuntimeError(
                "YouTubeAdapter requires google-api-python-client. "
                "Run: uv sync --extra scrape"
            )

        api_key = self._yt_config.api_key

        def _build() -> Any:
            return build("youtube", "v3", developerKey=api_key, cache_discovery=False)

        self._service = await asyncio.to_thread(_build)

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._service is not None:
            close_fn = getattr(self._service, "close", None)
            if close_fn is not None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(close_fn)
            self._service = None

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        query: str = "",
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._service is None:
            raise RuntimeError("YouTubeAdapter.setup() must run before fetch_raw()")

        cfg = self._yt_config
        page_token: str | None = None
        total_yielded = 0

        while total_yielded < limit and not self.is_cancelled:
            await self._rate_limit()
            self._record_request_metric(method="yt_search")

            per_page = min(50, limit - total_yielded)

            def _search(tok: str | None = page_token) -> dict[str, Any]:
                req = self._service.search().list(
                    part="snippet",
                    q=query or "trending",
                    type="video",
                    order="relevance" if query else "viewCount",
                    maxResults=per_page,
                    pageToken=tok,
                )
                return req.execute()  # type: ignore[no-any-return]

            try:
                search_resp = await asyncio.to_thread(_search)
            except Exception as e:
                log.warning("youtube.search.failed", error=str(e), query=query)
                break

            items: list[dict[str, Any]] = search_resp.get("items", [])
            if not items:
                break

            # Optionally enrich with video stats.
            details_map: dict[str, dict[str, Any]] = {}
            if cfg.fetch_video_details:
                video_ids = [
                    item["id"]["videoId"]
                    for item in items
                    if isinstance(item.get("id"), dict)
                    and item["id"].get("videoId")
                ]
                if video_ids:
                    await self._rate_limit()
                    self._record_request_metric(method="yt_videos")

                    def _videos(vids: list[str] = video_ids) -> dict[str, Any]:
                        return self._service.videos().list(  # type: ignore[no-any-return]
                            part="snippet,statistics,contentDetails",
                            id=",".join(vids),
                        ).execute()

                    try:
                        vids_resp = await asyncio.to_thread(_videos)
                        details_map = {
                            v["id"]: v for v in vids_resp.get("items", [])
                        }
                    except Exception as e:
                        log.warning("youtube.videos.failed", error=str(e))

            for item in items:
                if total_yielded >= limit or self.is_cancelled:
                    return
                vid_id = (item.get("id") or {}).get("videoId")
                if not vid_id:
                    continue
                yield {"item": item, "detail": details_map.get(vid_id, {})}
                total_yielded += 1

            page_token = search_resp.get("nextPageToken")
            if not page_token:
                break

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            item = raw.get("item", {})
            detail = raw.get("detail", {})

            vid_id: str = (item.get("id") or {}).get("videoId", "")
            if not vid_id:
                return None

            snippet = item.get("snippet", {})
            title: str = str(snippet.get("title") or "")
            description: str = str(snippet.get("description") or "")
            channel_id: str = str(snippet.get("channelId") or "")
            channel_title: str = str(snippet.get("channelTitle") or "")
            published_str: str | None = snippet.get("publishedAt")

            posted_at: datetime | None = None
            if published_str:
                with contextlib.suppress(ValueError):
                    posted_at = datetime.fromisoformat(
                        published_str.replace("Z", "+00:00")
                    )

            stats = detail.get("statistics", {})
            views = _int_or_none(stats.get("viewCount"))
            likes = _int_or_none(stats.get("likeCount"))
            comments = _int_or_none(stats.get("commentCount"))

            url = f"{_YT_WATCH}{vid_id}"

            author: Author | None = None
            if channel_id:
                author = Author(
                    platform_user_id=channel_id,
                    handle=channel_title or channel_id,
                    display_name=channel_title or None,
                    profile_url=f"{_YT_CHANNEL}{channel_id}",  # type: ignore[arg-type]
                )

            thumb = snippet.get("thumbnails", {}).get("high") or {}
            media: tuple[MediaRef, ...] = ()
            if thumb.get("url"):
                media = (
                    MediaRef(
                        url=thumb["url"],  # type: ignore[arg-type]
                        modality=ContentModality.IMAGE,
                        width_px=thumb.get("width"),
                        height_px=thumb.get("height"),
                    ),
                )

            raw_tags: list[str] = (detail.get("snippet") or {}).get("tags") or []
            tags = frozenset(
                t.lower().replace(" ", "_")[:128]
                for t in raw_tags[:20]
                if t and len(t.strip()) > 0
            )

            h = compute_content_hash(
                platform=Platform.YOUTUBE,
                external_id=vid_id,
                url=url,
                title=title,
                raw_text=description or None,
                posted_at=posted_at,
            )

            modality = ContentModality.MULTIMODAL if media else ContentModality.VIDEO

            return ProductSignal(
                platform=Platform.YOUTUBE,
                tier=SourceTier.TIER_1_INTENT,
                external_id=vid_id,
                url=url,  # type: ignore[arg-type]
                title=title or None,
                raw_text=description or None,
                modality=modality,
                tags=tags,
                intent=IntentType.PASSIVE,
                author=author,
                engagement=EngagementMetrics(
                    views=views,
                    likes=likes,
                    comments=comments,
                ),
                media=media,
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.OFFICIAL_API,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=1.0 if (title and views is not None) else 0.7,
                    source_confidence=0.95,
                ),
                content_hash=h,
                platform_specific={
                    "channel_id": channel_id,
                    "channel_title": channel_title,
                    "view_count": views,
                    "like_count": likes,
                    "comment_count": comments,
                },
            )
        except Exception as e:
            log.warning("youtube.parse.failed", error=str(e))
            return None


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


__all__ = ["YouTubeAdapter", "YouTubeConfig", "SCRAPER_VERSION"]
