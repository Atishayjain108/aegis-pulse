"""Reddit public JSON API adapter (no API credentials required).

Reddit exposes public JSON endpoints for every subreddit:
  https://www.reddit.com/r/{subreddit}/{listing}.json
  https://www.reddit.com/r/popular.json
  https://www.reddit.com/r/all.json

No client_id or client_secret required — only a descriptive User-Agent.
Reddit's robots.txt explicitly allows this endpoint for reasonable usage.

ToS Risk: GREEN — publicly documented JSON endpoint, no auth required.
Rate limit: stay at 1 req/3 s to stay well under Reddit's 60 req/min cap.
"""

from __future__ import annotations

import contextlib
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

SCRAPER_VERSION = "reddit-json-0.2.0"

_REDDIT_BASE = "https://www.reddit.com"

_VALID_LISTINGS = ("hot", "new", "top", "rising", "best")


@dataclass(frozen=True, slots=True)
class RedditRSSConfig(AdapterConfig):
    """Reddit JSON API adapter config — no API credentials needed."""

    name: str = "reddit-rss"
    per_source_rps: float = 0.33   # 1 req per 3 seconds
    timeout_seconds: float = 20.0
    max_retries: int = 3
    use_cloudflare_bypass: bool = False

    listing: str = "hot"
    """Feed type: 'hot' | 'new' | 'top' | 'rising' | 'best'."""

    subreddits: tuple[str, ...] = ("popular",)
    """Subreddits to scrape. Use 'popular' or 'all' for site-wide feeds."""


class RedditRSSAdapter(SourceAdapter[dict[str, Any]]):
    """Reddit via public JSON API — no credentials required.

    Run with::

        adapter = RedditRSSAdapter(RedditRSSConfig(
            subreddits=("BuyItForLife", "frugalmalefashion"),
            listing="hot",
        ))
        async for signal in adapter.run(limit=20):
            ...
    """

    def __init__(self, config: RedditRSSConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._rss_config = config if isinstance(config, RedditRSSConfig) else RedditRSSConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "reddit-rss"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._rss_config.timeout_seconds),
            headers={
                # Reddit requires a descriptive User-Agent or returns 429/403.
                # Must NOT send 'br' in Accept-Encoding — Reddit's CDN blocks it.
                "User-Agent": "python:aegis-pulse:v0.1.0 (market research; non-commercial)",
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=True,
            http2=False,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        subreddit: str | None = None,
        limit: int = 50,
        listing: str | None = None,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("RedditRSSAdapter.setup() must run before fetch_raw()")

        cfg = self._rss_config
        listing_kind = listing or cfg.listing
        if listing_kind not in _VALID_LISTINGS:
            listing_kind = "hot"

        subreddits = [subreddit] if subreddit else list(cfg.subreddits)
        total_yielded = 0
        per_sub = max(1, limit // max(1, len(subreddits)))

        for sub in subreddits:
            if total_yielded >= limit or self.is_cancelled:
                return

            if sub in ("popular", "all"):
                feed_url = f"{_REDDIT_BASE}/r/{sub}.json"
            else:
                feed_url = f"{_REDDIT_BASE}/r/{sub}/{listing_kind}.json"

            await self._rate_limit()
            self._record_request_metric(method="reddit_json")

            try:
                resp = await self._client.get(
                    feed_url,
                    params={"limit": min(100, per_sub), "raw_json": "1"},
                )
                if resp.status_code == 429:
                    log.warning("reddit_rss.rate_limited", subreddit=sub)
                    break
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
            except httpx.HTTPStatusError as e:
                log.warning("reddit_rss.http_error", status=e.response.status_code, sub=sub)
                continue
            except httpx.RequestError as e:
                log.warning("reddit_rss.request_error", error=str(e), sub=sub)
                continue
            except Exception as e:
                log.warning("reddit_rss.json_parse_error", error=str(e), sub=sub)
                continue

            posts = (data.get("data") or {}).get("children") or []
            for post_wrapper in posts:
                if total_yielded >= limit or self.is_cancelled:
                    return
                post_data = post_wrapper.get("data") or {}
                post_data["_subreddit_override"] = sub
                yield post_data
                total_yielded += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            post_id: str = str(raw.get("id") or "")
            if not post_id:
                return None

            external_id = f"t3_{post_id}"
            title: str = str(raw.get("title") or "")
            selftext: str = str(raw.get("selftext") or "")
            subreddit: str = str(raw.get("subreddit") or raw.get("_subreddit_override") or "")
            author_name: str = str(raw.get("author") or "")
            score: int = int(raw.get("score") or 0)
            num_comments: int = int(raw.get("num_comments") or 0)
            url: str | None = raw.get("url")
            permalink: str | None = raw.get("permalink")
            created_utc: float | None = raw.get("created_utc")
            is_self: bool = bool(raw.get("is_self", False))
            nsfw: bool = bool(raw.get("over_18", False))

            if nsfw:
                return None

            canonical_url = (
                f"https://www.reddit.com{permalink}"
                if permalink
                else (url or f"https://www.reddit.com/comments/{post_id}/")
            )

            posted_at: datetime | None = None
            if created_utc is not None:
                with contextlib.suppress(ValueError, OSError):
                    posted_at = datetime.fromtimestamp(float(created_utc), tz=UTC)

            author: Author | None = None
            if author_name and author_name not in ("", "[deleted]"):
                author = Author(
                    platform_user_id=author_name,
                    handle=author_name,
                    profile_url=f"https://reddit.com/user/{author_name}",  # type: ignore[arg-type]
                )

            raw_text: str | None = None
            if is_self and selftext and selftext not in ("[deleted]", "[removed]"):
                raw_text = selftext[:20_000]
            elif not is_self and url:
                raw_text = url

            tags = frozenset({subreddit.lower()} if subreddit else ())

            h = compute_content_hash(
                platform=Platform.REDDIT,
                external_id=external_id,
                url=canonical_url,
                title=title[:512] if title else None,
                raw_text=raw_text,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.REDDIT,
                tier=SourceTier.TIER_1_INTENT,
                external_id=external_id,
                url=canonical_url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=raw_text,
                modality=ContentModality.TEXT,
                tags=tags,
                intent=IntentType.ENGAGE,
                author=author,
                engagement=EngagementMetrics(
                    likes=score,
                    comments=num_comments,
                ),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.80 if title else 0.40,
                    source_confidence=0.90,
                ),
                content_hash=h,
                platform_specific={
                    "subreddit": subreddit,
                    "post_id": post_id,
                    "score": score,
                    "num_comments": num_comments,
                    "is_self": is_self,
                },
            )
        except Exception as e:
            log.warning("reddit_rss.parse.failed", error=str(e))
            return None


__all__ = ["RedditRSSAdapter", "RedditRSSConfig", "SCRAPER_VERSION"]
