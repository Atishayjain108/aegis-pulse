"""IndiaMart adapter — B2B trade intelligence signals.

Two-phase fetch:
  1. HTML parse of https://www.indiamart.com/trade-intelligence/
     → extract trending B2B category names
  2. For each category, query:
     https://www.indiamart.com/search.mp?ss={category_keyword}
     → JSON response with supplier_count, buyer_inquiry_count, price_range

Extracted fields: product name, supplier_count, buyer_inquiry_count, price_range.
  score = log1p(buyer_inquiry_count)
  raw_json["currency"] = "INR"
  raw_json["market_type"] = "B2B"

ToS Risk: AMBER — public trade intelligence and search pages.
Rate-limit: 0.1 req/s — B2B platform; respect throttle aggressively.
"""
from __future__ import annotations

import hashlib
import re
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
from aegis.scrape.http_client import get_or_create_client
from aegis.scrape.playwright_fetcher import fetch_page_html as _playwright_fetch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.indiamart")

SCRAPER_VERSION = "indiamart-0.1.0"

_TRADE_INTEL_URL = "https://www.indiamart.com/trade-intelligence/"
_SEARCH_URL = "https://www.indiamart.com/search.mp"

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Referer": "https://www.indiamart.com/",
}

_JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Referer": _TRADE_INTEL_URL,
}

# HTML selectors for trade intelligence page — prefer semantic/structural over
# fragile class names; last selector targets any card-like container with a heading.
_CATEGORY_SEL = (
    ".category-card, .trending-category, [class*='categoryCard'], .trd-ctg, "
    "article, [class*='trending'], [class*='category'], li.card"
)
_CATEGORY_NAME_SEL = "h2, h3, h4, .category-name, [class*='categoryTitle'], [class*='name']"

_MAX_CATEGORIES = 8

# Evergreen B2B categories used as last-resort fallback when both httpx and
# Playwright fail to parse the trade-intelligence page.
_FALLBACK_CATEGORIES = [
    "Industrial Machinery",
    "Electronics Components",
    "Textile & Fabric",
    "Agriculture Products",
    "Chemical Compounds",
    "Building Materials",
    "Packaging Materials",
    "Auto Parts",
]


def _extract_categories_html(html: str) -> list[str]:
    """Extract trending B2B category names from IndiaMart trade intelligence HTML."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("indiamart.missing_dependency", dep="beautifulsoup4")
        return []

    categories: list[str] = []
    try:
        soup = BeautifulSoup(html, "lxml")

        for card in soup.select(_CATEGORY_SEL):
            try:
                name_el = card.select_one(_CATEGORY_NAME_SEL)
                name = name_el.get_text(strip=True) if name_el else ""
                if name and name not in categories:
                    categories.append(name)
            except Exception:  # known-fragile scraper; skip malformed cards
                continue

        if not categories:
            # Fallback: look for any prominent headings that represent categories
            for heading in soup.select("h3, h4"):
                text = heading.get_text(strip=True)
                if len(text) > 3 and len(text) < 60 and text not in categories:
                    categories.append(text)

    except Exception as e:
        _log.warning("indiamart.extract_categories.failed", error=str(e))

    return categories[:_MAX_CATEGORIES]


def _extract_search_results(data: Any, category: str) -> list[dict[str, Any]]:
    """Extract product entries from IndiaMart search JSON response."""
    items: list[dict[str, Any]] = []
    try:
        if not isinstance(data, list | dict):
            return []

        rows: list[Any] = data if isinstance(data, list) else (
            data.get("data") or data.get("results") or data.get("items") or []
        )
        if not isinstance(rows, list):
            return []

        scraped_at = datetime.now(UTC).isoformat()
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                subject = str(row.get("SUBJECT") or row.get("name") or row.get("productName") or "").strip()
                if not subject:
                    continue

                sup_count = int(row.get("SUP_COUNT") or row.get("supplier_count") or 0)
                buyer_count = int(row.get("GLF") or row.get("buyer_inquiry_count") or row.get("inquiries") or 0)
                price_range = str(row.get("PRICE_RANGE") or row.get("price_range") or row.get("priceRange") or "")
                product_id = str(row.get("id") or row.get("product_id") or subject[:20])
                score = log1p(buyer_count)

                items.append({
                    "title": subject,
                    "url": f"https://www.indiamart.com/search.mp?ss={subject.replace(' ', '+')}",
                    "scraped_at": scraped_at,
                    "raw_json": {
                        "currency": "INR",
                        "market_type": "B2B",
                        "category": category,
                        "supplier_count": sup_count,
                        "buyer_inquiry_count": buyer_count,
                        "price_range": price_range,
                        "product_id": product_id,
                        "score": score,
                    },
                })
            except Exception as e:
                _log.warning("indiamart.row.failed", error=str(e))

    except Exception as e:
        _log.warning("indiamart.extract_search.failed", error=str(e))
    return items


@dataclass(frozen=True, slots=True)
class IndiaMartConfig(AdapterConfig):
    """IndiaMart adapter configuration."""

    name: str = "indiamart"
    per_source_rps: float = 0.1  # aggressive rate limit for B2B platform
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    max_categories: int = 5


class IndiaMartAdapter(SourceAdapter[dict[str, Any]]):
    """IndiaMart B2B trade intelligence adapter.

    Phase 1: Scrapes the trade intelligence page for trending categories.
    Phase 2: Queries the search API for each category to get supplier and
    buyer inquiry signals. score = log1p(buyer_inquiry_count).
    """

    def __init__(self, config: IndiaMartConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, IndiaMartConfig) else IndiaMartConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "indiamart"

    async def setup(self, ctx: ScrapeContext) -> None:
        # Shared client: per-host throttle + header/UA rotation + optional
        # proxy via http_client event hooks (see http_client.py).
        self._client = await get_or_create_client(
            "www.indiamart.com",
            timeout=self._cfg.timeout_seconds,
            headers=dict(_HEADERS),
            http2=False,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        self._client = None  # shared client — release reference, never close

    async def _fetch_categories(self) -> list[str]:
        """Fetch trending B2B categories via httpx → Playwright → hardcoded fallback."""
        if self._client is None:
            return _FALLBACK_CATEGORIES
        await self._rate_limit()
        self._record_request_metric(method="trade_intel_html")

        # --- httpx path ---
        categories: list[str] = []
        try:
            resp = await self._client.get(_TRADE_INTEL_URL)
            resp.raise_for_status()
            categories = _extract_categories_html(resp.text)
        except Exception as e:
            _log.warning("indiamart.categories.httpx_failed", error=str(e))

        # --- Playwright fallback when httpx returns nothing ---
        if not categories:
            _log.info("indiamart.categories.trying_playwright")
            html = await _playwright_fetch(_TRADE_INTEL_URL)
            if html:
                categories = _extract_categories_html(html)

        # --- Hardcoded evergreen fallback ---
        if not categories:
            _log.info("indiamart.categories.using_fallback_list")
            categories = _FALLBACK_CATEGORIES

        return categories

    async def _search_category(self, category: str) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="search_json")
        try:
            resp = await self._client.get(
                _SEARCH_URL,
                params={"ss": category},
                headers=dict(_JSON_HEADERS),
            )
            resp.raise_for_status()
            return _extract_search_results(resp.json(), category)
        except httpx.HTTPStatusError as e:
            _log.warning("indiamart.search.http_error", category=category, status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("indiamart.search.failed", category=category, error=str(e))
            return []

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        categories = await self._fetch_categories()
        if not categories:
            _log.warning("indiamart.no_categories")
            return

        total = 0
        for category in categories[: self._cfg.max_categories]:
            if total >= limit or self.is_cancelled:
                return
            results = await self._search_category(category)
            for item in results:
                if total >= limit or self.is_cancelled:
                    return
                yield item
                total += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or _TRADE_INTEL_URL)
            if not title:
                return None

            raw_json = raw.get("raw_json") or {}

            external_id = hashlib.sha1(  # noqa: S324
                url.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=Platform.INDIAMART,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=None,
                posted_at=None,
            )

            buyer_count = int(raw_json.get("buyer_inquiry_count") or 0)
            score = log1p(buyer_count)
            category = str(raw_json.get("category") or "")
            cat_tag = re.sub(r"[^a-z0-9_\-\.]", "-", category.lower()).strip("-") if category else ""

            return ProductSignal(
                platform=Platform.INDIAMART,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({"indiamart", "b2b", *([cat_tag] if cat_tag else [])}),
                intent=IntentType.SEARCH,
                engagement=EngagementMetrics(likes=int(score) if score else None),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.6 if raw_json.get("supplier_count") else 0.3,
                    source_confidence=0.70,
                ),
                content_hash=h,
                platform_specific={
                    "currency": raw_json.get("currency", "INR"),
                    "market_type": raw_json.get("market_type", "B2B"),
                    "category": category,
                    "supplier_count": raw_json.get("supplier_count"),
                    "buyer_inquiry_count": buyer_count,
                    "price_range": raw_json.get("price_range"),
                },
            )
        except Exception as e:
            _log.warning("indiamart.parse.failed", error=str(e))
            return None


__all__ = [
    "SCRAPER_VERSION",
    "IndiaMartAdapter",
    "IndiaMartConfig",
    "_extract_categories_html",
    "_extract_search_results",
]
