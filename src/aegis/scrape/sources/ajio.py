"""AJIO adapter — trending products via public JSON search API.

Endpoint: https://www.ajio.com/api/search?text=trending&start=0&sz=50
No authentication required. Returns JSON with product list.
Extracts: brand, name, price, discounted price, discount, rating.

ToS Risk: AMBER — public API endpoint, no login required.
Rate-limit: 0.15 req/s.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
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
    ConfidenceMetadata,
    EngagementMetrics,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter
from aegis.scrape.ecommerce_utils import join_brand_title
from aegis.scrape.http_client import get_or_create_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.ajio")

SCRAPER_VERSION = "ajio-0.1.0"

_SEARCH_URL = "https://www.ajio.com/api/search"
_BASE_PARAMS = {"text": "trending", "start": "0", "sz": "50"}

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Referer": "https://www.ajio.com/",
    "x-request-id": "ajio-web-search",
}


def _extract_ajio_products(data: Any) -> list[dict[str, Any]]:
    """Extract product entries from AJIO JSON search response."""
    items: list[dict[str, Any]] = []
    try:
        if not isinstance(data, dict):
            return []

        results = (
            data.get("responseBody", {}).get("hits")
            or data.get("hits")
            or data.get("products")
            or []
        )
        if not isinstance(results, list):
            return []

        scraped_at = datetime.now(UTC).isoformat()
        for p in results:
            if not isinstance(p, dict):
                continue

            brand = str(p.get("brandname") or p.get("brand") or "").strip()
            name = str(p.get("name") or p.get("productName") or "").strip()
            title = join_brand_title(brand, name)
            if not title:
                continue

            product_id = str(p.get("code") or p.get("id") or "")
            url = f"https://www.ajio.com{p.get('url', '')}" if p.get("url") else f"https://www.ajio.com/search?q={product_id}"

            price = p.get("price") or p.get("discountedprice") or 0
            orig_price = p.get("wasPriceData") or p.get("mrp") or price
            try:
                discount_pct = float(p.get("discount") or 0)
            except (ValueError, TypeError):
                discount_pct = 0.0

            rating = p.get("rating") or p.get("averageRating")

            items.append({
                "title": title,
                "url": url,
                "scraped_at": scraped_at,
                "raw_json": {
                    "currency": "INR",
                    "brand": brand,
                    "price_inr": float(price) if price else None,
                    "orig_price_inr": float(orig_price) if orig_price else None,
                    "discount_pct": discount_pct,
                    "rating": float(rating) if rating else None,
                    "product_id": product_id,
                },
            })
    except Exception as e:
        _log.warning("ajio.extract_products.failed", error=str(e))
    return items


@dataclass(frozen=True, slots=True)
class AjioConfig(AdapterConfig):
    """AJIO adapter configuration."""

    name: str = "ajio"
    per_source_rps: float = 0.15
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    search_query: str = "trending"
    page_size: int = 50


class AjioAdapter(SourceAdapter[dict[str, Any]]):
    """AJIO trending products via public JSON search API.

    Single endpoint, no bypass needed. Falls back to [] gracefully on
    JSON decode errors or HTTP failures.
    """

    def __init__(self, config: AjioConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, AjioConfig) else AjioConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "ajio"

    async def setup(self, ctx: ScrapeContext) -> None:
        # Shared client: per-host throttle + header/UA rotation + optional
        # proxy via http_client event hooks (see http_client.py).
        self._client = await get_or_create_client(
            "www.ajio.com",
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
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            return

        await self._rate_limit()
        self._record_request_metric(method="json_search")

        params = {
            "text": self._cfg.search_query,
            "start": "0",
            "sz": str(min(limit, self._cfg.page_size)),
        }

        try:
            resp = await self._client.get(_SEARCH_URL, params=params)
            resp.raise_for_status()
            items = _extract_ajio_products(resp.json())
        except httpx.HTTPStatusError as e:
            _log.warning("ajio.http_error", status=e.response.status_code)
            return
        except Exception as e:
            _log.warning("ajio.fetch.failed", error=str(e))
            return

        for item in items[:limit]:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or "https://www.ajio.com")
            if not title:
                return None

            raw_json = raw.get("raw_json") or {}

            external_id = hashlib.sha1(  # noqa: S324
                url.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=Platform.AJIO,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=None,
                posted_at=None,
            )

            return ProductSignal(
                platform=Platform.AJIO,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({"ajio", "fashion"}),
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.7 if raw_json.get("price_inr") else 0.4,
                    source_confidence=0.75,
                ),
                content_hash=h,
                platform_specific={
                    "currency": raw_json.get("currency", "INR"),
                    "brand": raw_json.get("brand"),
                    "price_inr": raw_json.get("price_inr"),
                    "orig_price_inr": raw_json.get("orig_price_inr"),
                    "discount_pct": raw_json.get("discount_pct", 0.0),
                    "rating": raw_json.get("rating"),
                    "product_id": raw_json.get("product_id"),
                },
            )
        except Exception as e:
            _log.warning("ajio.parse.failed", error=str(e))
            return None


__all__ = [
    "SCRAPER_VERSION",
    "AjioAdapter",
    "AjioConfig",
    "_extract_ajio_products",
]
