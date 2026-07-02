"""NSE & BSE market data adapter — Indian exchange gainers/losers/most-active.

NSE endpoints (require Referer header):
  - Gainers:     https://www.nseindia.com/api/live-analysis-variations?index=gainers
  - Losers:      https://www.nseindia.com/api/live-analysis-variations?index=losers
  - Most active: https://www.nseindia.com/api/live-analysis-stockswatched

BSE public endpoint:
  - https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w

Score formula: abs(pct_change) * log1p(volume)

IMPORTANT: NSE frequently changes session-cookie requirements. If the JSON
parse fails or the list is empty, log a warning and return [] — never crash.

ToS Risk: AMBER — public market data API endpoints.
PLATFORM: nse_bse | TIER: T3_search (no price captured; % change + volume only)
"""
from __future__ import annotations

import asyncio
import hashlib
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
from aegis.scrape.schema_guard import validate_batch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.nse_bse")

SCRAPER_VERSION = "nse-bse-0.1.0"

NSE_HEADERS: dict[str, str] = {
    "Referer": "https://www.nseindia.com",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

_NSE_BASE = "https://www.nseindia.com/api"
_NSE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    (f"{_NSE_BASE}/live-analysis-variations?index=gainers", "gainers"),
    (f"{_NSE_BASE}/live-analysis-variations?index=losers", "losers"),
    # live-analysis-stockswatched returns 404; pre-open covers all securities
    (f"{_NSE_BASE}/market-data-pre-open?key=ALL", "pre_open"),
)

_BSE_URL = "https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"


def _extract_stocks(data: Any, category: str, exchange: str) -> list[dict[str, Any]]:
    """Parse raw JSON into normalised signal dicts. Returns [] on any failure."""
    try:
        # NSE wraps results differently per endpoint:
        # live-analysis-variations → data["allSec"]["data"] (20 stocks per section)
        # market-data-pre-open    → data["data"] (list of {metadata: {...}})
        # Legacy ADVANCES_DECLINES format also supported.
        if isinstance(data, dict):
            all_sec = data.get("allSec")
            if isinstance(all_sec, dict):
                stocks = all_sec.get("data") or []
            else:
                pre_open_items = data.get("data")
                if isinstance(pre_open_items, list) and pre_open_items and isinstance(pre_open_items[0], dict) and "metadata" in pre_open_items[0]:
                    # market-data-pre-open: unwrap the metadata layer
                    stocks = [item["metadata"] for item in pre_open_items if "metadata" in item]
                else:
                    stocks = (
                        data.get("ADVANCES_DECLINES")
                        or pre_open_items
                        or data.get("Table")
                        or []
                    )
        elif isinstance(data, list):
            stocks = data
        else:
            return []

        if not isinstance(stocks, list):
            return []

        raws: list[dict[str, Any]] = []
        scraped_at = datetime.now(UTC).isoformat()

        for stock in stocks:
            if not isinstance(stock, dict):
                continue

            symbol: str = str(
                stock.get("symbol") or stock.get("SYMBOL") or ""
            ).strip()
            company: str = str(
                stock.get("companyName") or stock.get("COMPANY") or symbol
            ).strip()

            try:
                pct_change = float(
                    stock.get("net_price")        # live-analysis-variations
                    or stock.get("pChange")        # pre-open metadata
                    or stock.get("change")
                    or stock.get("CHANGE")
                    or 0.0
                )
            except (TypeError, ValueError):
                pct_change = 0.0

            try:
                volume = int(
                    stock.get("trade_quantity")    # live-analysis-variations
                    or stock.get("totalTradedVolume")
                    or stock.get("VOLUME")
                    or 0
                )
            except (TypeError, ValueError):
                volume = 0

            if not symbol:
                continue

            direction = "+" if pct_change >= 0 else ""
            title = f"{company} ({symbol}) {direction}{pct_change:.2f}%"
            if exchange == "NSE":
                url = f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}"
            else:
                url = f"https://www.bseindia.com/stock-share-price/{symbol.lower()}"

            score = abs(pct_change) * log1p(volume) if volume else abs(pct_change)

            # Sentiment proxy: positive pct_change → bullish (1.0), negative → bearish (-1.0)
            sentiment = 1.0 if pct_change > 0 else (-1.0 if pct_change < 0 else 0.0)

            raws.append({
                "title": title,
                "url": url,
                "platform": Platform.NSE_BSE.value,
                "scraped_at": scraped_at,
                "author": None,
                "score": score,
                "views": None,
                "likes": None,
                "comments": None,
                "shares": None,
                "saves": None,
                "sentiment": sentiment,
                "raw_json": {
                    "currency": "INR",
                    "exchange": exchange,
                    "symbol": symbol,
                    "company": company,
                    "pct_change": pct_change,
                    "volume": volume,
                    "category": category,
                },
            })

        return raws
    except Exception as e:
        _log.warning(
            "nse_bse.extract_stocks.failed",
            exchange=exchange,
            category=category,
            error=str(e),
        )
        return []


@dataclass(frozen=True, slots=True)
class NSEBSEConfig(AdapterConfig):
    """NSE/BSE adapter configuration."""

    name: str = "nse_bse"
    per_source_rps: float = 0.2
    timeout_seconds: float = 20.0
    max_retries: int = 1
    use_cloudflare_bypass: bool = False
    fetch_bse: bool = False
    """BSE endpoint is less reliable; disabled by default."""


class NSEBSEAdapter(SourceAdapter[dict[str, Any]]):
    """NSE & BSE Indian market data — gainers, losers, most active.

    Sends the required Referer header for NSE API access and degrades
    gracefully when NSE changes its session-cookie scheme (returns [] not crash).
    """

    def __init__(self, config: NSEBSEConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, NSEBSEConfig) else NSEBSEConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "nse_bse"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "www.nseindia.com",
            http2=False,
            timeout=self._cfg.timeout_seconds,
            headers=NSE_HEADERS,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        # Shared pooled client (PASS5-5B / ADP-7) — release the reference, never close.
        self._client = None

    async def _fetch_json(
        self, url: str, category: str, exchange: str
    ) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="json")
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
            stocks = _extract_stocks(data, category, exchange)
            _log.info(
                "nse_bse.fetch.ok",
                exchange=exchange,
                category=category,
                count=len(stocks),
            )
            return stocks
        except httpx.HTTPStatusError as e:
            _log.warning(
                "nse_bse.http_error", url=url, status=e.response.status_code
            )
            return []
        except Exception as e:
            _log.warning("nse_bse.fetch.failed", url=url, category=category, error=str(e))
            return []

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        tasks = [self._fetch_json(url, cat, "NSE") for url, cat in _NSE_ENDPOINTS]
        if self._cfg.fetch_bse:
            tasks.append(self._fetch_json(_BSE_URL, "movers", "BSE"))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen: set[str] = set()
        raws: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                _log.warning("nse_bse.gather_error", error=str(result))
                continue
            for item in result:
                url = item.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    raws.append(item)

        valid = validate_batch(raws, Platform.NSE_BSE.value)
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
            pct_change: float = float(raw_json.get("pct_change") or 0.0)
            volume: int = int(raw_json.get("volume") or 0)
            exchange: str = str(raw_json.get("exchange") or "NSE")
            symbol: str = str(raw_json.get("symbol") or "")
            category: str = str(raw_json.get("category") or "mover")
            score_val = abs(pct_change) * log1p(volume) if volume else abs(pct_change)

            external_id = hashlib.sha1(  # noqa: S324
                url.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=Platform.NSE_BSE,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=None,
                posted_at=None,
            )

            tags: frozenset[str] = frozenset(
                t for t in ("nse-bse", exchange.lower(), category, "inr") if t
            )

            return ProductSignal(
                platform=Platform.NSE_BSE,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=tags,
                intent=IntentType.PASSIVE,
                engagement=EngagementMetrics(views=volume if volume else None),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.75,
                    source_confidence=0.85,
                ),
                content_hash=h,
                platform_specific={
                    "currency": "INR",
                    "exchange": exchange,
                    "symbol": symbol,
                    "pct_change": pct_change,
                    "volume": volume,
                    "score": score_val,
                },
            )
        except Exception as e:
            _log.warning("nse_bse.parse.failed", error=str(e))
            return None


__all__ = [
    "NSE_HEADERS",
    "SCRAPER_VERSION",
    "NSEBSEAdapter",
    "NSEBSEConfig",
    "_extract_stocks",
]
