"""Reddit Finance adapter — r/IndiaInvestments, r/wallstreetbets, and related subs.

Uses Reddit's public JSON API (no credentials required).
Critical: gzip-only Accept-Encoding, http2=False, descriptive User-Agent.
Fetches all subreddits concurrently via asyncio.gather limited by ConcurrencyGovernor.

ToS Risk: GREEN — publicly documented JSON endpoint.
Rate limit: 1 req/3 s across all subreddits combined.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
    Author,
    ConfidenceMetadata,
    EngagementMetrics,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter
from aegis.scrape.sentiment import score_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.reddit_finance")

SCRAPER_VERSION = "reddit-finance-0.1.0"

_REDDIT_BASE = "https://www.reddit.com"
_USER_AGENT = "AEGIS-Pulse/1.0 (market-intelligence-bot)"
_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}

_DEFAULT_SUBREDDITS: tuple[str, ...] = (
    "IndiaInvestments",
    "IndianStockMarket",
    "Zerodha",
    "mutualfunds",
    "personalfinance",
    "investing",
    "stocks",
    "wallstreetbets",
    "cryptocurrency",
    "Bitcoin",
    "SecurityAnalysis",
)


@dataclass(frozen=True, slots=True)
class RedditFinanceConfig(AdapterConfig):
    name: str = "reddit_finance"
    per_source_rps: float = 0.33
    timeout_seconds: float = 20.0
    max_retries: int = 3
    use_cloudflare_bypass: bool = False
    subreddits: tuple[str, ...] = field(default=_DEFAULT_SUBREDDITS)
    listing: str = "hot"
    posts_per_sub: int = 25


class RedditFinanceAdapter(SourceAdapter[dict[str, Any]]):
    """Reddit finance subreddits via public JSON API."""

    def __init__(self, config: RedditFinanceConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._rf_config = (
            config if isinstance(config, RedditFinanceConfig) else RedditFinanceConfig()
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "reddit_finance"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._rf_config.timeout_seconds),
            headers=_HEADERS,
            follow_redirects=True,
            http2=False,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch_subreddit(self, sub: str, per_sub: int) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        url = f"{_REDDIT_BASE}/r/{sub}/{self._rf_config.listing}.json"
        await self._rate_limit()
        self._record_request_metric(method="reddit_json")
        try:
            resp = await self._client.get(url, params={"limit": min(100, per_sub), "raw_json": "1"})
            if resp.status_code == 429:
                _log.warning("reddit_finance.rate_limited", subreddit=sub)
                return []
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except httpx.HTTPStatusError as e:
            _log.warning("reddit_finance.http_error", status=e.response.status_code, sub=sub)
            return []
        except Exception as e:
            _log.warning("reddit_finance.request_error", error=str(e), sub=sub)
            return []

        posts = []
        for wrapper in (data.get("data") or {}).get("children") or []:
            post = wrapper.get("data") or {}
            post["_subreddit_override"] = sub
            posts.append(post)
        return posts

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        cfg = self._rf_config
        per_sub = max(1, limit // max(1, len(cfg.subreddits)))

        results = await asyncio.gather(
            *[self._fetch_subreddit(sub, per_sub) for sub in cfg.subreddits],
            return_exceptions=True,
        )

        total = 0
        for result in results:
            if isinstance(result, BaseException):
                _log.warning("reddit_finance.gather_error", error=str(result))
                continue
            for post in result:
                if total >= limit or self.is_cancelled:
                    return
                yield post
                total += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            post_id = str(raw.get("id") or "")
            if not post_id:
                return None

            if raw.get("over_18"):
                return None

            external_id = f"t3_{post_id}"
            title = str(raw.get("title") or "")
            selftext = str(raw.get("selftext") or "")
            subreddit = str(raw.get("subreddit") or raw.get("_subreddit_override") or "")
            author_name = str(raw.get("author") or "")
            score = int(raw.get("score") or 0)
            num_comments = int(raw.get("num_comments") or 0)
            permalink = raw.get("permalink")
            url = raw.get("url")
            created_utc = raw.get("created_utc")
            is_self = bool(raw.get("is_self", False))

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

            sentiment = score_text(title + " " + (selftext[:500] if selftext else ""))

            h = compute_content_hash(
                platform=Platform.REDDIT_FINANCE,
                external_id=external_id,
                url=canonical_url,
                title=title[:512] if title else None,
                raw_text=raw_text,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.REDDIT_FINANCE,
                tier=SourceTier.TIER_1_INTENT,
                external_id=external_id,
                url=canonical_url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=raw_text,
                modality=ContentModality.TEXT,
                tags=frozenset({subreddit.lower()} if subreddit else ()),
                intent=IntentType.ENGAGE,
                author=author,
                engagement=EngagementMetrics(likes=score, comments=num_comments),
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
                    "sentiment": sentiment,
                },
            )
        except Exception as e:
            _log.warning("reddit_finance.parse.failed", error=str(e))
            return None


__all__ = ["RedditFinanceAdapter", "RedditFinanceConfig", "SCRAPER_VERSION", "_USER_AGENT"]
