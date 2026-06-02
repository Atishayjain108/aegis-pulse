"""TikTok Creative Center source adapter.

Scrapes the TikTok Creative Center public JSON endpoints:
  - Trending hashtags: /popular_trend/hashtag/list
  - Trending videos:   /popular_trend/video/list

No authentication is required for the Creative Center discovery endpoints.
The API is unofficial — TikTok may update or gate it without notice.

ToS Risk: AMBER — public content, no explicit API; scraping tolerated in
practice for read-only research. We respect robots.txt and never log in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

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
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)

SCRAPER_VERSION = "tiktok-0.1.0"

_CC_BASE = "https://ads.tiktok.com/creative_radar_api/v1/popular_trend"
_TT_VIDEO_BASE = "https://www.tiktok.com/@{author}/video/{id}"


@dataclass(frozen=True, slots=True)
class TikTokConfig(AdapterConfig):
    """TikTok Creative Center adapter config."""

    name: str = "tiktok"
    per_source_rps: float = 0.5
    timeout_seconds: float = 30.0
    max_retries: int = 3
    use_cloudflare_bypass: bool = False
    mobile_user_agent: bool = True

    country_code: str = "US"
    """2-letter ISO country code for regional trends."""

    period_days: int = 7
    """Trending window: 7 or 30 days."""

    fetch_videos: bool = True
    """If True, also fetch trending videos (not just hashtags)."""


class TikTokAdapter(SourceAdapter[dict[str, Any]]):
    """TikTok Creative Center adapter."""

    def __init__(self, config: TikTokConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._tt_config = config if isinstance(config, TikTokConfig) else TikTokConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "tiktok"

    async def setup(self, ctx: ScrapeContext) -> None:
        ua = (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Mobile Safari/537.36"
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._tt_config.timeout_seconds),
            headers={
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://ads.tiktok.com/",
                "Origin": "https://ads.tiktok.com",
            },
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        query: str | None = None,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("TikTokAdapter.setup() must run before fetch_raw()")

        cfg = self._tt_config
        total_yielded = 0

        # --- Trending hashtags ---
        await self._rate_limit()
        self._record_request_metric(method="cc_hashtags")

        hashtag_params: dict[str, Any] = {
            "page": 1,
            "limit": min(50, limit),
            "period": cfg.period_days,
            "country_code": cfg.country_code,
            "sort_by": "popular",
        }
        if query:
            hashtag_params["keyword"] = query

        try:
            resp = await self._client.get(
                f"{_CC_BASE}/hashtag/list",
                params=hashtag_params,
            )
            resp.raise_for_status()
            data = resp.json()
            code = data.get("code", 0)
            if code != 0:
                log.warning(
                    "tiktok.auth_required",
                    code=code,
                    msg=data.get("msg", ""),
                    hint="TikTok Creative Center API now requires a TikTok Ads account "
                    "login session (code 40101 = no permission). "
                    "The tiktok adapter cannot fetch without authentication.",
                )
                return
            for item in (data.get("data") or {}).get("list") or []:
                if total_yielded >= limit or self.is_cancelled:
                    return
                yield {"type": "hashtag", **item}
                total_yielded += 1
        except httpx.HTTPStatusError as e:
            log.warning("tiktok.hashtags.failed", status=e.response.status_code)
        except httpx.RequestError as e:
            log.warning("tiktok.hashtags.request_error", error=str(e))

        # --- Trending videos ---
        if cfg.fetch_videos and total_yielded < limit and not self.is_cancelled:
            await self._rate_limit()
            self._record_request_metric(method="cc_videos")

            video_params: dict[str, Any] = {
                "page": 1,
                "limit": min(50, limit - total_yielded),
                "period": cfg.period_days,
                "country_code": cfg.country_code,
                "sort_by": "popular",
            }
            if query:
                video_params["keyword"] = query

            try:
                resp = await self._client.get(
                    f"{_CC_BASE}/video/list",
                    params=video_params,
                )
                resp.raise_for_status()
                data = resp.json()
                for item in (data.get("data") or {}).get("list") or []:
                    if total_yielded >= limit or self.is_cancelled:
                        return
                    yield {"type": "video", **item}
                    total_yielded += 1
            except httpx.HTTPStatusError as e:
                log.warning("tiktok.videos.failed", status=e.response.status_code)
            except httpx.RequestError as e:
                log.warning("tiktok.videos.request_error", error=str(e))

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            item_type = raw.get("type", "hashtag")
            if item_type == "hashtag":
                return self._parse_hashtag(raw)
            return self._parse_video(raw)
        except Exception as e:
            log.warning("tiktok.parse.failed", error=str(e))
            return None

    def _parse_hashtag(self, raw: dict[str, Any]) -> ProductSignal | None:
        hashtag_name: str = str(raw.get("hashtag_name") or raw.get("name") or "")
        if not hashtag_name:
            return None

        hashtag_id: str = str(raw.get("hashtag_id") or hashtag_name)
        view_count: int | None = _int_or_none(raw.get("video_views") or raw.get("view_count"))
        publish_count: int | None = _int_or_none(raw.get("publish_cnt") or raw.get("video_count"))

        tag_clean = hashtag_name.lower().lstrip("#")
        h = compute_content_hash(
            platform=Platform.TIKTOK,
            external_id=f"hashtag_{hashtag_id}",
            url=f"https://www.tiktok.com/tag/{tag_clean}",
            title=f"#{hashtag_name}",
            raw_text=None,
            posted_at=None,
        )

        return ProductSignal(
            platform=Platform.TIKTOK,
            tier=SourceTier.TIER_1_INTENT,
            external_id=f"hashtag_{hashtag_id}",
            url=f"https://www.tiktok.com/tag/{tag_clean}",  # type: ignore[arg-type]
            title=f"#{hashtag_name}",
            raw_text=None,
            modality=ContentModality.TEXT,
            tags=frozenset({tag_clean}),
            intent=IntentType.ENGAGE,
            engagement=EngagementMetrics(
                views=view_count,
                shares=publish_count,
            ),
            posted_at=None,
            provenance=ScrapeProvenance(
                method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                scraped_at=datetime.now(UTC),
                scraper_version=SCRAPER_VERSION,
                tos_risk=ToSRisk.AMBER,
            ),
            confidence=ConfidenceMetadata(
                completeness=0.6,
                source_confidence=0.70,
            ),
            content_hash=h,
            platform_specific={
                "hashtag_id": hashtag_id,
                "view_count": view_count,
                "publish_count": publish_count,
                "rank": raw.get("rank"),
            },
        )

    def _parse_video(self, raw: dict[str, Any]) -> ProductSignal | None:
        video_id: str = str(raw.get("item_id") or raw.get("video_id") or "")
        if not video_id:
            return None

        author_name: str = str(raw.get("author_name") or raw.get("nick_name") or "")
        author_id: str = str(raw.get("author_id") or author_name or "unknown")
        title: str = str(raw.get("video_description") or raw.get("title") or "")
        view_count: int | None = _int_or_none(raw.get("play_count") or raw.get("view_count"))
        like_count: int | None = _int_or_none(raw.get("digg_count") or raw.get("like_count"))
        comment_count: int | None = _int_or_none(raw.get("comment_count"))
        share_count: int | None = _int_or_none(raw.get("share_count"))

        url = _TT_VIDEO_BASE.format(author=author_name or "unknown", id=video_id)

        author: Author | None = None
        if author_id and author_id != "unknown":
            author = Author(
                platform_user_id=author_id,
                handle=author_name or author_id,
                profile_url=f"https://www.tiktok.com/@{author_name}",  # type: ignore[arg-type]
            )

        h = compute_content_hash(
            platform=Platform.TIKTOK,
            external_id=video_id,
            url=url,
            title=title,
            raw_text=None,
            posted_at=None,
        )

        return ProductSignal(
            platform=Platform.TIKTOK,
            tier=SourceTier.TIER_1_INTENT,
            external_id=video_id,
            url=url,  # type: ignore[arg-type]
            title=title or None,
            raw_text=None,
            modality=ContentModality.VIDEO,
            tags=frozenset(),
            intent=IntentType.PASSIVE,
            author=author,
            engagement=EngagementMetrics(
                views=view_count,
                likes=like_count,
                comments=comment_count,
                shares=share_count,
            ),
            posted_at=None,
            provenance=ScrapeProvenance(
                method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                scraped_at=datetime.now(UTC),
                scraper_version=SCRAPER_VERSION,
                tos_risk=ToSRisk.AMBER,
            ),
            confidence=ConfidenceMetadata(
                completeness=0.65,
                source_confidence=0.70,
            ),
            content_hash=h,
            platform_specific={
                "video_id": video_id,
                "author_id": author_id,
                "play_count": view_count,
                "digg_count": like_count,
                "rank": raw.get("rank"),
            },
        )


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


__all__ = ["SCRAPER_VERSION", "TikTokAdapter", "TikTokConfig"]
