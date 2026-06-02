"""Snapdeal adapter — offers and deals via RSS (primary) and HTML (fallback).

Strategy:
  1. RSS feed:  https://www.snapdeal.com/rss/products/offers.xml  (more reliable)
  2. HTML page: https://www.snapdeal.com/products/offers-deals    (fallback if RSS empty)

ToS Risk: AMBER — public RSS and product pages.
Rate-limit: 0.15 req/s.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
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
from aegis.scrape.ecommerce_utils import random_ua

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.snapdeal")

SCRAPER_VERSION = "snapdeal-0.1.0"

_RSS_URL = "https://www.snapdeal.com/rss/products/offers.xml"
_HTML_URL = "https://www.snapdeal.com/products/offers-deals"

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Referer": "https://www.snapdeal.com/",
}

# XML namespaces commonly found in RSS feeds
_NS = {"media": "http://search.yahoo.com/mrss/"}

# HTML selectors for fallback
_CARD_SEL = ".product-tuple-listing, [class*='productCard'], .product-desc-rating"
_TITLE_SEL = ".product-title, p.product-title"
_PRICE_SEL = ".product-price, .payBlkBig"
_ORIG_SEL = ".product-desc-price.slash, .product-desc-price strike"


def _parse_rss_feed(xml_text: str) -> list[dict[str, Any]]:
    """Parse Snapdeal RSS XML. Returns [] on any XML/parse failure."""
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)  # noqa: S314
        channel = root.find("channel")
        if channel is None:
            return []

        scraped_at = datetime.now(UTC).isoformat()
        for item_el in channel.findall("item"):
            try:
                title = (item_el.findtext("title") or "").strip()
                url = (item_el.findtext("link") or "").strip()
                if not title or not url:
                    continue

                desc = (item_el.findtext("description") or "").strip()
                price_inr: float | None = None
                # Description often contains price like "Rs. 1,299"
                for prefix in ("Rs.", "₹", "INR"):
                    if prefix in desc:
                        try:
                            raw = desc.split(prefix, 1)[1].replace(",", "").strip().split()[0]
                            price_inr = float(raw)
                            break
                        except (ValueError, IndexError):
                            pass

                items.append({
                    "title": title,
                    "url": url,
                    "scraped_at": scraped_at,
                    "raw_json": {
                        "currency": "INR",
                        "price_inr": price_inr,
                        "description": desc[:500] if desc else None,
                        "source": "rss",
                    },
                })
            except Exception as e:
                _log.warning("snapdeal.rss_item.failed", error=str(e))

    except ET.ParseError as e:
        _log.warning("snapdeal.rss_parse.xml_error", error=str(e))
    except Exception as e:
        _log.warning("snapdeal.rss_parse.failed", error=str(e))

    return items


def _parse_snapdeal_html(html: str) -> list[dict[str, Any]]:
    """Extract deal cards from Snapdeal HTML page. Returns [] on any failure."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        _log.error("snapdeal.missing_dependency", dep="beautifulsoup4")
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
                price_inr: float | None = None
                try:
                    raw = (price_el.get_text(strip=True) if price_el else "").replace("Rs.", "").replace("₹", "").replace(",", "").strip()
                    price_inr = float(raw.split()[0]) if raw else None
                except (ValueError, IndexError):
                    pass

                link_el = card.find("a")
                href = link_el.get("href", "") if link_el else ""
                url = href if isinstance(href, str) and href.startswith("http") else f"https://www.snapdeal.com{href}"

                items.append({
                    "title": title,
                    "url": str(url) or _HTML_URL,
                    "scraped_at": scraped_at,
                    "raw_json": {
                        "currency": "INR",
                        "price_inr": price_inr,
                        "description": None,
                        "source": "html",
                    },
                })
            except Exception as e:
                _log.warning("snapdeal.html_card.failed", error=str(e))

    except Exception as e:
        _log.warning("snapdeal.parse_html.failed", error=str(e))

    return items


@dataclass(frozen=True, slots=True)
class SnapdealConfig(AdapterConfig):
    """Snapdeal adapter configuration."""

    name: str = "snapdeal"
    per_source_rps: float = 0.15
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class SnapdealAdapter(SourceAdapter[dict[str, Any]]):
    """Snapdeal offers adapter — RSS primary, HTML fallback.

    RSS is more reliable and less bot-defended than the product page.
    HTML path activates only when the RSS feed returns 0 items.
    """

    def __init__(self, config: SnapdealConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._cfg = config if isinstance(config, SnapdealConfig) else SnapdealConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "snapdeal"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._cfg.timeout_seconds),
            headers={**_HEADERS, "User-Agent": random_ua()},
            follow_redirects=True,
            http2=False,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch_rss(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="rss_feed")
        try:
            resp = await self._client.get(_RSS_URL)
            resp.raise_for_status()
            return _parse_rss_feed(resp.text)
        except httpx.HTTPStatusError as e:
            _log.warning("snapdeal.rss.http_error", status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("snapdeal.rss.failed", error=str(e))
            return []

    async def _fetch_html(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self._rate_limit()
        self._record_request_metric(method="html_fallback")
        try:
            resp = await self._client.get(_HTML_URL)
            resp.raise_for_status()
            return _parse_snapdeal_html(resp.text)
        except Exception as e:
            _log.warning("snapdeal.html.failed", error=str(e))
            return []

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        items = await self._fetch_rss()

        if not items:
            _log.info("snapdeal.rss_empty_falling_back_to_html")
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

            desc = str(raw_json.get("description") or "")

            h = compute_content_hash(
                platform=Platform.SNAPDEAL,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=desc[:2000] if desc else None,
                posted_at=None,
            )

            return ProductSignal(
                platform=Platform.SNAPDEAL,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=desc[:2000] if desc else None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({"snapdeal", "deals"}),
                intent=IntentType.PURCHASE,
                engagement=EngagementMetrics(),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.RSS_FEED if raw_json.get("source") == "rss" else ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.65 if raw_json.get("price_inr") else 0.35,
                    source_confidence=0.70,
                ),
                content_hash=h,
                platform_specific={
                    "currency": raw_json.get("currency", "INR"),
                    "price_inr": raw_json.get("price_inr"),
                    "source": raw_json.get("source", "rss"),
                },
            )
        except Exception as e:
            _log.warning("snapdeal.parse.failed", error=str(e))
            return None


__all__ = [
    "SCRAPER_VERSION",
    "SnapdealAdapter",
    "SnapdealConfig",
    "_parse_rss_feed",
    "_parse_snapdeal_html",
]
