"""Amazon India adapter — bestseller rankings from amazon.in.

Fetches the top-10 categories in parallel using asyncio.gather.
Extracts: ASIN, rank, title, price (INR), rating, review_count.
score = rating * log1p(review_count) if both present, else 0.0.

ToS Risk: AMBER — amazon.in/robots.txt allows /gp/bestsellers/.
Rate-limit: 0.12 req/s across all category requests.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
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
from aegis.scrape.ecommerce_utils import random_ua

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.amazon_in")

SCRAPER_VERSION = "amazon_in-0.1.0"

_BASE_URL = "https://www.amazon.in"

_DEFAULT_CATEGORIES: tuple[str, ...] = (
    "electronics",
    "clothing-accessories",
    "sports-fitness-outdoor",
    "beauty",
    "home-kitchen",
    "books",
    "toys-games",
    "grocery",
    "health-personal-care",
    "car-motorbike",
)

# CSS selectors — defined as constants; update when Amazon changes markup
_ITEM_SEL = "[data-asin]"
_RANK_SEL = ".zg-badge-text"
_TITLE_SEL = ".p13n-sc-truncate, .p13n-sc-truncated-heading"
_PRICE_SEL = ".p13n-sc-price"
_RATING_SEL = ".a-icon-alt"
_REVIEW_SEL = ".a-size-small.a-color-secondary"


def _parse_float(text: str) -> float | None:
    try:
        return float(text.strip().split()[0].replace(",", ""))
    except (ValueError, IndexError):
        return None


def _parse_int(text: str) -> int | None:
    try:
        return int(text.strip().replace(",", "").split()[0])
    except (ValueError, IndexError):
        return None


def _parse_amazon_in_page(html: str, *, category: str) -> list[dict[str, Any]]:
    """Extract bestseller items from an Amazon.in category page. Returns [] on any failure."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("amazon_in.missing_dependency", dep="beautifulsoup4")
        return []

    items: list[dict[str, Any]] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        scraped_at = datetime.now(UTC).isoformat()

        for card in soup.select(_ITEM_SEL):
            try:
                asin = card.get("data-asin", "").strip()
                if not asin or len(asin) != 10:
                    continue

                rank_el = card.select_one(_RANK_SEL)
                rank: int | None = None
                if rank_el:
                    rank_text = rank_el.get_text(strip=True).lstrip("#").replace(",", "")
                    rank = _parse_int(rank_text)

                title_el = card.select_one(_TITLE_SEL)
                title = title_el.get_text(strip=True) if title_el else ""

                price_el = card.select_one(_PRICE_SEL)
                price_inr: float | None = None
                if price_el:
                    raw_price = price_el.get_text(strip=True).replace("₹", "").replace(",", "")
                    price_inr = _parse_float(raw_price)

                rating: float | None = None
                rating_el = card.select_one(_RATING_SEL)
                if rating_el:
                    rating_text = rating_el.get_text(strip=True)  # "4.3 out of 5 stars"
                    rating = _parse_float(rating_text)

                review_count: int | None = None
                review_el = card.select_one(_REVIEW_SEL)
                if review_el:
                    review_count = _parse_int(review_el.get_text(strip=True))

                score = (
                    (rating * log1p(review_count))
                    if (rating is not None and review_count is not None)
                    else 0.0
                )

                items.append({
                    "asin": asin,
                    "rank": rank,
                    "title": title,
                    "url": f"{_BASE_URL}/dp/{asin}",
                    "category": category,
                    "scraped_at": scraped_at,
                    "raw_json": {
                        "currency": "INR",
                        "asin": asin,
                        "category": category,
                        "rank": rank,
                        "price_inr": price_inr,
                        "rating": rating,
                        "review_count": review_count,
                        "score": score,
                    },
                })
            except Exception as e:
                _log.warning("amazon_in.card.failed", error=str(e))
                continue

    except Exception as e:
        _log.warning("amazon_in.parse_page.failed", category=category, error=str(e))

    return items


@dataclass(frozen=True, slots=True)
class AmazonINConfig(AdapterConfig):
    """Amazon India adapter configuration."""

    name: str = "amazon_in"
    per_source_rps: float = 0.12  # ~1 req per 8s — conservative for Indian marketplace
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    categories: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_CATEGORIES)


class AmazonINAdapter(SourceAdapter[dict[str, Any]]):
    """Amazon India bestsellers — parallel category fetch via asyncio.gather.

    Fetches all configured categories concurrently, extracts ranking signals
    with price, rating, and review_count from HTML. Price data lands in
    platform_specific (tier=T3 because HTML prices are unreliable for audit).
    """

    def __init__(self, config: AmazonINConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, AmazonINConfig) else AmazonINConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "amazon_in"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._cfg.timeout_seconds),
            headers={
                "User-Agent": random_ua(),
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9,hi;q=0.7",
                "Accept-Encoding": "gzip",
            },
            follow_redirects=True,
            http2=False,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch_category(self, category: str) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="bestsellers_html")
        url = f"{_BASE_URL}/gp/bestsellers/{category}"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return _parse_amazon_in_page(resp.text, category=category)
        except httpx.HTTPStatusError as e:
            _log.warning("amazon_in.fetch.http_error", category=category, status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("amazon_in.fetch.failed", category=category, error=str(e))
            return []

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        tasks = [self._fetch_category(cat) for cat in self._cfg.categories]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        total = 0
        for result in results:
            if isinstance(result, BaseException):
                _log.warning("amazon_in.gather.error", error=str(result))
                continue
            for item in result:
                if total >= limit or self.is_cancelled:
                    return
                yield item
                total += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            asin = str(raw.get("asin") or "").strip()
            if not asin:
                return None

            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or f"{_BASE_URL}/dp/{asin}")
            raw_json = raw.get("raw_json") or {}
            category = str(raw.get("category") or "")

            rank = raw_json.get("rank")
            likes: int | None = max(1, 101 - rank) if rank is not None and rank > 0 else None

            external_id = hashlib.sha1(  # noqa: S324
                f"amazon_in:{asin}".encode()
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=Platform.AMAZON_IN,
                external_id=external_id,
                url=url,
                title=title or None,
                raw_text=None,
                posted_at=None,
            )

            return ProductSignal(
                platform=Platform.AMAZON_IN,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({category} if category else set()),
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(likes=likes),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.6 if title else 0.3,
                    source_confidence=0.75,
                ),
                content_hash=h,
                platform_specific={
                    "currency": raw_json.get("currency", "INR"),
                    "asin": asin,
                    "category": category,
                    "rank": rank,
                    "price_inr": raw_json.get("price_inr"),
                    "rating": raw_json.get("rating"),
                    "review_count": raw_json.get("review_count"),
                },
            )
        except Exception as e:
            _log.warning("amazon_in.parse.failed", error=str(e))
            return None


__all__ = [
    "SCRAPER_VERSION",
    "AmazonINAdapter",
    "AmazonINConfig",
    "_parse_amazon_in_page",
]
