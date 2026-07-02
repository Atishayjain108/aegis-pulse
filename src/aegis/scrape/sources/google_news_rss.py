"""Google News RSS adapter — free, no API key required.

Fetches news articles from Google News RSS search feeds:
  https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en

Each query returns up to 100 recent news articles from Google's index,
covering thousands of publishers worldwide. This is the most comprehensive
free news source available without authentication.

ToS Risk: AMBER — publicly accessible but not officially documented as a
stable API. We use conservative rate limiting (1 req / 4 s) and a neutral
User-Agent to stay well within reasonable-use bounds.
"""

from __future__ import annotations

import contextlib
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

import httpx
from defusedxml.ElementTree import fromstring as _safe_fromstring  # audit P2-7

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
from aegis.scrape.http_client import get_or_create_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)

SCRAPER_VERSION = "google-news-rss-0.1.0"

_GNEWS_RSS_BASE = "https://news.google.com/rss/search"
_GNEWS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AegisPulse/0.3; +market-research)",
    "Accept": "application/rss+xml, application/xml, text/xml",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True, slots=True)
class GoogleNewsRSSConfig(AdapterConfig):
    """Config for the Google News RSS adapter."""

    name: str = "google-news-rss"
    per_source_rps: float = 0.25  # 1 req / 4 s — conservative to avoid soft-blocking
    timeout_seconds: float = 20.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False

    language: str = "en"
    country: str = "US"


class GoogleNewsRSSAdapter(SourceAdapter[dict[str, Any]]):
    """Adapter that searches Google News RSS for any query string.

    Each call to ``fetch_raw(query=..., limit=N)`` fires one RSS request
    returning up to 100 results. For topic-based scraping you would call
    this multiple times with different query expansions.

    Example::

        adapter = GoogleNewsRSSAdapter(GoogleNewsRSSConfig())
        async with adapter:
            async for signal in adapter.run(query="AI chips market 2025", limit=40):
                print(signal.title)
    """

    def __init__(self, config: GoogleNewsRSSConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._gn_config = (
            config if isinstance(config, GoogleNewsRSSConfig) else GoogleNewsRSSConfig()
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "google-news-rss"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "news.google.com",
            http2=False,
            timeout=self._gn_config.timeout_seconds,
            headers=_GNEWS_HEADERS,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        # Shared pooled client (PASS5-5B) — release the reference, never close.
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
            raise RuntimeError("GoogleNewsRSSAdapter.setup() must run before fetch_raw()")
        if not query:
            log.warning("google_news_rss.empty_query")
            return

        cfg = self._gn_config
        ceid = f"{cfg.country}:{cfg.language.upper()}"
        url = (
            f"{_GNEWS_RSS_BASE}"
            f"?q={quote_plus(query)}"
            f"&hl={cfg.language}-{cfg.country}"
            f"&gl={cfg.country}"
            f"&ceid={ceid}"
        )

        await self._rate_limit()
        self._record_request_metric(method="rss_feed")

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            xml_bytes = resp.content
        except httpx.HTTPStatusError as e:
            log.warning("google_news_rss.http_error", status=e.response.status_code, query=query)
            return
        except httpx.RequestError as e:
            log.warning("google_news_rss.request_error", error=str(e), query=query)
            return

        try:
            root = _safe_fromstring(xml_bytes)
        except ET.ParseError as e:
            log.warning("google_news_rss.xml_parse_error", error=str(e), query=query)
            return

        # RSS structure: <rss><channel><item>...</item></channel></rss>
        channel = root.find("channel")
        if channel is None:
            return

        yielded = 0
        for item in channel.findall("item"):
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
            title = (raw.get("title") or "").strip()
            link = (raw.get("link") or "").strip()
            description = (raw.get("description") or "").strip()
            pub_date_str = raw.get("pubDate") or ""
            source_name = raw.get("source") or ""
            guid = raw.get("guid") or link or title
            query = raw.get("_query") or ""

            if not title and not link:
                return None

            # Strip Google's redirect wrapper from links
            # Google News wraps: https://news.google.com/rss/articles/...?oc=5
            # The actual article URL is inside the <link> tag as plain text.
            canonical_url = link or f"https://news.google.com/search?q={quote_plus(query)}"

            posted_at: datetime | None = None
            if pub_date_str:
                with contextlib.suppress(Exception):
                    posted_at = parsedate_to_datetime(pub_date_str)
                    if posted_at.tzinfo is None:
                        posted_at = posted_at.replace(tzinfo=UTC)

            # Stable external_id: hash of the guid so it's consistent across re-scrapes
            external_id = hashlib.sha1(  # noqa: S324 — non-security use
                guid.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            # Clean description (often contains HTML snippets from Google)
            clean_desc: str | None = None
            if description:
                # Strip simple HTML tags from the description snippet
                import re
                clean_desc = re.sub(r"<[^>]+>", " ", description).strip()
                clean_desc = re.sub(r"\s+", " ", clean_desc)[:2000] or None

            h = compute_content_hash(
                platform=Platform.GOOGLE_NEWS,
                external_id=external_id,
                url=canonical_url,
                title=title[:512] if title else None,
                raw_text=clean_desc,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.GOOGLE_NEWS,
                tier=SourceTier.TIER_4_CULTURAL,
                external_id=external_id,
                url=canonical_url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=clean_desc,
                modality=ContentModality.TEXT,
                tags=frozenset({"news", "google_news"}),
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
                    source_confidence=0.80,
                ),
                content_hash=h,
                platform_specific={
                    "source_name": source_name,
                    "query": query,
                    "guid": guid[:200],
                },
            )
        except Exception as e:
            log.warning("google_news_rss.parse.failed", error=str(e))
            return None


def _text(element: ET.Element, tag: str) -> str:
    """Safely extract text from an XML child element."""
    child = element.find(tag)
    if child is None:
        return ""
    return (child.text or "").strip()


__all__ = ["SCRAPER_VERSION", "GoogleNewsRSSAdapter", "GoogleNewsRSSConfig"]
