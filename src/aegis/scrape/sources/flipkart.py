"""Flipkart adapter — top-offers and bestsellers pages.

Strategy:
  1. Try plain httpx with a rotated User-Agent.
  2. If response is 403 or looks like a CAPTCHA page →
     call flaresolverr_get() via the ConcurrencyGovernor semaphore.
  3. Parse product cards with BeautifulSoup + lxml.

Extracts: title, URL, discounted price, original price, rating, review_count.
  discount_pct = round((orig - disc) / orig * 100, 1) if orig > 0 else 0
  score = rating * log1p(review_count) if both present, else 0.0

ToS Risk: RED — Flipkart actively defends against bots; FlareSolverr may be needed.
Rate-limit: 0.1 req/s.
"""
from __future__ import annotations

import hashlib
import json
import os
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
from aegis.scrape.playwright_fetcher import fetch_page_html as _playwright_fetch
from aegis.scrape.session_tokens import get_session_bundle

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.flipkart")

SCRAPER_VERSION = "flipkart-0.1.0"

# No-query fallback listing pages. The old /top-offers + /bestsellers paths
# now 404 — replaced with search-results pages that return parseable product
# cards (verified live 2026-06-19). The search layout is what _parse_flipkart_html
# already handles for the query-driven path.
_URLS: tuple[str, ...] = (
    "https://www.flipkart.com/search?q=trending",
    "https://www.flipkart.com/search?q=best%20sellers",
)
# Keyword search — returns products relevant to the query.
_SEARCH_URL = "https://www.flipkart.com/search?q={q}"

# CSS selectors — multiple variants per field because Flipkart rotates its
# obfuscated class names. `div[data-id]` is the durable per-product anchor on
# search-results pages; the legacy classes cover offers/bestseller pages.
_CARD_SEL = "div[data-id], ._1AtVbE, ._2kHMtA, ._75nlfW, .slAVV4, .tUxRFH, .cPHDOP"
_TITLE_SEL = "a.pIpigb, .KzDlHZ, .wjcEIp, ._4rR01T, .IRpwTa, .s1Q9rs, a.WKTcLC, .syl9yP"
_PRICE_DISC_SEL = ".hZ3P6w, .Nx9bqj, ._30jeq3, ._4b5DiR"
_PRICE_ORIG_SEL = ".yRaY8j, ._3I9_wc, ._3tbKJL ._3I9_wc"
_RATING_SEL = ".CjyrHS, .XQDdHH, ._3LWZlK, ._1lRcqv, ._5OesEi span"
_REVIEW_SEL = ".Wphh3N, ._2_R_DZ, ._13vcmD"
_LINK_SEL = "a[href*='/p/'], a.CGtC98, a.VJA3rP, a.wjcEIp"


def _parse_inr(text: str) -> float | None:
    """Parse an INR price string like '₹1,299' → 1299.0."""
    try:
        clean = text.replace("₹", "").replace(",", "").strip()
        return float(clean.split()[0])
    except (ValueError, IndexError):
        return None


def _parse_rating(text: str) -> float | None:
    try:
        return float(text.strip().split()[0].replace("★", ""))
    except (ValueError, IndexError):
        return None


def _parse_review_count(text: str) -> int | None:
    try:
        return int(text.strip().replace(",", "").split()[0])
    except (ValueError, IndexError):
        return None


def _extract_initial_state(html: str) -> dict[str, Any] | None:
    """Pull the ``window.__INITIAL_STATE__`` JSON blob out of a Flipkart page.

    Brace-matched scan (not a greedy regex) so nested objects parse correctly.
    Returns the decoded dict, or ``None`` when the blob is absent/unparseable.
    """
    marker = "__INITIAL_STATE__"
    idx = html.find(marker)
    if idx == -1:
        return None
    brace = html.find("{", idx)
    if brace == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    end = -1
    for i in range(brace, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return None
    try:
        return json.loads(html[brace:end])
    except (json.JSONDecodeError, ValueError):
        return None


# Field-name aliases Flipkart has used for the same concept across layouts.
_T_TITLE = ("title", "name", "productTitle")
_T_URL = ("url", "smartUrl", "baseUrl", "pageUri", "productUrl")
_T_PRICE = ("finalPrice", "sellingPrice", "price", "value", "decimalValue")
_T_MRP = ("mrp", "strikeOff", "totalPrice")


def _first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", {}):
            return v
    return None


def _coerce_price(v: Any) -> float | None:
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, dict):
        inner = _first(v, _T_PRICE)
        return _coerce_price(inner) if inner is not None else None
    if isinstance(v, str):
        return _parse_inr(v)
    return None


def _walk_products(node: Any, out: list[dict[str, Any]], seen: set[str], depth: int = 0) -> None:
    """Recursively collect product-like dicts from the __INITIAL_STATE__ tree.

    A node counts as a product when it carries a non-empty title AND a parseable
    price. Robust to Flipkart moving products around the tree because it keys on
    *shape*, not a fixed path. Bounded recursion depth as a safety valve.
    """
    if depth > 25 or len(out) >= 200:
        return
    if isinstance(node, dict):
        title = _first(node, _T_TITLE)
        price = _coerce_price(_first(node, _T_PRICE))
        if isinstance(title, str) and title.strip() and price is not None:
            url = _first(node, _T_URL)
            url_str = str(url) if url else ""
            if url_str and not url_str.startswith("http"):
                url_str = f"https://www.flipkart.com{url_str}"
            key = url_str or title.strip()
            if key not in seen:
                seen.add(key)
                mrp = _coerce_price(_first(node, _T_MRP))
                rating = node.get("rating")
                if isinstance(rating, dict):
                    rating = rating.get("average") or rating.get("value")
                reviews = node.get("reviewCount") or node.get("ratingCount")
                out.append({
                    "title": title.strip(),
                    "url": url_str or "https://www.flipkart.com",
                    "scraped_at": datetime.now(UTC).isoformat(),
                    "raw_json": {
                        "currency": "INR",
                        "image_url": node.get("imageUrl") or node.get("image"),
                        "disc_price": price,
                        "orig_price": mrp,
                        "discount_pct": (
                            round((mrp - price) / mrp * 100, 1)
                            if mrp and price and mrp > price > 0
                            else 0.0
                        ),
                        "rating": float(rating) if isinstance(rating, int | float) else None,
                        "review_count": int(reviews) if isinstance(reviews, int | float) else None,
                        "source": "initial_state",
                    },
                })
        for v in node.values():
            _walk_products(v, out, seen, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _walk_products(v, out, seen, depth + 1)


def _parse_flipkart_initial_state(html: str) -> list[dict[str, Any]]:
    """Primary Flipkart parser: walk the __INITIAL_STATE__ JS blob.

    More stable than CSS selectors (which Flipkart rotates frequently). Returns
    [] when the blob is missing — the caller then falls back to the HTML parser.
    """
    state = _extract_initial_state(html)
    if state is None:
        return []
    out: list[dict[str, Any]] = []
    try:
        _walk_products(state, out, set())
    except Exception as e:  # pragma: no cover - defensive
        _log.warning("flipkart.initial_state.walk_failed", error=str(e))
    return out


def _parse_flipkart_html(html: str) -> list[dict[str, Any]]:
    """Extract product cards from a Flipkart page. Returns [] on any failure."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("flipkart.missing_dependency", dep="beautifulsoup4")
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
                    # Flipkart's product image alt-text carries the full name.
                    img_el = card.select_one("img")
                    title = str(img_el.get("alt") or "").strip() if img_el else ""
                if not title:
                    continue

                link_el = card.select_one(_LINK_SEL)
                rel_href = link_el.get("href", "") if link_el else ""
                url = f"https://www.flipkart.com{rel_href}" if rel_href else "https://www.flipkart.com"

                disc_el = card.select_one(_PRICE_DISC_SEL)
                disc_price = _parse_inr(disc_el.get_text()) if disc_el else None

                orig_el = card.select_one(_PRICE_ORIG_SEL)
                orig_price = _parse_inr(orig_el.get_text()) if orig_el else None

                discount_pct = 0.0
                if orig_price and disc_price and orig_price > 0 and disc_price < orig_price:
                    discount_pct = round((orig_price - disc_price) / orig_price * 100, 1)

                rating: float | None = None
                rating_el = card.select_one(_RATING_SEL)
                if rating_el:
                    rating = _parse_rating(rating_el.get_text())

                review_count: int | None = None
                review_el = card.select_one(_REVIEW_SEL)
                if review_el:
                    review_count = _parse_review_count(review_el.get_text())

                score = (
                    rating * log1p(review_count)
                    if (rating is not None and review_count is not None)
                    else 0.0
                )

                items.append({
                    "title": title,
                    "url": url,
                    "scraped_at": scraped_at,
                    "raw_json": {
                        "currency": "INR",
                        "image_url": extract_card_image(card),
                        "disc_price": disc_price,
                        "orig_price": orig_price,
                        "discount_pct": discount_pct,
                        "rating": rating,
                        "review_count": review_count,
                        "score": score,
                    },
                })
            except Exception as e:
                _log.warning("flipkart.card.failed", error=str(e))
                continue

    except Exception as e:
        _log.warning("flipkart.parse_html.failed", error=str(e))

    return items


def _flaresolverr_default() -> str:
    return os.environ.get("AEGIS_SCRAPE_FLARESOLVERR_URL", "http://localhost:8191/v1")


@dataclass(frozen=True, slots=True)
class FlipkartConfig(AdapterConfig):
    """Flipkart adapter configuration."""

    name: str = "flipkart"
    per_source_rps: float = 0.1  # conservative — high anti-bot risk
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = True
    flaresolverr_url: str = field(default_factory=_flaresolverr_default)


class FlipkartAdapter(SourceAdapter[dict[str, Any]]):
    """Flipkart top-offers + bestsellers adapter with FlareSolverr fallback.

    Tries plain httpx first (rotated UA). If the response is 403 or a CAPTCHA
    page, falls back to FlareSolverr. If FlareSolverr is unavailable, logs a
    warning and returns [].
    """

    def __init__(
        self,
        config: FlipkartConfig | AdapterConfig,
        *,
        governor: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, FlipkartConfig) else FlipkartConfig()
        self._governor = governor
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "flipkart"

    async def setup(self, ctx: ScrapeContext) -> None:
        # Shared client: per-host throttle + header/UA rotation + optional
        # proxy via http_client event hooks (see http_client.py).
        self._client = await get_or_create_client(
            "www.flipkart.com",
            timeout=self._cfg.timeout_seconds,
            headers={
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
                "Accept-Encoding": "gzip",
                "Referer": "https://www.flipkart.com/",
            },
            http2=False,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        self._client = None  # shared client — release reference, never close

    async def _session_headers(self) -> dict[str, str]:
        """Harvest a homepage browser session so the cheap httpx path clears
        Flipkart's WAF 403 more often before falling back to FlareSolverr.

        Returns an empty dict when Playwright is unavailable — the fetch then
        proceeds without cookies and relies on the FlareSolverr/Playwright
        fallbacks already in :meth:`_fetch_url`.
        """
        bundle = await get_session_bundle(
            "https://www.flipkart.com/",
            host="www.flipkart.com",
        )
        if bundle is None or not bundle.cookies:
            return {}
        return {"Cookie": bundle.cookie_header}

    async def _fetch_url(self, url: str) -> list[dict[str, Any]]:
        """Fetch one URL: httpx → FlareSolverr → Playwright (stealth)."""
        if self._client is None:
            return []

        await self._rate_limit()
        self._record_request_metric(method="page_html")

        html: str | None = None
        try:
            resp = await self._client.get(url, headers=await self._session_headers())
            if resp.status_code == 403 or _looks_like_captcha(resp.text):
                _log.warning("flipkart.plain_blocked", url=url, status=resp.status_code)
                html = await flaresolverr_get(
                    url,
                    self._cfg.flaresolverr_url,
                    self._client,
                    self._governor,
                )
                if html is None:
                    _log.info("flipkart.trying_playwright", url=url)
                    html = await _playwright_fetch(url)
            else:
                resp.raise_for_status()
                html = resp.text
        except httpx.HTTPStatusError as e:
            _log.warning("flipkart.http_error", url=url, status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("flipkart.fetch.failed", url=url, error=str(e))
            return []

        if html is None:
            _log.warning("flipkart.no_html", url=url)
            return []
        # Primary: the __INITIAL_STATE__ JS blob (stable across CSS rotations).
        # Fallback: the CSS-selector HTML parser when the blob is absent/empty.
        items = _parse_flipkart_initial_state(html)
        if items:
            _log.info("flipkart.parsed", url=url, results=len(items), via="initial_state")
            return items
        items = _parse_flipkart_html(html)
        _log.info("flipkart.parsed", url=url, results=len(items), via="html")
        return items

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        query: str | None = None,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        total = 0
        seen_titles: set[str] = set()

        # Query-driven search is the real intelligence path; offers pages are the
        # no-query fallback.
        if query and query.strip():
            from urllib.parse import quote_plus

            urls: tuple[str, ...] = (_SEARCH_URL.format(q=quote_plus(query.strip())),)
        else:
            urls = _URLS

        for url in urls:
            if total >= limit or self.is_cancelled:
                return
            items = await self._fetch_url(url)
            for item in items:
                if total >= limit or self.is_cancelled:
                    return
                title = item.get("title", "")
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    yield item
                    total += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or "https://www.flipkart.com")
            if not title:
                return None

            raw_json = raw.get("raw_json") or {}

            external_id = hashlib.sha1(  # noqa: S324
                url.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=Platform.FLIPKART,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=None,
                posted_at=None,
            )

            score = float(raw_json.get("score") or 0.0)

            return ProductSignal(
                platform=Platform.FLIPKART,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({"flipkart"}),
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(likes=int(score) if score else None),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.RED,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.6 if raw_json.get("disc_price") else 0.4,
                    source_confidence=0.65,
                ),
                content_hash=h,
                platform_specific={
                    "currency": raw_json.get("currency", "INR"),
                    "image_url": raw_json.get("image_url"),
                    "disc_price": raw_json.get("disc_price"),
                    "orig_price": raw_json.get("orig_price"),
                    "discount_pct": raw_json.get("discount_pct", 0.0),
                    "rating": raw_json.get("rating"),
                    "review_count": raw_json.get("review_count"),
                },
            )
        except Exception as e:
            _log.warning("flipkart.parse.failed", error=str(e))
            return None


__all__ = [
    "SCRAPER_VERSION",
    "FlipkartAdapter",
    "FlipkartConfig",
    "_parse_flipkart_html",
]
