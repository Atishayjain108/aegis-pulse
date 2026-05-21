"""Moneycontrol adapter — Indian financial news and stock movers.

Three data sources:
  1. News RSS:     https://www.moneycontrol.com/rss/latestnews.xml
  2. Markets RSS:  https://www.moneycontrol.com/rss/marketreports.xml
  3. Stock movers: https://www.moneycontrol.com/stocks/marketstats/mover_shakers.php

Stock mover signals carry raw_json["currency"] = "INR" and
raw_json["data_type"] = "stock_mover".

ToS Risk: AMBER — public RSS and HTML; no login required.
Rate-limit: 0.2 req/s.
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
from aegis.scrape.schema_guard import validate_batch
from aegis.scrape.sentiment import score_text
from aegis.scrape.sources._rss_base import _parse_date, fetch_feed_entries

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.moneycontrol")

SCRAPER_VERSION = "moneycontrol-0.1.0"

_NEWS_FEED = "https://www.moneycontrol.com/rss/latestnews.xml"
_MARKETS_FEED = "https://www.moneycontrol.com/rss/marketreports.xml"
_MOVERS_URL = "https://www.moneycontrol.com/stocks/marketstats/mover_shakers.php"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
}


def _parse_movers_html(html: str) -> list[dict[str, Any]]:
    """Extract stock mover rows from Moneycontrol HTML. Returns [] on any failure."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("moneycontrol.missing_dependency", dep="beautifulsoup4")
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
        raws: list[dict[str, Any]] = []
        scraped_at = datetime.now(UTC).isoformat()

        for table in soup.find_all("table"):
            for row in table.find_all("tr")[1:]:  # skip header row
                cols = row.find_all("td")
                if len(cols) < 3:
                    continue

                ticker = cols[0].get_text(strip=True)
                pct_text = cols[1].get_text(strip=True).replace("%", "").replace("+", "").strip()
                vol_text = cols[2].get_text(strip=True).replace(",", "").strip()

                if not ticker:
                    continue

                try:
                    pct_change = float(pct_text)
                except (ValueError, TypeError):
                    continue

                try:
                    volume = int(vol_text) if vol_text.isdigit() else 0
                except (ValueError, TypeError):
                    volume = 0

                direction = "+" if pct_change >= 0 else ""
                title = f"{ticker} {direction}{pct_change:.2f}%"
                url = f"https://www.moneycontrol.com/india/stockpricequote/{ticker.lower()}"
                score = abs(pct_change) * log1p(volume) if volume else abs(pct_change)

                raws.append({
                    "title": title,
                    "url": url,
                    "platform": Platform.MONEYCONTROL.value,
                    "scraped_at": scraped_at,
                    "author": None,
                    "score": score,
                    "views": None,
                    "likes": None,
                    "comments": None,
                    "shares": None,
                    "saves": None,
                    "sentiment": score_text(title),
                    "raw_json": {
                        "currency": "INR",
                        "data_type": "stock_mover",
                        "ticker": ticker,
                        "pct_change": pct_change,
                        "volume": volume,
                    },
                })

        return raws
    except Exception as e:
        _log.warning("moneycontrol.parse_movers.failed", error=str(e))
        return []


@dataclass(frozen=True, slots=True)
class MoneycontrolConfig(AdapterConfig):
    """Moneycontrol adapter configuration."""

    name: str = "moneycontrol"
    per_source_rps: float = 0.2
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False
    fetch_movers: bool = True
    """Whether to fetch the stock movers HTML page in addition to RSS feeds."""


class MoneycontrolAdapter(SourceAdapter[dict[str, Any]]):
    """Moneycontrol: news RSS + markets RSS + stock movers HTML.

    Merges all three sources into a single signal stream. Stock mover
    signals include raw_json["currency"]="INR" and raw_json["data_type"]="stock_mover".
    """

    def __init__(self, config: MoneycontrolConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._mc_config = (
            config if isinstance(config, MoneycontrolConfig) else MoneycontrolConfig()
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "moneycontrol"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._mc_config.timeout_seconds),
            headers=_HEADERS,
            follow_redirects=True,
            http2=False,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch_movers_html(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="movers_html")
        try:
            resp = await self._client.get(_MOVERS_URL)
            resp.raise_for_status()
            return _parse_movers_html(resp.text)
        except httpx.HTTPStatusError as e:
            _log.warning("moneycontrol.movers.http_error", status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("moneycontrol.movers.failed", error=str(e))
            return []

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        platform_str = Platform.MONEYCONTROL.value

        rss_results = await asyncio.gather(
            fetch_feed_entries(_NEWS_FEED, platform_str),
            fetch_feed_entries(_MARKETS_FEED, platform_str),
            return_exceptions=True,
        )

        movers = await self._fetch_movers_html() if self._mc_config.fetch_movers else []

        seen_urls: set[str] = set()
        raws: list[dict[str, Any]] = []

        for result in rss_results:
            if isinstance(result, BaseException):
                _log.warning("moneycontrol.rss.gather_error", error=str(result))
                continue
            for item in result:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    raws.append(item)

        for mover in movers:
            url = mover.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                raws.append(mover)

        valid = validate_batch(raws, platform_str)
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
            data_type = raw_json.get("data_type", "news")
            summary = raw_json.get("summary", "")
            sentiment_val: float = raw.get("sentiment") or 0.0

            posted_at = _parse_date(raw_json.get("published", "")) if data_type == "news" else None

            external_id = hashlib.sha1(  # noqa: S324
                url.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=Platform.MONEYCONTROL,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=summary[:2000] if summary else None,
                posted_at=posted_at,
            )

            platform_specific: dict[str, Any] = {
                "sentiment": sentiment_val,
                "data_type": data_type,
            }
            if data_type == "stock_mover":
                platform_specific["currency"] = raw_json.get("currency", "INR")
                platform_specific["ticker"] = raw_json.get("ticker")
                platform_specific["pct_change"] = raw_json.get("pct_change")
                platform_specific["volume"] = raw_json.get("volume")

            score_val: float = float(raw.get("score") or 0.0)

            return ProductSignal(
                platform=Platform.MONEYCONTROL,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=summary[:2000] if summary else None,
                modality=ContentModality.TEXT,
                tags=frozenset({"moneycontrol", data_type.replace("_", "-")}),
                intent=IntentType.PASSIVE,
                engagement=EngagementMetrics(
                    likes=int(score_val) if score_val and data_type == "stock_mover" else None,
                ),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=(
                        ScrapeMethod.RSS_FEED
                        if data_type == "news"
                        else ScrapeMethod.PUBLIC_API_UNOFFICIAL
                    ),
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.7 if title else 0.4,
                    source_confidence=0.75,
                ),
                content_hash=h,
                platform_specific=platform_specific,
            )
        except Exception as e:
            _log.warning("moneycontrol.parse.failed", error=str(e))
            return None


__all__ = ["MoneycontrolAdapter", "MoneycontrolConfig", "SCRAPER_VERSION", "_parse_movers_html"]
