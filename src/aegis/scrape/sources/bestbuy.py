"""Best Buy Products API adapter — free, official, key-gated, fail-open.

Uses Best Buy's free **Products API** which returns real US-catalog listings:
name, sale price (USD), customer review average + count (genuine demand), URL
and image. Simple API-key auth — no OAuth, no scraping, never WAF-blocked.

Free setup (founder, zero prior knowledge — see docs/FREE_COMMERCE_SETUP.md):
  1. Register a free developer account at https://developer.bestbuy.com/.
  2. Click "Get API Key" → confirm via email.
  3. Export it:
        export AEGIS_BESTBUY_API_KEY=...
  No key? Logs `bestbuy.no_credentials` and fail-opens to [].

PLATFORM: bestbuy | TIER: T2_commerce
ToS Risk: GREEN — official public API within documented terms.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from math import log1p
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

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
    Price,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter
from aegis.scrape.http_client import get_or_create_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.bestbuy")

SCRAPER_VERSION = "bestbuy-0.1.0"

_BASE = "https://api.bestbuy.com/v1/products"
_SHOW = "sku,name,salePrice,regularPrice,customerReviewAverage,customerReviewCount,url,image,manufacturer"

_PLATFORM = Platform.BESTBUY


def _api_key() -> str | None:
    key = os.environ.get("AEGIS_BESTBUY_API_KEY", "").strip()
    return key or None


@dataclass(frozen=True, slots=True)
class BestBuyConfig(AdapterConfig):
    """Best Buy Products API adapter configuration."""

    name: str = "bestbuy"
    per_source_rps: float = 4.0  # free tier: 5 req/s, 50k/day
    timeout_seconds: float = 20.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class BestBuyAdapter(SourceAdapter[dict[str, Any]]):
    """Best Buy Products search adapter — US catalog price + real review counts."""

    def __init__(self, config: BestBuyConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, BestBuyConfig) else BestBuyConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "bestbuy"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "api.bestbuy.com",
            http2=True,
            timeout=self._cfg.timeout_seconds,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        self._client = None  # shared pooled client — release reference, never close

    async def _search(self, query: str, limit: int) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        key = _api_key()
        if key is None:
            _log.warning("bestbuy.no_credentials", hint="set AEGIS_BESTBUY_API_KEY")
            return []

        # Best Buy search syntax: products(search=term1&search=term2)
        terms = "&".join(f"search={quote(t)}" for t in query.split()[:6])
        url = f"{_BASE}(({terms}))"
        await self._rate_limit()
        self._record_request_metric(method="products_api")
        try:
            resp = await self._client.get(
                url,
                params={
                    "apiKey": key,
                    "format": "json",
                    "show": _SHOW,
                    "pageSize": str(min(limit, 100)),
                    "sort": "customerReviewCount.desc",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPStatusError as e:
            _log.warning("bestbuy.search.http_error", status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("bestbuy.search.failed", error=str(e))
            return []

        total = int(payload.get("total") or 0)
        scraped_at = datetime.now(UTC).isoformat()
        out: list[dict[str, Any]] = []
        for p in payload.get("products") or []:
            price_usd = p.get("salePrice") if p.get("salePrice") is not None else p.get("regularPrice")
            out.append({
                "sku": str(p.get("sku") or ""),
                "title": (p.get("name") or "").strip(),
                "url": p.get("url") or "",
                "scraped_at": scraped_at,
                "raw_json": {
                    "currency": "USD",
                    "price_usd": float(price_usd) if price_usd is not None else None,
                    "rating": p.get("customerReviewAverage"),
                    "review_count": p.get("customerReviewCount"),
                    "image_url": p.get("image"),
                    "manufacturer": p.get("manufacturer"),
                    "listing_total": total,
                },
            })
        _log.info("bestbuy.search", query=query, results=len(out), total=total)
        return out

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        query: str | None = None,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            _log.debug("bestbuy.no_query")
            return
        for item in (await self._search(q, limit))[:limit]:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = (raw.get("title") or "").strip()
            url = raw.get("url") or ""
            sku = str(raw.get("sku") or "")
            if not title or not url:
                return None
            raw_json = raw.get("raw_json") or {}
            review_count = raw_json.get("review_count")
            rating = raw_json.get("rating")

            external_id = hashlib.sha1(  # noqa: S324
                f"bestbuy:{sku or url}".encode()
            ).hexdigest()[:24]

            # review_count is a real demand proxy → engagement.likes.
            likes = int(review_count) if isinstance(review_count, int | float) else None

            # T2 commerce requires a real price; a price-less Best Buy row is an
            # incomplete listing — skip it rather than misclassify the tier.
            price_usd = raw_json.get("price_usd")
            if price_usd is None:
                return None
            try:
                price_obj = Price(amount=Decimal(str(price_usd)), currency="USD")
            except Exception:
                return None

            h = compute_content_hash(
                platform=_PLATFORM,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=None,
                posted_at=None,
            )
            return ProductSignal(
                platform=_PLATFORM,
                tier=SourceTier.TIER_2_COMMERCE,
                price=price_obj,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({"bestbuy"}),
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(likes=likes),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.OFFICIAL_API,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.85 if raw_json.get("price_usd") is not None else 0.6,
                    source_confidence=0.95,
                ),
                content_hash=h,
                platform_specific={
                    "sku": sku,
                    "currency": "USD",
                    "price_usd": raw_json.get("price_usd"),
                    "rating": rating,
                    "review_count": review_count,
                    "image_url": raw_json.get("image_url"),
                    "manufacturer": raw_json.get("manufacturer"),
                    "listing_total": raw_json.get("listing_total"),
                    "demand_proxy": int(log1p(review_count)) if isinstance(review_count, int | float) and review_count else None,
                },
            )
        except Exception as e:
            _log.warning("bestbuy.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "BestBuyAdapter", "BestBuyConfig"]
