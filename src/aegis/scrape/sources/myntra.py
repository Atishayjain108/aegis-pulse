"""Myntra adapter — trending fashion products.

Strategy:
  1. Primary: JSON gateway  https://www.myntra.com/gateway/v2/search/trending
  2. Fallback (403): HTML parse via FlareSolverr for https://www.myntra.com/trending-now

JSON fields: product_name, brand, price, discount, rating, review_count, categories.
Category tags → platform_specific["tags"].

ToS Risk: RED — Myntra defends against bots; FlareSolverr may be required for HTML.
Rate-limit: 0.1 req/s.
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
from aegis.scrape.ecommerce_utils import flaresolverr_get, random_ua

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.myntra")

SCRAPER_VERSION = "myntra-0.1.0"

_JSON_URL = "https://www.myntra.com/gateway/v2/search/trending"
_HTML_URL = "https://www.myntra.com/trending-now"

_HEADERS = {
    "Accept": "application/json, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Referer": "https://www.myntra.com/",
    "x-meta-app": '{"appFamily":"Web"}',
}

# HTML selectors (FlareSolverr fallback path)
_CARD_SEL = ".product-base, [data-testid='product-card']"
_TITLE_SEL = ".product-product, .product-brand + .product-product"
_BRAND_SEL = ".product-brand"
_PRICE_SEL = ".product-discountedPrice, .product-price strong"
_ORIG_SEL = ".product-strike"
_RATING_SEL = ".product-ratingsCount"


def _extract_json_products(data: Any) -> list[dict[str, Any]]:
    """Extract products from Myntra JSON gateway response."""
    items: list[dict[str, Any]] = []
    try:
        if not isinstance(data, dict):
            return []

        products = (
            data.get("products")
            or data.get("data", {}).get("products")
            or data.get("searchData", {}).get("results", {}).get("products")
            or []
        )
        if not isinstance(products, list):
            return []

        scraped_at = datetime.now(UTC).isoformat()
        for p in products:
            if not isinstance(p, dict):
                continue

            name = str(p.get("productName") or p.get("product") or "").strip()
            brand = str(p.get("brand") or p.get("brandName") or "").strip()
            title = f"{brand} {name}".strip() if brand else name
            if not title:
                continue

            product_id = str(p.get("productId") or p.get("id") or "")
            url = f"https://www.myntra.com/{product_id}" if product_id else _HTML_URL

            price = p.get("price") or p.get("discountedPrice") or 0
            mrp = p.get("mrp") or p.get("originalPrice") or 0
            discount = p.get("discount") or p.get("discountDisplayLabel") or 0
            try:
                discount_pct = float(str(discount).replace("%", "").replace("OFF", "").strip().split()[0])
            except (ValueError, IndexError):
                discount_pct = 0.0

            rating = p.get("rating") or p.get("averageRating")
            review_count = p.get("ratingCount") or p.get("reviewCount") or 0
            categories = p.get("primaryCategories") or []
            tags = [str(c.get("name") or "") for c in categories if isinstance(c, dict) and c.get("name")]

            score = (
                float(rating) * log1p(int(review_count))
                if (rating is not None and review_count)
                else 0.0
            )

            items.append({
                "title": title,
                "url": url,
                "scraped_at": scraped_at,
                "raw_json": {
                    "currency": "INR",
                    "brand": brand,
                    "price_inr": float(price) if price else None,
                    "orig_price_inr": float(mrp) if mrp else None,
                    "discount_pct": discount_pct,
                    "rating": float(rating) if rating else None,
                    "review_count": int(review_count) if review_count else 0,
                    "tags": tags,
                    "product_id": product_id,
                    "score": score,
                    "source": "json_api",
                },
            })
    except Exception as e:
        _log.warning("myntra.extract_json.failed", error=str(e))
    return items


def _parse_myntra_html(html: str) -> list[dict[str, Any]]:
    """Extract product cards from Myntra HTML (FlareSolverr path)."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("myntra.missing_dependency", dep="beautifulsoup4")
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
                title = f"{brand} {name}".strip() if brand else name
                if not title:
                    continue

                price_el = card.select_one(_PRICE_SEL)
                price_inr: float | None = None
                with contextlib.suppress(ValueError, TypeError):
                    price_inr = float(
                        (price_el.get_text(strip=True) if price_el else "")
                        .replace("Rs.", "")
                        .replace(",", "")
                        .strip()
                    )

                link_el = card.find("a")
                href = link_el.get("href", "") if link_el else ""
                url = href if href.startswith("http") else f"https://www.myntra.com{href}"

                items.append({
                    "title": title,
                    "url": url or _HTML_URL,
                    "scraped_at": scraped_at,
                    "raw_json": {
                        "currency": "INR",
                        "brand": brand,
                        "price_inr": price_inr,
                        "discount_pct": 0.0,
                        "rating": None,
                        "review_count": 0,
                        "tags": [],
                        "score": 0.0,
                        "source": "html_fallback",
                    },
                })
            except Exception as e:
                _log.warning("myntra.card.failed", error=str(e))
    except Exception as e:
        _log.warning("myntra.parse_html.failed", error=str(e))
    return items


@dataclass(frozen=True, slots=True)
class MyntraConfig(AdapterConfig):
    """Myntra adapter configuration."""

    name: str = "myntra"
    per_source_rps: float = 0.1
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = True
    flaresolverr_url: str = "http://localhost:8191/v1"


class MyntraAdapter(SourceAdapter[dict[str, Any]]):
    """Myntra trending fashion adapter.

    Tries the JSON gateway first. On 403, uses FlareSolverr to fetch
    the HTML trending page. Price, discount, and brand data all go to
    platform_specific (tier=T3 for consistent scraper semantics).
    """

    def __init__(
        self,
        config: MyntraConfig | AdapterConfig,
        *,
        governor: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, MyntraConfig) else MyntraConfig()
        self._governor = governor
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "myntra"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._cfg.timeout_seconds),
            headers={**_HEADERS, "User-Agent": random_ua()},
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

        # --- JSON path (primary) ---
        await self._rate_limit()
        self._record_request_metric(method="json_api")
        items: list[dict[str, Any]] = []
        try:
            resp = await self._client.get(_JSON_URL)
            if resp.status_code == 403:
                _log.warning("myntra.json_api.forbidden")
            else:
                resp.raise_for_status()
                items = _extract_json_products(resp.json())
        except Exception as e:
            _log.warning("myntra.json_api.failed", error=str(e))

        # --- FlareSolverr HTML fallback ---
        if not items:
            _log.info("myntra.falling_back_to_flaresolverr")
            await self._rate_limit()
            self._record_request_metric(method="html_flaresolverr")
            html = await flaresolverr_get(
                _HTML_URL, self._cfg.flaresolverr_url, self._client, self._governor
            )
            if html:
                items = _parse_myntra_html(html)
            else:
                _log.warning("myntra.flaresolverr.no_html")

        for item in items[:limit]:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or _HTML_URL)
            if not title:
                return None

            raw_json = raw.get("raw_json") or {}

            external_id = hashlib.sha1(  # noqa: S324
                url.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=Platform.MYNTRA,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=None,
                posted_at=None,
            )

            review_count = int(raw_json.get("review_count") or 0)
            tags_list: list[str] = raw_json.get("tags") or []

            return ProductSignal(
                platform=Platform.MYNTRA,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({"myntra", "fashion", *[t.lower() for t in tags_list if t]}),
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(likes=review_count if review_count else None),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.RED,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.7 if raw_json.get("price_inr") else 0.4,
                    source_confidence=0.65,
                ),
                content_hash=h,
                platform_specific={
                    "currency": raw_json.get("currency", "INR"),
                    "brand": raw_json.get("brand"),
                    "price_inr": raw_json.get("price_inr"),
                    "orig_price_inr": raw_json.get("orig_price_inr"),
                    "discount_pct": raw_json.get("discount_pct", 0.0),
                    "rating": raw_json.get("rating"),
                    "review_count": review_count,
                    "tags": tags_list,
                    "source": raw_json.get("source", "json_api"),
                },
            )
        except Exception as e:
            _log.warning("myntra.parse.failed", error=str(e))
            return None


__all__ = [
    "MyntraAdapter",
    "MyntraConfig",
    "SCRAPER_VERSION",
    "_extract_json_products",
    "_parse_myntra_html",
]
