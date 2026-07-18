"""NPM Trends adapter — registry search + monthly download counts.

Uses two public endpoints (no authentication):
  https://registry.npmjs.org/-/v1/search?text={keyword}&size=50&quality=1.0&popularity=1.0
  https://api.npmjs.org/downloads/point/last-month/{package_name}

score = downloads_last_month / 1000 (normalised).

ToS Risk: GREEN — official NPM public API.
Rate limit: 1 req/s — NPM registry is generous for public search.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlparse, urlunparse

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
from aegis.scrape.http_client import get_or_create_client
from aegis.scrape.schema_guard import validate_batch
from aegis.scrape.sentiment import score_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.npm_trends")

SCRAPER_VERSION = "npm-trends-0.1.0"

_SEARCH_BASE = "https://registry.npmjs.org/-/v1/search"
_DOWNLOADS_BASE = "https://api.npmjs.org/downloads/point/last-month"

_HEADERS = {
    "User-Agent": "AegisPulse/0.3 (market-intelligence; contact@aegis.dev)",
    "Accept": "application/json",
}


def _clean_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


@dataclass(frozen=True, slots=True)
class NPMTrendsConfig(AdapterConfig):
    name: str = "npm_trends"
    per_source_rps: float = 1.0
    timeout_seconds: float = 20.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class NPMTrendsAdapter(SourceAdapter[dict[str, Any]]):
    """NPM registry search + download counts — fully public, no API key required."""

    def __init__(self, config: NPMTrendsConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._npm_config = (
            config if isinstance(config, NPMTrendsConfig) else NPMTrendsConfig()
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "npm_trends"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "registry.npmjs.org",
            http2=False,
            timeout=self._npm_config.timeout_seconds,
            headers=_HEADERS,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        # Shared pooled client (PASS5-5B / ADP-7) — release the reference, never close.
        self._client = None

    async def _search_keyword(self, keyword: str, size: int = 50) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="json_api")
        try:
            resp = await self._client.get(
                _SEARCH_BASE,
                params={
                    "text": keyword,
                    "size": size,
                    "quality": "1.0",
                    "popularity": "1.0",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("objects") or []
        except httpx.HTTPStatusError as e:
            _log.warning("npm_trends.search.http_error", keyword=keyword, status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("npm_trends.search.error", keyword=keyword, error=str(e))
            return []

    async def _get_downloads(self, package_name: str) -> int:
        if self._client is None:
            return 0
        await self._rate_limit()
        self._record_request_metric(method="json_api")
        try:
            url = f"{_DOWNLOADS_BASE}/{quote(package_name, safe='@/')}"
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return int(data.get("downloads") or 0)
        except Exception:
            return 0

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        from aegis.config import settings as _settings

        keywords = _settings().npm_seed_keywords

        # Search all keywords concurrently
        search_results = await asyncio.gather(
            *[self._search_keyword(kw) for kw in keywords],
            return_exceptions=True,
        )

        seen_names: set[str] = set()
        packages: list[dict[str, Any]] = []
        for result in search_results:
            if isinstance(result, BaseException):
                _log.warning("npm_trends.search_gather_error", error=str(result))
                continue
            for obj in result:
                pkg = (obj.get("package") or {})
                name = pkg.get("name") or ""
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                packages.append(pkg)

        # Fetch download counts for the top candidates, limited to avoid hammering
        top_packages = packages[: min(limit, 100)]
        download_results = await asyncio.gather(
            *[self._get_downloads(p["name"]) for p in top_packages],
            return_exceptions=True,
        )

        scraped_at = datetime.now(UTC).isoformat()
        raws: list[dict[str, Any]] = []
        for pkg, dl_result in zip(top_packages, download_results, strict=False):
            downloads = dl_result if isinstance(dl_result, int) else 0
            name = pkg.get("name") or ""
            description = (pkg.get("description") or "").strip()
            pkg_url = f"https://www.npmjs.com/package/{name}"
            keywords_list: list[str] = pkg.get("keywords") or []
            version = pkg.get("version") or ""

            raws.append({
                "title": name,
                "url": pkg_url,
                "platform": Platform.NPM_TRENDS.value,
                "scraped_at": scraped_at,
                "author": (pkg.get("publisher") or {}).get("username"),
                "score": float(downloads) / 1000.0,
                "views": downloads or None,
                "likes": None,
                "comments": None,
                "shares": None,
                "saves": None,
                "sentiment": score_text(f"{name} {description}"),
                "raw_json": {
                    "description": description,
                    "keywords": keywords_list,
                    "version": version,
                    "downloads_last_month": downloads,
                },
            })

        valid = validate_batch(raws, Platform.NPM_TRENDS.value)
        # Sort by downloads descending before yielding
        valid.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
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
            keywords_list: list[str] = raw_json.get("keywords") or []
            version = str(raw_json.get("version") or "")
            downloads = int(raw_json.get("downloads_last_month") or 0)
            sentiment_val: float = raw.get("sentiment") or 0.0

            tags: frozenset[str] = frozenset(
                k.lower().replace(" ", "-") for k in keywords_list if k
            ) | {"npm"}

            external_id = hashlib.sha1(url.encode("utf-8", errors="replace")).hexdigest()[:24]  # noqa: S324

            h = compute_content_hash(
                platform=Platform.NPM_TRENDS,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=description[:2000] if description else None,
                posted_at=None,
            )

            return ProductSignal(
                platform=Platform.NPM_TRENDS,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=description[:2000] if description else None,
                modality=ContentModality.STRUCTURED,
                tags=tags,
                intent=IntentType.PASSIVE,
                engagement=EngagementMetrics(views=downloads if downloads else None),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.75 if description else 0.50,
                    source_confidence=0.90,
                ),
                content_hash=h,
                platform_specific={
                    "version": version,
                    "downloads_last_month": downloads,
                    "sentiment": sentiment_val,
                },
            )
        except Exception as e:
            _log.warning("npm_trends.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "NPMTrendsAdapter", "NPMTrendsConfig"]
