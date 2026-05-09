"""Hacker News source adapter.

Uses the Algolia-powered HN Search API (https://hn.algolia.com/api/v1/).
This is the officially recommended way to search HN — free, no auth required.

ToS Risk: GREEN — publicly documented, no rate limit restrictions for
reasonable usage (we stay at ≤ 2 req/s).
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

SCRAPER_VERSION = "hacker-news-0.1.0"

_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"
_HN_ITEM_BASE = "https://news.ycombinator.com/item"
_HN_USER_BASE = "https://news.ycombinator.com/user"


@dataclass(frozen=True, slots=True)
class HackerNewsConfig(AdapterConfig):
    """HackerNews adapter config."""

    name: str = "hacker-news"
    per_source_rps: float = 2.0
    timeout_seconds: float = 15.0
    max_retries: int = 3
    use_cloudflare_bypass: bool = False

    search_by_date: bool = True
    """If True use /search_by_date (newest-first); else /search (relevance)."""

    min_points: int = 0
    """Filter out stories below this upvote score."""

    tags: str = "story"
    """Algolia tags filter. ``story`` | ``ask_hn`` | ``show_hn`` | ``comment``."""


class HackerNewsAdapter(SourceAdapter[dict[str, Any]]):
    """HN adapter using the Algolia HN Search API."""

    def __init__(self, config: HackerNewsConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._hn_config = config if isinstance(config, HackerNewsConfig) else HackerNewsConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "hacker-news"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._hn_config.timeout_seconds),
            headers={
                "User-Agent": "aegis-pulse/0.1.0 (market intelligence; not a scraper)",
                "Accept": "application/json",
            },
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        query: str = "",
        limit: int = 100,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("HackerNewsAdapter.setup() must run before fetch_raw()")

        cfg = self._hn_config
        endpoint = (
            f"{_ALGOLIA_BASE}/search_by_date"
            if cfg.search_by_date
            else f"{_ALGOLIA_BASE}/search"
        )

        page = 0
        total_yielded = 0

        while total_yielded < limit and not self.is_cancelled:
            await self._rate_limit()
            self._record_request_metric(method="algolia_api")

            params: dict[str, Any] = {
                "tags": cfg.tags,
                "hitsPerPage": min(50, limit - total_yielded),
                "page": page,
            }
            if query:
                params["query"] = query

            try:
                resp = await self._client.get(endpoint, params=params)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                log.warning("hn.fetch.http_error", status=e.response.status_code, page=page)
                break
            except httpx.RequestError as e:
                log.warning("hn.fetch.request_error", error=str(e), page=page)
                break

            hits: list[dict[str, Any]] = data.get("hits", [])
            if not hits:
                break

            for hit in hits:
                if total_yielded >= limit or self.is_cancelled:
                    return
                if cfg.min_points > 0 and (hit.get("points") or 0) < cfg.min_points:
                    continue
                yield hit
                total_yielded += 1

            if len(hits) < params["hitsPerPage"]:
                break
            page += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            obj_id: str = str(raw.get("objectID") or "")
            if not obj_id:
                return None

            title: str = str(raw.get("title") or "")
            url: str | None = raw.get("url")
            author_name: str | None = raw.get("author")
            points: int = int(raw.get("points") or 0)
            num_comments: int = int(raw.get("num_comments") or 0)
            created_at_str: str | None = raw.get("created_at")

            posted_at: datetime | None = None
            if created_at_str:
                with contextlib.suppress(ValueError):
                    posted_at = datetime.fromisoformat(
                        created_at_str.replace("Z", "+00:00")
                    )

            hn_url = f"{_HN_ITEM_BASE}?id={obj_id}"

            author: Author | None = None
            if author_name:
                author = Author(
                    platform_user_id=author_name,
                    handle=author_name,
                    profile_url=f"{_HN_USER_BASE}?id={author_name}",  # type: ignore[arg-type]
                )

            story_tags: list[str] = raw.get("_tags") or []

            h = compute_content_hash(
                platform=Platform.HACKER_NEWS,
                external_id=obj_id,
                url=url or hn_url,
                title=title,
                raw_text=None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.HACKER_NEWS,
                tier=SourceTier.TIER_5_ALTERNATIVE,
                external_id=obj_id,
                url=(url or hn_url),  # type: ignore[arg-type]
                title=title or None,
                raw_text=None,
                modality=ContentModality.TEXT,
                tags=frozenset({"hacker_news"}),
                intent=IntentType.ENGAGE,
                author=author,
                engagement=EngagementMetrics(
                    likes=max(0, points),
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
                    completeness=0.85 if title else 0.5,
                    source_confidence=0.90,
                ),
                content_hash=h,
                platform_specific={
                    "points": points,
                    "num_comments": num_comments,
                    "story_tags": story_tags,
                },
            )
        except Exception as e:
            log.warning("hn.parse.failed", error=str(e))
            return None


__all__ = ["HackerNewsAdapter", "HackerNewsConfig", "SCRAPER_VERSION"]
