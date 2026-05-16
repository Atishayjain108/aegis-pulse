"""Amazon source adapter.

Scrapes Amazon bestseller rankings from public product-listing pages using
plain HTTP — no browser automation or API key required.

The bestsellers page at /gp/bestsellers/{category} returns ASINs, ranks,
and product URLs in the initial HTML, which we extract with regex. Titles
come from the URL slug (approximate but useful for trend detection).

Categories scraped by default:
  books, electronics, toys-and-games, clothing-shoes-jewelry,
  sporting-goods, beauty, home-kitchen, health-personal-care

ToS Risk: AMBER — Amazon's robots.txt allows /gp/bestsellers/ pages.
Rate-limit: one request per ~7 seconds across categories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from aegis.core.logging import get_logger
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

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)

SCRAPER_VERSION = "amazon-0.2.0"

_AMZN_BASE = "https://www.amazon.com"

_DEFAULT_CATEGORIES: tuple[str, ...] = (
    "books",
    "electronics",
    "toys-and-games",
    "clothing-shoes-jewelry",
    "sporting-goods",
    "beauty",
    "home-kitchen",
    "health-personal-care",
)

# Matches one bestseller item in the HTML:
#   data-asin="B0XXXXXXX" ... #N badge ... href="/Product-Name/dp/B0XXXXXXX..."
_ITEM_RE = re.compile(
    r'data-asin="([A-Z0-9]{10})".*?zg-bdg-text">([^<]+)</span>.*?href="(/[^\s"]+/dp/\1[^"]*)',
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class AmazonConfig(AdapterConfig):
    """Amazon adapter config."""

    name: str = "amazon"
    per_source_rps: float = 0.15  # ~1 request per 7 seconds — conservative
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    mobile_user_agent: bool = False

    marketplace: str = "com"
    """Amazon marketplace TLD: 'com' | 'co.uk' | 'de' | 'in' | 'co.jp'."""

    categories: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_CATEGORIES)
    """Bestseller category slugs to cycle through per run."""


class AmazonAdapter(SourceAdapter[dict[str, Any]]):
    """Amazon bestsellers adapter — static HTTP, no browser required.

    Fetches /gp/bestsellers/{category} for each configured category and
    extracts rank, ASIN, and approximate title from the page HTML.
    No prices are available from this endpoint; the tier is set to
    TIER_3_SEARCH accordingly.
    """

    def __init__(self, config: AmazonConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._amzn_config = config if isinstance(config, AmazonConfig) else AmazonConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "amazon"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._amzn_config.timeout_seconds),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip",
            },
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
            raise RuntimeError("AmazonAdapter.setup() must run before fetch_raw()")

        cfg = self._amzn_config
        base_url = f"https://www.amazon.{cfg.marketplace}"
        total_yielded = 0

        for category in cfg.categories:
            if total_yielded >= limit or self.is_cancelled:
                return

            await self._rate_limit()
            self._record_request_metric(method="bestsellers_html")

            url = f"{base_url}/gp/bestsellers/{category}"
            try:
                resp = await self._client.get(url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                log.warning(
                    "amazon.fetch.http_error",
                    category=category,
                    status=e.response.status_code,
                )
                continue
            except httpx.RequestError as e:
                log.warning(
                    "amazon.fetch.request_error",
                    category=category,
                    error=str(e),
                )
                continue

            items = _parse_bestsellers_page(resp.text, category=category, base_url=base_url)
            if not items:
                log.warning("amazon.fetch.no_items", category=category, url=url)
                continue

            log.info("amazon.fetch.ok", category=category, count=len(items))
            for item in items:
                if total_yielded >= limit or self.is_cancelled:
                    return
                yield item
                total_yielded += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            asin: str = str(raw.get("asin") or "")
            if not asin:
                return None

            title: str = str(raw.get("title") or "")
            url: str = str(raw.get("url") or f"{_AMZN_BASE}/dp/{asin}")
            rank: int | None = _int_or_none(raw.get("rank"))
            category: str = str(raw.get("category") or "")

            # Use inverse rank as engagement proxy: rank 1 → 100, rank 100 → 1
            likes: int | None = max(1, 101 - rank) if rank is not None and rank > 0 else None

            h = compute_content_hash(
                platform=Platform.AMAZON,
                external_id=asin,
                url=url,
                title=title or None,
                raw_text=None,
                posted_at=None,
            )

            return ProductSignal(
                platform=Platform.AMAZON,
                # No price data from bestsellers; use T3_search tier
                tier=SourceTier.TIER_3_SEARCH,
                external_id=asin,
                url=url,  # type: ignore[arg-type]
                title=title or None,
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({category} if category else set()),
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(likes=likes),
                price=None,
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.6,
                    source_confidence=0.80,
                ),
                content_hash=h,
                platform_specific={
                    "asin": asin,
                    "rank": rank,
                    "category": category,
                },
            )
        except Exception as e:
            log.warning("amazon.parse.failed", error=str(e))
            return None


def _parse_bestsellers_page(html: str, *, category: str, base_url: str) -> list[dict[str, Any]]:
    """Extract bestseller items from an Amazon category page."""
    items: list[dict[str, Any]] = []
    try:
        for asin, rank_text, url_path in _ITEM_RE.findall(html):
            rank = _int_or_none(rank_text.lstrip("#"))

            clean_url = f"{base_url}/dp/{asin}"

            # Extract title from URL slug: /Product-Name-Here/dp/ASIN → "Product Name Here"
            slug_match = re.match(r"/(.+?)/dp/", url_path)
            title = slug_match.group(1).replace("-", " ").strip() if slug_match else ""

            items.append(
                {
                    "asin": asin,
                    "rank": rank,
                    "title": title,
                    "url": clean_url,
                    "category": category,
                }
            )
    except Exception as e:
        log.warning("amazon.parse_page.failed", category=category, error=str(e))
    return items


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


__all__ = ["AmazonAdapter", "AmazonConfig", "SCRAPER_VERSION"]
