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
from aegis.scrape.ecommerce_utils import _looks_like_captcha, flaresolverr_get, random_ua
from aegis.scrape.playwright_fetcher import fetch_page_html as _playwright_fetch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.flipkart")

SCRAPER_VERSION = "flipkart-0.1.0"

_URLS: tuple[str, ...] = (
    "https://www.flipkart.com/top-offers",
    "https://www.flipkart.com/bestsellers",
)

# CSS selectors as module-level constants
_CARD_SEL = "._1AtVbE, ._2kHMtA"
_TITLE_SEL = "._4rR01T, .IRpwTa, .s1Q9rs"
_PRICE_DISC_SEL = ".Nx9bqj, ._30jeq3"
_PRICE_ORIG_SEL = "._3I9_wc, ._3tbKJL ._3I9_wc"
_RATING_SEL = "._3LWZlK, ._1lRcqv"
_REVIEW_SEL = "._2_R_DZ, ._13vcmD"
_LINK_SEL = "a[href*='/p/']"


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
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._cfg.timeout_seconds),
            headers={
                "User-Agent": random_ua(),
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
                "Accept-Encoding": "gzip",
                "Referer": "https://www.flipkart.com/",
            },
            follow_redirects=True,
            http2=False,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch_url(self, url: str) -> list[dict[str, Any]]:
        """Fetch one URL: httpx → FlareSolverr → Playwright (stealth)."""
        if self._client is None:
            return []

        await self._rate_limit()
        self._record_request_metric(method="page_html")

        html: str | None = None
        try:
            resp = await self._client.get(url, headers={"User-Agent": random_ua()})
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
        return _parse_flipkart_html(html)

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        total = 0
        seen_titles: set[str] = set()

        for url in _URLS:
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
