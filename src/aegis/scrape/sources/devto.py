"""Dev.to public JSON API adapter.

Fetches articles from two endpoints and merges by article ID:
  https://dev.to/api/articles?top=1          — top-voted all-time
  https://dev.to/api/articles?per_page=50&page=1  — recent popular

No API key required. Rate limit is generous for read-only access.

PLATFORM: devto | TIER: T1_intent
ToS Risk: GREEN — official public API with no authentication.
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

_log = structlog.get_logger("aegis.scrape.devto")

SCRAPER_VERSION = "devto-0.1.0"

_BASE = "https://dev.to/api/articles"
_ENDPOINTS = [
    f"{_BASE}?top=1",
    f"{_BASE}?per_page=50&page=1",
]
_HEADERS = {
    "User-Agent": "AegisPulse/0.3 (market-intelligence; contact@aegis.dev)",
    "Accept": "application/json",
}


def _clean_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


@dataclass(frozen=True, slots=True)
class DevToConfig(AdapterConfig):
    name: str = "devto"
    per_source_rps: float = 1.0
    timeout_seconds: float = 20.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class DevToAdapter(SourceAdapter[dict[str, Any]]):
    """Dev.to adapter — two public API endpoints merged and deduplicated by article ID."""

    def __init__(self, config: DevToConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "devto"

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

    async def _fetch_endpoint(self, url: str) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        try:
            await self._rate_limit()
            self._record_request_metric(method="json_api")
            resp = await self._client.get(url)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as e:
            _log.warning("devto.http_error", url=url, status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("devto.request_error", url=url, error=str(e))
            return []

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        batches = await asyncio.gather(*[self._fetch_endpoint(u) for u in _ENDPOINTS])

        seen_ids: set[int] = set()
        raws: list[dict[str, Any]] = []
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for article in batch:
                article_id = article.get("id")
                if article_id is None or article_id in seen_ids:
                    continue
                seen_ids.add(article_id)

                title = (article.get("title") or "").strip()
                url = _clean_url(article.get("url") or article.get("canonical_url") or "")
                description = (article.get("description") or "").strip()
                author = (article.get("user", {}) or {}).get("name") or None
                tags_raw: list[str] = article.get("tag_list") or []

                raws.append({
                    "title": title,
                    "url": url,
                    "platform": Platform.DEVTO.value,
                    "scraped_at": datetime.now(UTC).isoformat(),
                    "author": author,
                    "score": float(article.get("reactions_count") or 0),
                    "views": None,
                    "likes": int(article.get("reactions_count") or 0),
                    "comments": int(article.get("comments_count") or 0),
                    "shares": None,
                    "saves": None,
                    "sentiment": score_text(f"{title} {description}"),
                    "raw_json": {
                        "article_id": article_id,
                        "description": description,
                        "tags": tags_raw,
                        "published_at": article.get("published_at") or "",
                        "reading_time_minutes": article.get("reading_time_minutes"),
                    },
                })

        valid = validate_batch(raws, Platform.DEVTO.value)
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
            tags_raw: list[str] = raw_json.get("tags") or []
            published_str = raw_json.get("published_at") or ""
            article_id = raw_json.get("article_id")
            reactions = int(raw.get("likes") or 0)
            comments = int(raw.get("comments") or 0)
            sentiment_val: float = raw.get("sentiment") or 0.0

            posted_at: datetime | None = None
            if published_str:
                with contextlib.suppress(ValueError):
                    posted_at = datetime.fromisoformat(
                        str(published_str).replace("Z", "+00:00")
                    )

            tags: frozenset[str] = frozenset(
                t.lower().replace(" ", "-") for t in tags_raw if t
            ) | {"devto"}

            external_id = (
                str(article_id)
                if article_id
                else hashlib.sha1(url.encode()).hexdigest()[:24]  # noqa: S324
            )

            h = compute_content_hash(
                platform=Platform.DEVTO,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=description[:2000] if description else None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.DEVTO,
                tier=SourceTier.TIER_1_INTENT,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=description[:2000] if description else None,
                modality=ContentModality.TEXT,
                tags=tags,
                intent=IntentType.ENGAGE,
                engagement=EngagementMetrics(
                    likes=reactions,
                    comments=comments,
                ),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.80 if (title and posted_at) else 0.55,
                    source_confidence=0.90,
                ),
                content_hash=h,
                platform_specific={
                    "article_id": article_id,
                    "reactions_count": reactions,
                    "comments_count": comments,
                    "sentiment": sentiment_val,
                },
            )
        except Exception as e:
            _log.warning("devto.parse.failed", error=str(e))
            return None


__all__ = ["DevToAdapter", "DevToConfig", "SCRAPER_VERSION"]
