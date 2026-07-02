"""ProductHunt HTML scraper — today's top products from the public homepage.

Primary URL:  https://www.producthunt.com
Fallback URL: https://www.producthunt.com/best/today

v1 API frequently returns 401 without an API key, so we parse public HTML
instead. BeautifulSoup + lxml parses the rendered product cards.

IMPORTANT: 3-second delay between page requests to avoid hammering the site.

ToS Risk: AMBER — public page, scraping tolerated in practice.
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
from aegis.scrape.http_client import get_or_create_client
from aegis.scrape.schema_guard import validate_batch
from aegis.scrape.sentiment import score_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.producthunt")

SCRAPER_VERSION = "producthunt-0.1.0"

_PRIMARY_URL = "https://www.producthunt.com"
_FALLBACK_URL = "https://www.producthunt.com/best/today"
_PAGE_DELAY_S = 3.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


def _clean_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def _parse_page(html: str) -> list[dict[str, Any]]:
    """Extract product cards from ProductHunt HTML. Returns [] on any parse error."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("producthunt.missing_dependency", dep="beautifulsoup4")
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
        raws: list[dict[str, Any]] = []
        scraped_at = datetime.now(UTC).isoformat()

        # Product cards: <li data-test="post-item"> or anchors with /posts/ hrefs
        for item in soup.find_all("li", attrs={"data-test": "post-item"})[:50]:
            # Title: first heading or the main anchor text
            title_tag = item.find(["h3", "h2", "strong"])
            title = title_tag.get_text(strip=True) if title_tag else ""

            # Link
            link_tag = item.find("a", href=lambda h: h and "/posts/" in h)
            href = link_tag.get("href", "") if link_tag else ""
            product_url = (
                f"https://www.producthunt.com{href}" if href.startswith("/") else href
            )

            # Upvotes: aria-label="N votes" or data-vote-count
            upvotes = 0
            vote_tag = item.find(attrs={"data-vote-count": True})
            if vote_tag:
                with contextlib.suppress(ValueError, KeyError):
                    upvotes = int(vote_tag["data-vote-count"])
            if not upvotes:
                for el in item.find_all(attrs={"aria-label": True}):
                    lbl = str(el.get("aria-label", ""))
                    if "vote" in lbl.lower():
                        parts = lbl.split()
                        if parts and parts[0].isdigit():
                            upvotes = int(parts[0])
                            break

            if not title or not product_url:
                continue

            raws.append({
                "title": title,
                "url": _clean_url(product_url),
                "platform": Platform.PRODUCT_HUNT.value,
                "scraped_at": scraped_at,
                "author": None,
                "score": float(upvotes),
                "views": None,
                "likes": upvotes or None,
                "comments": None,
                "shares": None,
                "saves": None,
                "sentiment": score_text(title),
                "raw_json": {"upvotes": upvotes, "source": "homepage_html"},
            })

        # Fallback: if structured items not found, look for any /posts/ links
        if not raws:
            for link in soup.find_all("a", href=lambda h: h and "/posts/" in h)[:30]:
                title = link.get_text(strip=True)
                href = str(link.get("href", ""))
                product_url = (
                    f"https://www.producthunt.com{href}" if href.startswith("/") else href
                )
                if not title or not product_url:
                    continue
                raws.append({
                    "title": title[:256],
                    "url": _clean_url(product_url),
                    "platform": Platform.PRODUCT_HUNT.value,
                    "scraped_at": scraped_at,
                    "author": None,
                    "score": 0.0,
                    "views": None,
                    "likes": None,
                    "comments": None,
                    "shares": None,
                    "saves": None,
                    "sentiment": score_text(title),
                    "raw_json": {"upvotes": 0, "source": "link_fallback"},
                })

        return raws
    except Exception as e:
        _log.warning("producthunt.parse_page.failed", error=str(e))
        return []


@dataclass(frozen=True, slots=True)
class ProductHuntConfig(AdapterConfig):
    name: str = "producthunt"
    per_source_rps: float = 0.2
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class ProductHuntAdapter(SourceAdapter[dict[str, Any]]):
    """ProductHunt today's top products via public HTML (no API key)."""

    def __init__(self, config: ProductHuntConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._ph_config = (
            config if isinstance(config, ProductHuntConfig) else ProductHuntConfig()
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "producthunt"

    async def setup(self, ctx: ScrapeContext) -> None:
        # Shared client: per-host throttle + header/UA rotation + optional
        # proxy via http_client event hooks (see http_client.py).
        self._client = await get_or_create_client(
            "www.producthunt.com",
            timeout=self._ph_config.timeout_seconds,
            headers=dict(_HEADERS),
            http2=False,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        self._client = None  # shared client — release reference, never close

    async def _fetch_page(self, url: str) -> str:
        if self._client is None:
            return ""
        await self._rate_limit()
        self._record_request_metric(method="html")
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPStatusError as e:
            _log.warning("producthunt.http_error", url=url, status=e.response.status_code)
            return ""
        except Exception as e:
            _log.warning("producthunt.request_error", url=url, error=str(e))
            return ""

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        # Primary page
        html = await self._fetch_page(_PRIMARY_URL)
        raws = _parse_page(html)

        # If primary yielded nothing, try fallback after the required 3s delay
        if not raws:
            await asyncio.sleep(_PAGE_DELAY_S)
            html = await self._fetch_page(_FALLBACK_URL)
            raws = _parse_page(html)

        seen_urls: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in raws:
            u = item.get("url", "")
            if u and u not in seen_urls:
                seen_urls.add(u)
                deduped.append(item)

        valid = validate_batch(deduped, Platform.PRODUCT_HUNT.value)
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
            upvotes = int(raw_json.get("upvotes") or raw.get("score") or 0)
            sentiment_val: float = raw.get("sentiment") or 0.0

            external_id = hashlib.sha1(url.encode("utf-8", errors="replace")).hexdigest()[:24]  # noqa: S324

            h = compute_content_hash(
                platform=Platform.PRODUCT_HUNT,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=None,
                posted_at=None,
            )

            return ProductSignal(
                platform=Platform.PRODUCT_HUNT,
                tier=SourceTier.TIER_5_ALTERNATIVE,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=None,
                modality=ContentModality.TEXT,
                tags=frozenset({"producthunt", "product-launch"}),
                intent=IntentType.PASSIVE,
                engagement=EngagementMetrics(likes=upvotes if upvotes else None),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.60,
                    source_confidence=0.75,
                ),
                content_hash=h,
                platform_specific={
                    "upvotes": upvotes,
                    "sentiment": sentiment_val,
                },
            )
        except Exception as e:
            _log.warning("producthunt.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "_PAGE_DELAY_S", "ProductHuntAdapter", "ProductHuntConfig"]
