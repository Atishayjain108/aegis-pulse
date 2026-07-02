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
import os
import re
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
from aegis.scrape.ecommerce_utils import (
    _looks_like_captcha,
    extract_card_image,
    flaresolverr_get,
)
from aegis.scrape.http_client import get_or_create_client

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
                        "image_url": extract_card_image(card),
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


_SEARCH_URL = "https://www.amazon.in/s?k={q}"
# Amazon search-results selectors (stable across recent layouts).
_SR_CARD_SEL = 'div[data-component-type="s-search-result"]'
_SR_TITLE_SEL = "h2 a span, h2 span"
_SR_PRICE_SEL = ".a-price .a-price-whole"
_SR_RATING_SEL = "span.a-icon-alt"
_SR_IMG_SEL = "img.s-image"


def _flaresolverr_default() -> str:
    return os.environ.get("AEGIS_SCRAPE_FLARESOLVERR_URL", "http://localhost:8191/v1")


def _parse_amazon_search(html: str) -> list[dict[str, Any]]:
    """Extract product cards from an Amazon.in search-results page."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("amazon_in.missing_dependency", dep="beautifulsoup4")
        return []

    items: list[dict[str, Any]] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        scraped_at = datetime.now(UTC).isoformat()

        for card in soup.select(_SR_CARD_SEL):
            try:
                asin = str(card.get("data-asin") or "").strip()
                title_el = card.select_one(_SR_TITLE_SEL)
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                price_el = card.select_one(_SR_PRICE_SEL)
                price_inr = _parse_float(
                    price_el.get_text(strip=True).replace(",", "") if price_el else ""
                )

                rating: float | None = None
                rating_el = card.select_one(_SR_RATING_SEL)
                if rating_el:
                    m = re.search(r"([0-5]\.\d)", rating_el.get_text())
                    rating = float(m.group(1)) if m else None

                review_count: int | None = None
                # Review count link sits next to the rating star icon.
                rc_el = card.select_one('a[href*="customerReviews"] span, span.a-size-base.s-underline-text')
                if rc_el:
                    review_count = _parse_int(rc_el.get_text(strip=True))

                img_el = card.select_one(_SR_IMG_SEL)
                image_url = str(img_el.get("src")) if img_el and img_el.get("src") else None

                href_el = card.select_one("h2 a, a.a-link-normal[href*='/dp/']")
                href = str(href_el.get("href") or "") if href_el else ""
                url = href if href.startswith("http") else f"{_BASE_URL}{href}"

                items.append({
                    "asin": asin or hashlib.sha1(title.encode()).hexdigest()[:16],  # noqa: S324
                    "title": title,
                    "url": url,
                    "category": "search",
                    "scraped_at": scraped_at,
                    "raw_json": {
                        "currency": "INR",
                        "image_url": image_url,
                        "price_inr": price_inr,
                        "rating": rating,
                        "review_count": review_count,
                    },
                })
            except Exception as e:
                _log.warning("amazon_in.search_card.failed", error=str(e))
    except Exception as e:
        _log.warning("amazon_in.parse_search.failed", error=str(e))

    return items


# ---------------------------------------------------------------------------
# Product-page (/dp/) enrichment — Best Sellers Rank + rating + reviews.
#
# The search-card parser misses these; the /dp/ page carries them and returns
# 200 even when the search endpoint 403s. BSR is the single strongest *free*
# demand proxy on Amazon. We extract from raw HTML (regex) rather than CSS
# selectors because the detail-bullet markup shifts often but the surrounding
# text ("Best Sellers Rank", "out of 5 stars", "ratings") is stable.
# ---------------------------------------------------------------------------
_BSR_RE = re.compile(
    r"Best Sellers Rank.*?#([\d,]+)\s+in\s+([\w &',;.\-]+?)\s*[\(<]",
    re.IGNORECASE | re.DOTALL,
)
_DP_RATING_RE = re.compile(r"([0-5](?:\.\d)?)\s*out of\s*5\s*stars", re.IGNORECASE)
_DP_REVIEWS_RE = re.compile(r"([\d,]+)\s+(?:global\s+)?ratings?", re.IGNORECASE)
_DP_PRICE_RE = re.compile(r'class="a-offscreen"[^>]*>\s*₹\s*([\d,]+(?:\.\d+)?)')


def parse_dp_page(html: str) -> dict[str, Any]:
    """Extract BSR + rating + review_count + price from an Amazon /dp/ page.

    Pure function over raw HTML. Returns a dict with ``None`` for any field that
    could not be found. Never raises.
    """
    out: dict[str, Any] = {
        "bsr": None,
        "bsr_category": None,
        "rating": None,
        "review_count": None,
        "price_inr": None,
    }
    if not html:
        return out
    try:
        m = _BSR_RE.search(html)
        if m:
            out["bsr"] = _parse_int(m.group(1))
            cat = m.group(2).strip()
            out["bsr_category"] = cat[:80] if cat else None
        m = _DP_RATING_RE.search(html)
        if m:
            out["rating"] = _parse_float(m.group(1))
        m = _DP_REVIEWS_RE.search(html)
        if m:
            out["review_count"] = _parse_int(m.group(1))
        m = _DP_PRICE_RE.search(html)
        if m:
            out["price_inr"] = _parse_float(m.group(1).replace(",", ""))
    except Exception as e:  # pragma: no cover - defensive
        _log.warning("amazon_in.dp_parse.failed", error=str(e))
    return out


@dataclass(frozen=True, slots=True)
class AmazonINConfig(AdapterConfig):
    """Amazon India adapter configuration."""

    name: str = "amazon_in"
    per_source_rps: float = 0.12  # ~1 req per 8s — conservative for Indian marketplace
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    flaresolverr_url: str = field(default_factory=_flaresolverr_default)
    categories: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_CATEGORIES)
    # /dp/ BSR enrichment: fetch product pages for the top-N search hits to
    # attach Best Sellers Rank (demand proxy). Gentle by design — Amazon 503s
    # if hammered. Disable with AEGIS_AMAZON_BSR=0.
    enrich_bsr: bool = field(
        default_factory=lambda: os.environ.get("AEGIS_AMAZON_BSR", "1").strip()
        not in {"0", "false", "False"}
    )
    bsr_top_n: int = 8
    # Hard ceiling on the whole BSR-enrichment pass so it can never delay or
    # suppress the search results under the harvest budget.
    bsr_budget_s: float = 12.0


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
        # Shared client: per-host throttle + header/UA rotation + optional
        # proxy via http_client event hooks (see http_client.py).
        self._client = await get_or_create_client(
            "www.amazon.in",
            timeout=self._cfg.timeout_seconds,
            headers={
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9,hi;q=0.7",
                "Accept-Encoding": "gzip",
            },
            http2=False,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        self._client = None  # shared client — release reference, never close

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

    async def _fetch_search(self, query: str) -> list[dict[str, Any]]:
        """Keyword search via FlareSolverr (Amazon blocks plain httpx search)."""
        if self._client is None:
            return []
        from urllib.parse import quote_plus

        await self._rate_limit()
        self._record_request_metric(method="search_html")
        url = _SEARCH_URL.format(q=quote_plus(query))
        html: str | None = None
        try:
            resp = await self._client.get(url, headers={"Referer": _BASE_URL})
            if resp.status_code not in (403, 503) and not _looks_like_captcha(resp.text):
                resp.raise_for_status()
                html = resp.text
        except Exception:
            html = None
        if not html:
            # Dedicated plain client — the shared client's per-host event hooks
            # don't apply to the FlareSolverr endpoint (localhost).
            async with httpx.AsyncClient(timeout=70.0) as fs:
                html = await flaresolverr_get(url, self._cfg.flaresolverr_url, fs, None)
        if not html:
            _log.warning("amazon_in.search.no_html", query=query)
            return []
        items = _parse_amazon_search(html)
        _log.info("amazon_in.search", query=query, results=len(items))
        if self._cfg.enrich_bsr and items:
            # Time-box enrichment hard: it must NEVER delay/suppress the search
            # results. Whatever BSR data lands within the budget is a bonus;
            # a timeout leaves already-enriched items intact and returns the
            # rest with their search-card data untouched.
            import contextlib

            with contextlib.suppress(TimeoutError, Exception):
                await asyncio.wait_for(
                    self._enrich_bsr(items[: self._cfg.bsr_top_n]),
                    timeout=self._cfg.bsr_budget_s,
                )
        return items

    async def _fetch_dp(self, asin: str) -> dict[str, Any]:
        """Fetch one /dp/ page and extract BSR + rating + reviews. Fail-open.

        The /dp/ page returns 200 even when search 403s. From a non-browser /
        cookieless client Amazon often serves a *degraded* page with the BSR
        detail section stripped, so when the direct page lacks the "Seller Rank"
        marker we retry once via FlareSolverr (if configured) which renders the
        full page. Rate-limited like every other request.
        """
        if self._client is None or not asin:
            return {}
        await self._rate_limit()
        self._record_request_metric(method="dp_html")
        url = f"{_BASE_URL}/dp/{asin}"
        html: str | None = None
        try:
            resp = await self._client.get(url, headers={"Referer": _BASE_URL})
            if resp.status_code not in (403, 503) and not _looks_like_captcha(resp.text):
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            _log.debug("amazon_in.dp.failed", asin=asin, error=str(e))
            html = None
        # Degraded page (no BSR section) → render the full page via FlareSolverr.
        if (not html or "Seller Rank" not in html) and self._cfg.flaresolverr_url:
            try:
                async with httpx.AsyncClient(timeout=70.0) as fs:
                    rendered = await flaresolverr_get(url, self._cfg.flaresolverr_url, fs, None)
                if rendered and "Seller Rank" in rendered:
                    html = rendered
            except Exception:
                pass
        return parse_dp_page(html) if html else {}

    async def _enrich_bsr(self, items: list[dict[str, Any]]) -> None:
        """Attach BSR/rating/review_count from /dp/ pages onto search items.

        Sequential (not gathered) — Amazon 503s on bursts and the adapter's
        rate limiter already paces requests. Best-effort: any miss leaves the
        item untouched. Mutates ``items`` in place.
        """
        for item in items:
            if self.is_cancelled:
                return
            asin = str(item.get("asin") or "")
            if not asin or len(asin) != 10:
                continue
            dp = await self._fetch_dp(asin)
            if not dp:
                continue
            rj = item.setdefault("raw_json", {})
            if dp.get("bsr") is not None:
                rj["bsr"] = dp["bsr"]
                rj["bsr_category"] = dp.get("bsr_category")
            # Fill rating/reviews/price only when the search card missed them.
            for key in ("rating", "review_count", "price_inr"):
                if rj.get(key) in (None, 0) and dp.get(key) is not None:
                    rj[key] = dp[key]

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        query: str | None = None,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        # Query-driven search is the real intelligence path; bestsellers is the
        # no-query fallback.
        if query and query.strip():
            for item in (await self._fetch_search(query.strip()))[:limit]:
                if self.is_cancelled:
                    return
                yield item
            return

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

            # Best Sellers Rank (from /dp/ enrichment) is a far stronger demand
            # proxy than a search position. Lower BSR = hotter seller. Fold it
            # into the engagement magnitude (a #1 item ≈ log1p(1e6) ≈ 138).
            bsr = raw_json.get("bsr")
            if isinstance(bsr, int) and bsr > 0:
                bsr_likes = int(log1p(1_000_000.0 / bsr))
                likes = max(likes or 0, bsr_likes)

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
                    "image_url": raw_json.get("image_url"),
                    "price_inr": raw_json.get("price_inr"),
                    "disc_price": raw_json.get("price_inr"),
                    "rating": raw_json.get("rating"),
                    "review_count": raw_json.get("review_count"),
                    "bsr": raw_json.get("bsr"),
                    "bsr_category": raw_json.get("bsr_category"),
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
    "parse_dp_page",
]
