"""Instagram source adapter.

Uses instaloader to scrape public hashtag posts.
No credentials required for public content (anonymous session).

ToS Risk: RED — Instagram actively defends against scraping.
This adapter is gated behind ``allow_red_tos=True`` in the config.
For production use, configure session cookies via
``AEGIS_INSTAGRAM_SESSION_COOKIE`` to reduce ban risk.

Setup:
  1. Ensure instaloader is installed: ``uv sync --extra scrape``
  2. Set ``AEGIS_INSTAGRAM_SESSION_COOKIE`` (optional but recommended):
       python -c "import instaloader; L = instaloader.Instaloader();
                  L.interactive_login('username')"
       Then copy the session file to the configured path.
  3. Set ``allow_red_tos=True`` when constructing ``InstagramConfig``.
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
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)

SCRAPER_VERSION = "instagram-0.1.0"
_IG_POST_BASE = "https://www.instagram.com/p/"
_IG_USER_BASE = "https://www.instagram.com/"


@dataclass(frozen=True, slots=True)
class InstagramConfig(AdapterConfig):
    """Instagram adapter config."""

    name: str = "instagram"
    per_source_rps: float = 0.1  # very conservative to avoid bans
    timeout_seconds: float = 60.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    mobile_user_agent: bool = True

    allow_red_tos: bool = False
    """Must be explicitly set to True to acknowledge ToS RED risk."""

    session_file: str = ""
    """Path to an instaloader session file for cookie reuse (reduces ban risk)."""


class InstagramAdapter(SourceAdapter[Any]):
    """Instagram adapter using instaloader."""

    parse_is_blocking: bool = True

    def __init__(self, config: InstagramConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._ig_config = config if isinstance(config, InstagramConfig) else InstagramConfig()
        self._loader: Any = None

    @property
    def name(self) -> str:
        return "instagram"

    async def setup(self, ctx: ScrapeContext) -> None:
        cfg = self._ig_config
        if not cfg.allow_red_tos:
            raise RuntimeError(
                "InstagramAdapter is gated behind allow_red_tos=True.\n"
                "Instagram actively defends against scraping (ToS RED).\n"
                "If you accept the legal risk, set allow_red_tos=True in\n"
                "InstagramConfig when constructing the adapter."
            )

        try:
            import instaloader  # type: ignore[import-untyped]
        except ImportError as e:
            raise RuntimeError(
                "InstagramAdapter requires instaloader. " "Run: uv sync --extra scrape"
            ) from e

        def _build() -> Any:
            loader = instaloader.Instaloader(
                quiet=True,
                download_pictures=False,
                download_videos=False,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                max_connection_attempts=1,
            )
            if cfg.session_file:
                try:
                    loader.load_session_from_file(cfg.session_file)
                    log.info("instagram.session.loaded", path=cfg.session_file)
                except Exception as e:
                    log.warning("instagram.session.load_failed", error=str(e))
            return loader

        self._loader = await asyncio.to_thread(_build)

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        hashtag: str | None = None,
        query: str | None = None,
        limit: int = 30,
        **_: Any,
    ) -> AsyncIterator[Any]:
        if self._loader is None:
            raise RuntimeError("InstagramAdapter.setup() must run before fetch_raw()")

        target = hashtag or query or "trending"
        import instaloader  # type: ignore[import-untyped]

        def _fetch_posts(tag: str) -> list[Any]:
            try:
                ht = instaloader.Hashtag.from_name(self._loader.context, tag)
                posts = []
                for post in ht.get_posts():
                    posts.append(post)
                    if len(posts) >= limit:
                        break
                return posts
            except Exception as e:
                log.warning("instagram.fetch.failed", error=str(e), hashtag=tag)
                return []

        await self._rate_limit()
        self._record_request_metric(method="instaloader")
        posts = await asyncio.to_thread(_fetch_posts, target)

        for post in posts:
            if self.is_cancelled:
                return
            yield post

    def parse(self, raw: Any, ctx: ScrapeContext) -> ProductSignal | None:
        try:
            shortcode: str = str(getattr(raw, "shortcode", "") or "")
            if not shortcode:
                return None

            caption: str = str(getattr(raw, "caption", "") or "")
            likes: int = int(getattr(raw, "likes", 0) or 0)
            comments: int = int(getattr(raw, "comments", 0) or 0)
            is_video: bool = bool(getattr(raw, "is_video", False))
            date: Any = getattr(raw, "date_utc", None)

            posted_at: datetime | None = None
            if date is not None and isinstance(date, datetime):
                posted_at = date.replace(tzinfo=UTC)

            owner = getattr(raw, "owner_profile", None)
            author: Author | None = None
            if owner is not None:
                with contextlib.suppress(Exception):
                    uid = str(getattr(owner, "userid", "") or "")
                    handle = str(getattr(owner, "username", "") or "")
                    followers = getattr(owner, "followers", None)
                    author = Author(
                        platform_user_id=uid or handle,
                        handle=handle or None,
                        follower_count=int(followers) if followers is not None else None,
                        profile_url=f"{_IG_USER_BASE}{handle}/",  # type: ignore[arg-type]
                    )

            url = f"{_IG_POST_BASE}{shortcode}/"
            tagged: list[str] = []
            with contextlib.suppress(Exception):
                tagged = list(getattr(raw, "tagged_users", []) or [])

            h = compute_content_hash(
                platform=Platform.INSTAGRAM,
                external_id=shortcode,
                url=url,
                title=None,
                raw_text=caption or None,
                posted_at=posted_at,
            )

            modality = ContentModality.VIDEO if is_video else ContentModality.IMAGE

            return ProductSignal(
                platform=Platform.INSTAGRAM,
                tier=SourceTier.TIER_1_INTENT,
                external_id=shortcode,
                url=url,  # type: ignore[arg-type]
                title=None,
                raw_text=caption or None,
                modality=modality,
                tags=frozenset(),
                intent=IntentType.PASSIVE,
                author=author,
                engagement=EngagementMetrics(
                    likes=likes,
                    comments=comments,
                ),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.RED,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.7,
                    source_confidence=0.75,
                ),
                content_hash=h,
                platform_specific={
                    "shortcode": shortcode,
                    "is_video": is_video,
                    "tagged_users": tagged[:10],
                },
            )
        except Exception as e:
            log.warning("instagram.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "InstagramAdapter", "InstagramConfig"]
