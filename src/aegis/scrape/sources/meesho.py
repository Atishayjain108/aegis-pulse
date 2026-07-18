"""Meesho adapter — trending products from India's social commerce platform.

Strategy:
  1. Primary: JSON API  https://api.meesho.com/v1/catalogue/collections/trending
  2. Fallback (401/403): HTML parse of https://meesho.com/trending

JSON fields: product_name, supplier_name, price, order_count, category.
  score = order_count / 100

ToS Risk: AMBER — public trending API; no authentication required at present.
Rate-limit: 0.15 req/s.
"""
from __future__ import annotations

import contextlib
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
from aegis.scrape.ecommerce_utils import extract_card_image
from aegis.scrape.http_client import get_or_create_client
from aegis.scrape.playwright_fetcher import fetch_page_html as _playwright_fetch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.meesho")

SCRAPER_VERSION = "meesho-0.1.0"

_JSON_API_URL = "https://api.meesho.com/v1/catalogue/collections/trending"
_HTML_URL = "https://meesho.com/trending"
# Keyword search — returns products RELEVANT to the query (the intelligence path).
_SEARCH_URL = "https://www.meesho.com/search?q={q}"

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Origin": "https://meesho.com",
    "Referer": "https://meesho.com/",
}

# HTML selectors (fallback path)
# Avoid Styled-Components hashes (sc-*) — they rotate on every deploy.
# Use semantic/data-testid selectors + broad structural fallbacks.
_CARD_SEL = (
    "[data-testid='product-card'], [data-testid='catalogueCard'], "
    ".product-card, article[class*='Product'], article[class*='product'], "
    "li[class*='product'], div[class*='ProductCard'], div[class*='productCard']"
)
_TITLE_SEL = (
    "[data-testid='product-name'], [data-testid='productName'], "
    ".product-title, [class*='productName'], [class*='ProductName'], h3, h4"
)
_PRICE_SEL = (
    "[data-testid='product-price'], [data-testid='discountedPrice'], "
    ".price, [class*='Price'], [class*='price'] span, "
    "[class*='discountedPrice'], [class*='DiscountedPrice']"
)


def _extract_json_products(data: Any) -> list[dict[str, Any]]:
    """Extract product list from Meesho JSON API response."""
    items: list[dict[str, Any]] = []
    try:
        if not isinstance(data, dict):
            return []
        catalogues = (
            data.get("data", {}).get("catalogues")
            or data.get("catalogues")
            or data.get("products")
            or []
        )
        if not isinstance(catalogues, list):
            return []

        scraped_at = datetime.now(UTC).isoformat()
        for product in catalogues:
            if not isinstance(product, dict):
                continue
            name = str(product.get("product_name") or product.get("name") or "").strip()
            if not name:
                continue

            supplier = str(product.get("supplier_name") or product.get("supplier") or "")
            price = product.get("price") or product.get("mrp") or 0
            order_count = int(product.get("order_count") or product.get("orders") or 0)
            category = str(product.get("category") or product.get("category_name") or "")
            product_id = str(product.get("id") or product.get("product_id") or name)
            score = order_count / 100

            items.append({
                "title": name,
                "url": f"https://meesho.com/product/{product_id}",
                "scraped_at": scraped_at,
                "raw_json": {
                    "currency": "INR",
                    "supplier": supplier,
                    "price_inr": float(price) if price else None,
                    "order_count": order_count,
                    "category": category,
                    "product_id": product_id,
                    "score": score,
                    "source": "json_api",
                },
            })
    except Exception as e:
        _log.warning("meesho.extract_json.failed", error=str(e))
    return items


def _parse_meesho_html(html: str) -> list[dict[str, Any]]:
    """Extract product cards from Meesho trending HTML. Returns [] on any failure."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("meesho.missing_dependency", dep="beautifulsoup4")
        return []

    items: list[dict[str, Any]] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        scraped_at = datetime.now(UTC).isoformat()

        for card in soup.select(_CARD_SEL):
            try:
                title_el = card.select_one(_TITLE_SEL)
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                price_el = card.select_one(_PRICE_SEL)
                price_text = price_el.get_text(strip=True) if price_el else ""
                price_inr: float | None = None
                with contextlib.suppress(ValueError, IndexError):
                    price_inr = float(
                        price_text.replace("₹", "").replace(",", "").strip().split()[0]
                    )

                link_el = card.find("a")
                href = link_el.get("href", "") if link_el else ""
                url = href if href.startswith("http") else f"https://meesho.com{href}"

                items.append({
                    "title": title,
                    "url": url or _HTML_URL,
                    "scraped_at": scraped_at,
                    "raw_json": {
                        "currency": "INR",
                        "image_url": extract_card_image(card),
                        "price_inr": price_inr,
                        "order_count": 0,
                        "score": 0.0,
                        "source": "html_fallback",
                    },
                })
            except Exception as e:
                _log.warning("meesho.card.failed", error=str(e))
    except Exception as e:
        _log.warning("meesho.parse_html.failed", error=str(e))
    return items


@dataclass(frozen=True, slots=True)
class MeeshoConfig(AdapterConfig):
    """Meesho adapter configuration."""

    name: str = "meesho"
    per_source_rps: float = 0.15
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class MeeshoAdapter(SourceAdapter[dict[str, Any]]):
    """Meesho trending products adapter.

    JSON API is tried first (primary path). If the API returns 401/403,
    falls back to HTML scraping of the trending page.
    """

    def __init__(self, config: MeeshoConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, MeeshoConfig) else MeeshoConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "meesho"

    async def setup(self, ctx: ScrapeContext) -> None:
        # Shared client: per-host throttle + header/UA rotation + optional
        # proxy via http_client event hooks (see http_client.py). The JSON API
        # lives on api.meesho.com but httpx clients are not host-locked, so a
        # single shared client serves both the API and the HTML host.
        self._client = await get_or_create_client(
            "www.meesho.com",
            timeout=self._cfg.timeout_seconds,
            headers=dict(_HEADERS),
            http2=False,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        self._client = None  # shared client — release reference, never close

    async def _fetch_json(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="json_api")
        try:
            resp = await self._client.get(_JSON_API_URL)
            if resp.status_code in (401, 403):
                _log.warning("meesho.json_api.unauthorized", status=resp.status_code)
                return []
            resp.raise_for_status()
            return _extract_json_products(resp.json())
        except Exception as e:
            _log.warning("meesho.json_api.failed", error=str(e))
            return []

    async def _fetch_html(self) -> list[dict[str, Any]]:
        """Fetch trending HTML: plain httpx → Playwright stealth fallback."""
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="html_fallback")

        # --- plain httpx ---
        items: list[dict[str, Any]] = []
        try:
            resp = await self._client.get(_HTML_URL)
            resp.raise_for_status()
            items = _parse_meesho_html(resp.text)
        except Exception as e:
            _log.warning("meesho.html_fallback.failed", error=str(e))

        # --- Playwright renders JS — needed for React-rendered product cards ---
        if not items:
            _log.info("meesho.html_fallback.trying_playwright")
            html = await _playwright_fetch(_HTML_URL)
            if html:
                items = _parse_meesho_html(html)
            else:
                _log.warning("meesho.playwright.no_html")

        return items

    async def _fetch_search(self, query: str) -> list[dict[str, Any]]:
        """Keyword search — returns products relevant to the query.

        Meesho's search page is React-rendered, so plain httpx usually returns a
        shell. Try httpx first (cheap), then fall back to Playwright which runs
        the JS and yields real product cards.
        """
        if self._client is None:
            return []
        from urllib.parse import quote_plus

        url = _SEARCH_URL.format(q=quote_plus(query))
        self._record_request_metric(method="search")
        items: list[dict[str, Any]] = []
        # Primary: curl_cffi browser-TLS impersonation (beats the WAF 403 that
        # plain httpx triggers). Falls back to Playwright (JS render) then httpx.
        from aegis.scrape.ecommerce_utils import extract_products_from_structured
        from aegis.scrape.http_client import impersonated_fetch

        code, html = await impersonated_fetch(url)
        if code == 200 and html:
            # Try DOM cards first, then site-agnostic structured data (JSON-LD /
            # __NEXT_DATA__) which survives SPA shells that have no DOM cards.
            items = _parse_meesho_html(html) or extract_products_from_structured(html)
        if not items:
            html2 = await _playwright_fetch(url)
            if html2:
                items = _parse_meesho_html(html2)
        if not items:
            try:
                await self._rate_limit()
                resp = await self._client.get(url)
                resp.raise_for_status()
                items = _parse_meesho_html(resp.text)
            except Exception as e:
                _log.warning("meesho.search.httpx_failed", error=str(e))
        _log.info("meesho.search", query=query, results=len(items))
        return items

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        query: str | None = None,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        # Query-driven search is the real intelligence path; trending JSON/HTML
        # is the no-query fallback so the adapter still surfaces hot products.
        if query and query.strip():
            items = await self._fetch_search(query.strip())
        else:
            items = await self._fetch_json()

        if not items:
            _log.info("meesho.empty_falling_back_to_html")
            items = await self._fetch_html()

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
                platform=Platform.MEESHO,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=None,
                posted_at=None,
            )

            order_count = int(raw_json.get("order_count") or 0)

            return ProductSignal(
                platform=Platform.MEESHO,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({"meesho", "social-commerce"}),
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(likes=order_count if order_count else None),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.7 if raw_json.get("price_inr") else 0.4,
                    source_confidence=0.70,
                ),
                content_hash=h,
                platform_specific={
                    "currency": raw_json.get("currency", "INR"),
                    "supplier": raw_json.get("supplier"),
                    "price_inr": raw_json.get("price_inr"),
                    "order_count": order_count,
                    "category": raw_json.get("category"),
                    "source": raw_json.get("source", "json_api"),
                },
            )
        except Exception as e:
            _log.warning("meesho.parse.failed", error=str(e))
            return None


__all__ = [
    "SCRAPER_VERSION",
    "MeeshoAdapter",
    "MeeshoConfig",
    "_extract_json_products",
    "_parse_meesho_html",
]
