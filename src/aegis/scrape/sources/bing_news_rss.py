"""Bing News RSS adapter — free, no API key required.

Fetches news articles from Microsoft Bing News RSS search feeds:
  https://www.bing.com/news/search?q={query}&format=RSS&count=50&mkt=en-US

Bing News aggregates articles from thousands of publishers worldwide and
frequently surfaces stories that Google News does not rank highly, making
it a high-value complementary source for topic intelligence.

ToS Risk: AMBER — publicly accessible Microsoft Bing News feed. We use
conservative rate limiting (1 req / 5 s) and a neutral browser User-Agent
to stay within reasonable-use bounds.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

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

SCRAPER_VERSION = "bing-news-rss-0.1.0"

_BING_RSS_BASE = "https://www.bing.com/news/search"
_BING_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bing.com/",
}


@dataclass(frozen=True, slots=True)
class BingNewsRSSConfig(AdapterConfig):
    """Config for the Bing News RSS adapter."""

    name: str = "bing-news-rss"
    per_source_rps: float = 0.20  # 1 req / 5 s — conservative
    timeout_seconds: float = 20.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False

    language: str = "en"
    market: str = "en-US"
    count: int = 50


class BingNewsRSSAdapter(SourceAdapter[dict[str, Any]]):
    """Adapter that searches Bing News RSS for any query string.

    Each call to ``run(query=..., limit=N)`` fires one RSS request returning
    up to 50 results covering a wide range of publishers. For topic-based
    scraping you would call this multiple times with different query expansions.

    Example::

        adapter = BingNewsRSSAdapter(BingNewsRSSConfig())
        async with adapter:
            async for signal in adapter.run(query="NVIDIA H100 market 2026", limit=30):
                print(signal.title)
    """

    def __init__(self, config: BingNewsRSSConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._bn_config = (
            config if isinstance(config, BingNewsRSSConfig) else BingNewsRSSConfig()
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "bing-news-rss"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._bn_config.timeout_seconds),
            headers=_BING_HEADERS,
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
        query: str = "",
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("BingNewsRSSAdapter.setup() must run before fetch_raw()")
        if not query:
            log.warning("bing_news_rss.empty_query")
            return

        cfg = self._bn_config
        url = (
            f"{_BING_RSS_BASE}"
            f"?q={quote_plus(query)}"
            f"&format=RSS"
            f"&count={min(cfg.count, 50)}"
            f"&mkt={cfg.market}"
        )

        await self._rate_limit()
        self._record_request_metric(method="rss_feed")

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            xml_bytes = resp.content
        except httpx.HTTPStatusError as e:
            log.warning("bing_news_rss.http_error", status=e.response.status_code, query=query)
            return
        except httpx.RequestError as e:
            log.warning("bing_news_rss.request_error", error=str(e), query=query)
            return

        try:
            root = ET.fromstring(xml_bytes)  # noqa: S314
        except ET.ParseError as e:
            log.warning("bing_news_rss.xml_parse_error", error=str(e), query=query)
            return

        # Standard RSS: <rss><channel><item>…</item></channel></rss>
        channel = root.find("channel")
        if channel is None:
            # Bing sometimes returns Atom-like feeds; attempt top-level items
            items = root.findall("item")
        else:
            items = channel.findall("item")

        yielded = 0
        for item in items:
            if yielded >= limit or self.is_cancelled:
                return
            raw: dict[str, Any] = {
                "title": _text(item, "title"),
                "link": _text(item, "link"),
                "description": _text(item, "description"),
                "pubDate": _text(item, "pubDate"),
                "source": _text(item, "source"),
                "guid": _text(item, "guid"),
                "_query": query,
            }
            if raw["title"] or raw["link"]:
                yield raw
                yielded += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            import re

            title = (raw.get("title") or "").strip()
            link = (raw.get("link") or "").strip()
            description = (raw.get("description") or "").strip()
            pub_date_str = raw.get("pubDate") or ""
            source_name = raw.get("source") or ""
            guid = raw.get("guid") or link or title
            query = raw.get("_query") or ""

            if not title and not link:
                return None

            canonical_url = link or f"https://www.bing.com/news/search?q={quote_plus(query)}"

            posted_at: datetime | None = None
            if pub_date_str:
                try:
                    _dt: datetime = parsedate_to_datetime(pub_date_str)  # type: ignore[assignment]
                    posted_at = _dt if _dt.tzinfo is not None else _dt.replace(tzinfo=UTC)
                except Exception as exc:
                    log.debug("bing_news.date_parse_failed", pub_date=pub_date_str, error=str(exc))

            external_id = hashlib.sha1(  # noqa: S324
                guid.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            clean_desc: str | None = None
            if description:
                clean_desc = re.sub(r"<[^>]+>", " ", description).strip()
                clean_desc = re.sub(r"\s+", " ", clean_desc)[:2000] or None

            h = compute_content_hash(
                platform=Platform.BING_NEWS,
                external_id=external_id,
                url=canonical_url,
                title=title[:512] if title else None,
                raw_text=clean_desc,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.BING_NEWS,
                tier=SourceTier.TIER_4_CULTURAL,
                external_id=external_id,
                url=canonical_url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=clean_desc,
                modality=ContentModality.TEXT,
                tags=frozenset({"news", "bing_news"}),
                intent=IntentType.PASSIVE,
                engagement=EngagementMetrics(),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.RSS_FEED,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.70 if title and posted_at else 0.45,
                    source_confidence=0.75,
                ),
                content_hash=h,
                platform_specific={
                    "source_name": source_name,
                    "query": query,
                    "guid": guid[:200],
                },
            )
        except Exception as e:
            log.warning("bing_news_rss.parse.failed", error=str(e))
            return None


def _text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    if child is None:
        return ""
    return (child.text or "").strip()


__all__ = ["SCRAPER_VERSION", "BingNewsRSSAdapter", "BingNewsRSSConfig"]
