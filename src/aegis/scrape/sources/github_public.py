"""GitHub public search API adapter.

Searches GitHub repositories for topics from settings.github_topics.
Endpoint: https://api.github.com/search/repositories?q=topic:{topic}&sort=stars

CRITICAL: Checks X-RateLimit-Remaining header after each response.
          Returns early if remaining ≤ 5 to preserve headroom for other callers.

Requests are made sequentially (not concurrently) with settings.scrape_delay_s
between each topic to stay comfortably within the unauthenticated rate limit
(60 requests/hour).

score = stargazers_count, views = watchers_count

PLATFORM: github_public | TIER: T3_search
ToS Risk: GREEN — official GitHub REST API, unauthenticated.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import httpx
import structlog

from aegis.config import settings
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

_log = structlog.get_logger("aegis.scrape.github_public")

SCRAPER_VERSION = "github-public-0.1.0"

_SEARCH_URL = "https://api.github.com/search/repositories"
_HEADERS = {
    "User-Agent": "AegisPulse/0.3 (market-intelligence)",
    "Accept": "application/vnd.github.v3+json",
}
_RATE_LIMIT_STOP_AT = 5


def _clean_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


@dataclass(frozen=True, slots=True)
class GitHubPublicConfig(AdapterConfig):
    name: str = "github_public"
    per_source_rps: float = 0.2  # 1 req / 5 s — safe for unauthenticated
    timeout_seconds: float = 20.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class GitHubPublicAdapter(SourceAdapter[dict[str, Any]]):
    """GitHub search API adapter — topic-based repo discovery."""

    def __init__(self, config: GitHubPublicConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "github_public"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers=_HEADERS,
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
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            return

        topics = settings().github_topics
        cfg_delay = settings().scrape.scrape_delay_s

        seen_ids: set[int] = set()
        raws: list[dict[str, Any]] = []

        for topic in topics:
            if self.is_cancelled or len(raws) >= limit:
                break

            await self._rate_limit()
            self._record_request_metric(method="json_api")

            try:
                resp = await self._client.get(
                    _SEARCH_URL,
                    params={"q": f"topic:{topic}", "sort": "stars", "per_page": 30},
                )
            except httpx.RequestError as e:
                _log.warning("github_public.request_error", topic=topic, error=str(e))
                await asyncio.sleep(cfg_delay)
                continue

            # Rate-limit guard — stop early to protect other callers
            remaining = int(resp.headers.get("X-RateLimit-Remaining", "999"))
            if remaining <= _RATE_LIMIT_STOP_AT:
                _log.warning(
                    "github_public.rate_limit_low",
                    remaining=remaining,
                    stop_at=_RATE_LIMIT_STOP_AT,
                )
                break

            if resp.status_code != 200:
                _log.warning(
                    "github_public.http_error", topic=topic, status=resp.status_code
                )
                await asyncio.sleep(cfg_delay)
                continue

            try:
                data = resp.json()
                items: list[dict[str, Any]] = data.get("items") or []
            except Exception as e:
                _log.warning("github_public.json_error", topic=topic, error=str(e))
                await asyncio.sleep(cfg_delay)
                continue

            for repo in items:
                repo_id: int = repo.get("id") or 0
                if repo_id in seen_ids:
                    continue
                if repo_id:
                    seen_ids.add(repo_id)

                name = repo.get("name") or ""
                full_name = repo.get("full_name") or name
                description = (repo.get("description") or "").strip()
                html_url = _clean_url(repo.get("html_url") or "")
                stars = int(repo.get("stargazers_count") or 0)
                watchers = int(repo.get("watchers_count") or 0)
                topics_list: list[str] = repo.get("topics") or []
                language = repo.get("language") or None

                title = f"{full_name}: {description}" if description else full_name

                raws.append({
                    "title": title[:512],
                    "url": html_url,
                    "platform": Platform.GITHUB_PUBLIC.value,
                    "scraped_at": datetime.now(UTC).isoformat(),
                    "author": (repo.get("owner") or {}).get("login") or None,
                    "score": float(stars),
                    "views": watchers,
                    "likes": stars,
                    "comments": None,
                    "shares": None,
                    "saves": None,
                    "sentiment": score_text(f"{title} {description}"),
                    "raw_json": {
                        "repo_id": repo_id,
                        "full_name": full_name,
                        "description": description,
                        "topics": topics_list,
                        "language": language,
                        "pushed_at": repo.get("pushed_at") or "",
                        "search_topic": topic,
                    },
                })

            await asyncio.sleep(cfg_delay)

        valid = validate_batch(raws, Platform.GITHUB_PUBLIC.value)
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
            topics_list: list[str] = raw_json.get("topics") or []
            language = raw_json.get("language")
            repo_id = raw_json.get("repo_id")
            pushed_at_str = raw_json.get("pushed_at") or ""
            stars = int(raw.get("likes") or 0)
            watchers = int(raw.get("views") or 0)
            sentiment_val: float = raw.get("sentiment") or 0.0

            posted_at: datetime | None = None
            if pushed_at_str:
                with contextlib.suppress(ValueError):
                    posted_at = datetime.fromisoformat(
                        str(pushed_at_str).replace("Z", "+00:00")
                    )

            tags: frozenset[str] = frozenset(
                t.lower().replace(" ", "-") for t in topics_list if t
            ) | {"github"}
            if language:
                lang_tag = language.lower().replace(" ", "-").replace("#", "sharp")
                tags = tags | {lang_tag}

            external_id = (
                str(repo_id)
                if repo_id
                else hashlib.sha1(url.encode()).hexdigest()[:24]  # noqa: S324
            )

            h = compute_content_hash(
                platform=Platform.GITHUB_PUBLIC,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=description[:2000] if description else None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.GITHUB_PUBLIC,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=description[:2000] if description else None,
                modality=ContentModality.STRUCTURED,
                tags=tags,
                intent=IntentType.SEARCH,
                engagement=EngagementMetrics(
                    likes=stars,
                    views=watchers,
                ),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.85 if (title and description) else 0.60,
                    source_confidence=0.90,
                ),
                content_hash=h,
                platform_specific={
                    "repo_id": repo_id,
                    "language": language,
                    "stars": stars,
                    "watchers": watchers,
                    "sentiment": sentiment_val,
                },
            )
        except Exception as e:
            _log.warning("github_public.parse.failed", error=str(e))
            return None


__all__ = ["GitHubPublicAdapter", "GitHubPublicConfig", "SCRAPER_VERSION"]
