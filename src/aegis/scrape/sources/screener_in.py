"""Screener.in adapter — Indian stock fundamentals and company discovery.

Two data paths:
  1. Company search API: https://www.screener.in/api/company/search/?q={keyword}
     Queries each keyword from the configured topic list; returns JSON.
  2. Top companies explore: https://www.screener.in/explore/?sort=market_cap&order=desc
     BeautifulSoup parses the company table for name, ticker, market cap, P/E.

ToS Risk: AMBER — public website and search API; no login required.
PLATFORM: screener_in | TIER: T3_search
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
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
from aegis.scrape.schema_guard import validate_batch
from aegis.scrape.sentiment import score_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.screener_in")

SCRAPER_VERSION = "screener-in-0.1.0"

_BASE = "https://www.screener.in"
_SEARCH_URL = f"{_BASE}/api/company/search/?q="
_EXPLORE_URL = f"{_BASE}/explore/?sort=market_cap&order=desc"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Referer": "https://www.screener.in",
}

_DEFAULT_KEYWORDS: tuple[str, ...] = ("ecommerce", "fintech", "llm", "india", "saas")


def _parse_search_results(data: Any, keyword: str) -> list[dict[str, Any]]:
    """Parse company search JSON into signal dicts. Returns [] on any failure."""
    try:
        if not isinstance(data, list):
            return []

        scraped_at = datetime.now(UTC).isoformat()
        raws: list[dict[str, Any]] = []

        for item in data:
            if not isinstance(item, dict):
                continue
            name: str = str(item.get("name") or "").strip()
            url_path: str = str(item.get("url") or "").strip()
            if not name or not url_path:
                continue
            url = f"{_BASE}{url_path}" if url_path.startswith("/") else url_path

            raws.append({
                "title": name,
                "url": url,
                "platform": Platform.SCREENER_IN.value,
                "scraped_at": scraped_at,
                "author": None,
                "score": 0.0,
                "views": None,
                "likes": None,
                "comments": None,
                "shares": None,
                "saves": None,
                "sentiment": score_text(name),
                "raw_json": {
                    "source": "search",
                    "keyword": keyword,
                    "company_type": item.get("company_type"),
                },
            })

        return raws
    except Exception as e:
        _log.warning("screener_in.parse_search.failed", keyword=keyword, error=str(e))
        return []


def _parse_explore_html(html: str) -> list[dict[str, Any]]:
    """Parse the Screener.in explore table. Returns [] on any failure."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("screener_in.missing_dependency", dep="beautifulsoup4")
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
        raws: list[dict[str, Any]] = []
        scraped_at = datetime.now(UTC).isoformat()

        # The explore page has a <table class="data-table ...">
        table = soup.find("table", class_=lambda c: c and "data-table" in c)
        if table is None:
            table = soup.find("table")
        if table is None:
            return []

        for row in table.find_all("tr")[1:]:  # skip header
            cols = row.find_all("td")
            if len(cols) < 2:
                continue

            name_cell = cols[0]
            link_tag = name_cell.find("a")
            if not link_tag:
                continue

            name = link_tag.get_text(strip=True)
            href = str(link_tag.get("href") or "")
            url = f"{_BASE}{href}" if href.startswith("/") else href

            # Ticker is typically the last non-empty segment of the URL path
            parts = [p for p in href.split("/") if p]
            ticker = parts[-1] if parts else ""

            market_cap = cols[1].get_text(strip=True) if len(cols) > 1 else ""
            pe_ratio = cols[2].get_text(strip=True) if len(cols) > 2 else ""

            if not name or not url:
                continue

            display_title = f"{name} ({ticker})" if ticker else name

            raws.append({
                "title": display_title,
                "url": url,
                "platform": Platform.SCREENER_IN.value,
                "scraped_at": scraped_at,
                "author": None,
                "score": 0.0,
                "views": None,
                "likes": None,
                "comments": None,
                "shares": None,
                "saves": None,
                "sentiment": score_text(name),
                "raw_json": {
                    "source": "explore",
                    "ticker": ticker,
                    "market_cap": market_cap,
                    "pe_ratio": pe_ratio,
                },
            })

        return raws
    except Exception as e:
        _log.warning("screener_in.parse_explore.failed", error=str(e))
        return []


@dataclass(frozen=True, slots=True)
class ScreenerInConfig(AdapterConfig):
    """Screener.in adapter configuration."""

    name: str = "screener_in"
    per_source_rps: float = 0.3
    timeout_seconds: float = 25.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    keywords: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_KEYWORDS)
    """Keywords to query via the company search API."""
    fetch_explore: bool = True
    """Whether to also scrape the top market-cap explore page."""


class ScreenerInAdapter(SourceAdapter[dict[str, Any]]):
    """Screener.in company search + top-market-cap explore adapter."""

    def __init__(self, config: ScreenerInConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._si_config = (
            config if isinstance(config, ScreenerInConfig) else ScreenerInConfig()
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "screener_in"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._si_config.timeout_seconds),
            headers=_HEADERS,
            follow_redirects=True,
            http2=False,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _search_keyword(self, keyword: str) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="search_json")
        try:
            resp = await self._client.get(f"{_SEARCH_URL}{keyword}")
            resp.raise_for_status()
            return _parse_search_results(resp.json(), keyword)
        except httpx.HTTPStatusError as e:
            _log.warning(
                "screener_in.search.http_error",
                keyword=keyword,
                status=e.response.status_code,
            )
            return []
        except Exception as e:
            _log.warning("screener_in.search.failed", keyword=keyword, error=str(e))
            return []

    async def _fetch_explore(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="explore_html")
        try:
            resp = await self._client.get(_EXPLORE_URL)
            resp.raise_for_status()
            return _parse_explore_html(resp.text)
        except httpx.HTTPStatusError as e:
            _log.warning("screener_in.explore.http_error", status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("screener_in.explore.failed", error=str(e))
            return []

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        tasks: list[Any] = [
            self._search_keyword(kw) for kw in self._si_config.keywords
        ]
        if self._si_config.fetch_explore:
            tasks.append(self._fetch_explore())

        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen: set[str] = set()
        raws: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                _log.warning("screener_in.gather_error", error=str(result))
                continue
            for item in result:
                url = item.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    raws.append(item)

        valid = validate_batch(raws, Platform.SCREENER_IN.value)
        for item in valid[:limit]:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = (raw.get("title") or "").strip()
            url = raw.get("url") or ""
            if not url:
                return None

            raw_json = raw.get("raw_json") or {}
            source: str = str(raw_json.get("source") or "search")
            ticker: str = str(raw_json.get("ticker") or raw_json.get("keyword") or "")
            sentiment_val: float = float(raw.get("sentiment") or 0.0)

            external_id = hashlib.sha1(  # noqa: S324
                url.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=Platform.SCREENER_IN,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=None,
                posted_at=None,
            )

            tags: frozenset[str] = frozenset(
                t for t in ("screener-in", source, "india", "equity") if t
            )

            return ProductSignal(
                platform=Platform.SCREENER_IN,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=tags,
                intent=IntentType.SEARCH,
                engagement=EngagementMetrics(),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.65,
                    source_confidence=0.80,
                ),
                content_hash=h,
                platform_specific={
                    "source": source,
                    "ticker": ticker,
                    "market_cap": raw_json.get("market_cap"),
                    "pe_ratio": raw_json.get("pe_ratio"),
                    "sentiment": sentiment_val,
                },
            )
        except Exception as e:
            _log.warning("screener_in.parse.failed", error=str(e))
            return None


__all__ = [
    "SCRAPER_VERSION",
    "ScreenerInAdapter",
    "ScreenerInConfig",
    "_parse_explore_html",
    "_parse_search_results",
]
