"""Nykaa adapter — popular beauty products from Nykaa.com.

Scrapes: https://www.nykaa.com/beauty/c/3?sort=popularity
Extracts: product name, brand, price (INR), rating, review_count.
BeautifulSoup + lxml parser.

ToS Risk: AMBER — public catalogue page; no login required.
Rate-limit: 0.15 req/s.
"""
from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from math import log1p
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
    ConfidenceMetadata,
    EngagementMetrics,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter
from aegis.scrape.ecommerce_utils import extract_card_image, join_brand_title
from aegis.scrape.http_client import get_or_create_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.nykaa")

SCRAPER_VERSION = "nykaa-0.1.0"

_URL = "https://www.nykaa.com/beauty/c/3?sort=popularity"
# Keyword search — query-relevant products (the real intelligence path).
_SEARCH_URL = "https://www.nykaa.com/search/result/?q={q}"

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Referer": "https://www.nykaa.com/",
}

# CSS selectors as module-level constants
_CARD_SEL = ".product-list-card, [class*='productCard'], [data-testid='product-card']"
_TITLE_SEL = "[class*='productName'], .product-list__product-name"
_BRAND_SEL = "[class*='brandName'], .product-list__brand-name"
_PRICE_SEL = "[class*='productPrice'], .product-list__price-label"
_RATING_SEL = "[class*='averageRating'], .product-list__rating"
_REVIEW_SEL = "[class*='reviewCount'], .product-list__review-count"


def _parse_nykaa_html(html: str) -> list[dict[str, Any]]:
    """Extract product cards from Nykaa popularity-sorted page."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("nykaa.missing_dependency", dep="beautifulsoup4")
        return []

    items: list[dict[str, Any]] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        scraped_at = datetime.now(UTC).isoformat()

        for card in soup.select(_CARD_SEL):
            try:
                brand_el = card.select_one(_BRAND_SEL)
                title_el = card.select_one(_TITLE_SEL)
                brand = brand_el.get_text(strip=True) if brand_el else ""
                name = title_el.get_text(strip=True) if title_el else ""
                title = join_brand_title(brand, name)
                if not title:
                    continue

                price_el = card.select_one(_PRICE_SEL)
                price_inr: float | None = None
                with contextlib.suppress(ValueError, IndexError):
                    raw_price = (price_el.get_text(strip=True) if price_el else "").replace("₹", "").replace(",", "").strip()
                    price_inr = float(raw_price.split()[0]) if raw_price else None

                rating: float | None = None
                rating_el = card.select_one(_RATING_SEL)
                with contextlib.suppress(ValueError, TypeError):
                    rating = float((rating_el.get_text(strip=True) if rating_el else "").strip())

                review_count: int | None = None
                review_el = card.select_one(_REVIEW_SEL)
                try:
                    review_text = (review_el.get_text(strip=True) if review_el else "").replace(",", "").split()[0]
                    review_count = int(review_text)
                except (ValueError, IndexError):
                    pass

                link_el = card.find("a")
                href = link_el.get("href", "") if link_el else ""
                url = href if href.startswith("http") else f"https://www.nykaa.com{href}"

                score = (
                    rating * log1p(review_count)
                    if (rating is not None and review_count is not None)
                    else 0.0
                )

                items.append({
                    "title": title,
                    "url": url or _URL,
                    "scraped_at": scraped_at,
                    "raw_json": {
                        "currency": "INR",
                        "image_url": extract_card_image(card),
                        "brand": brand,
                        "price_inr": price_inr,
                        "rating": rating,
                        "review_count": review_count,
                        "score": score,
                    },
                })
            except Exception as e:
                _log.warning("nykaa.card.failed", error=str(e))

    except Exception as e:
        _log.warning("nykaa.parse_html.failed", error=str(e))

    return items


@dataclass(frozen=True, slots=True)
class NykaaConfig(AdapterConfig):
    """Nykaa adapter configuration."""

    name: str = "nykaa"
    per_source_rps: float = 0.15
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class NykaaAdapter(SourceAdapter[dict[str, Any]]):
    """Nykaa popular beauty products HTML adapter.

    Scrapes the popularity-sorted beauty catalogue. All selector failures
    are caught per-card; the adapter always produces a partial result
    rather than failing completely.
    """

    def __init__(self, config: NykaaConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, NykaaConfig) else NykaaConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "nykaa"

    async def setup(self, ctx: ScrapeContext) -> None:
        # Shared client: per-host throttle, header/UA rotation, and optional
        # proxy are applied via http_client event hooks (see http_client.py).
        self._client = await get_or_create_client(
            "www.nykaa.com",
            timeout=self._cfg.timeout_seconds,
            headers=dict(_HEADERS),
            http2=False,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        self._client = None  # shared client — release reference, never close

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        query: str | None = None,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            return

        from urllib.parse import quote_plus

        q = (query or "").strip()
        # Query-driven search is the intelligence path; the popularity-sorted
        # category page is the no-query fallback.
        url = _SEARCH_URL.format(q=quote_plus(q)) if q else _URL

        await self._rate_limit()
        self._record_request_metric(method="search" if q else "html_page")

        items: list[dict[str, Any]] = []
        # Primary: curl_cffi browser-TLS impersonation beats the WAF 403 that
        # plain httpx triggers; then DOM cards or site-agnostic structured data.
        from aegis.scrape.ecommerce_utils import extract_products_from_structured
        from aegis.scrape.http_client import impersonated_fetch

        code, imp_html = await impersonated_fetch(url)
        if code == 200 and imp_html:
            items = _parse_nykaa_html(imp_html) or extract_products_from_structured(imp_html)

        if not items:
            try:
                resp = await self._client.get(url)
                resp.raise_for_status()
                items = _parse_nykaa_html(resp.text)
            except httpx.HTTPStatusError as e:
                _log.warning("nykaa.http_error", status=e.response.status_code)
            except Exception as e:
                _log.warning("nykaa.fetch.failed", error=str(e))

        # Nykaa search results are JS-rendered; plain httpx often returns a shell.
        if not items:
            from aegis.scrape.playwright_fetcher import fetch_page_html
            html = await fetch_page_html(url)
            if html:
                items = _parse_nykaa_html(html)

        for item in items[:limit]:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or _URL)
            if not title:
                return None

            raw_json = raw.get("raw_json") or {}

            external_id = hashlib.sha1(  # noqa: S324
                url.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=Platform.NYKAA,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=None,
                posted_at=None,
            )

            rating = raw_json.get("rating")
            review_count = raw_json.get("review_count")
            score = (
                float(rating) * log1p(int(review_count))
                if (rating is not None and review_count)
                else 0.0
            )

            return ProductSignal(
                platform=Platform.NYKAA,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({"nykaa", "beauty"}),
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(
                    likes=int(score) if score else None,
                ),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.65 if raw_json.get("price_inr") else 0.35,
                    source_confidence=0.70,
                ),
                content_hash=h,
                platform_specific={
                    "currency": raw_json.get("currency", "INR"),
                    "brand": raw_json.get("brand"),
                    "price_inr": raw_json.get("price_inr"),
                    "rating": rating,
                    "review_count": review_count,
                },
            )
        except Exception as e:
            _log.warning("nykaa.parse.failed", error=str(e))
            return None


__all__ = [
    "SCRAPER_VERSION",
    "NykaaAdapter",
    "NykaaConfig",
    "_parse_nykaa_html",
]
