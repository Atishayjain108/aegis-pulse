"""Google Trends global radar — daily trending searches across many regions.

Generalises the India-only ``google_trends_india`` adapter to *every* region.
Uses Google Trends' keyless daily-trends RSS endpoint, one request per geo:

  https://trends.google.com/trending/rss?geo=<CC>

This returns the actual trending search terms for that country right now, with
an approximate traffic volume (a real number) and the headline driving each
trend. Polling a basket of countries across all continents gives AEGIS a live,
geo-attributed read on what humans are actively searching for anywhere on earth.

Override the basket with ``AEGIS_SCRAPE_TRENDS_GEOS`` (comma-separated ISO-3166
country codes). The default basket spans 6 continents.

PLATFORM: google_trends_global | TIER: T3_search
ToS Risk: GREEN — public aggregate trend data, no auth.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
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

_log = structlog.get_logger("aegis.scrape.google_trends_global")

SCRAPER_VERSION = "google-trends-global-0.1.0"

_BASE = "https://trends.google.com/trending/rss"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AegisPulse/0.3; +contact@aegis.dev)",
    "Accept": "application/rss+xml, application/xml",
}
# Default basket — one country per major market, spanning all 6 inhabited
# continents: N.America, S.America, Europe, Africa, Asia, Oceania.
_DEFAULT_GEOS = ["US", "GB", "IN", "BR", "JP", "DE", "NG", "AU", "ZA", "ID"]

_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_TRAFFIC_RE = re.compile(r"<ht:approx_traffic>(.*?)</ht:approx_traffic>", re.DOTALL)
_PUBDATE_RE = re.compile(r"<pubDate>(.*?)</pubDate>", re.DOTALL)
_NEWS_RE = re.compile(r"<ht:news_item_title>(.*?)</ht:news_item_title>", re.DOTALL)


def _geos() -> list[str]:
    raw = os.getenv("AEGIS_SCRAPE_TRENDS_GEOS", "").strip()
    if raw:
        return [g.strip().upper() for g in raw.split(",") if g.strip()]
    return _DEFAULT_GEOS


def _unescape(text: str) -> str:
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.DOTALL)
    return (text.replace("&amp;", "&").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"').strip())


def _approx_to_int(raw: str) -> int:
    # "500+" / "2K+" / "1M+" → integer floor
    raw = raw.strip().rstrip("+").strip()
    mult = 1
    if raw.endswith(("K", "k")):
        mult, raw = 1_000, raw[:-1]
    elif raw.endswith(("M", "m")):
        mult, raw = 1_000_000, raw[:-1]
    try:
        return int(float(raw) * mult)
    except ValueError:
        return 0


@dataclass(frozen=True, slots=True)
class GoogleTrendsGlobalConfig(AdapterConfig):
    name: str = "google_trends_global"
    per_source_rps: float = 0.5
    timeout_seconds: float = 25.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class GoogleTrendsGlobalAdapter(SourceAdapter[dict[str, Any]]):
    """Google Trends global radar — daily trending RSS across a geo basket."""

    def __init__(self, config: GoogleTrendsGlobalConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "google_trends_global"

    async def setup(self, ctx: ScrapeContext) -> None:
        # Dedicated client (not the shared pool): the pooled client's redirect
        # event-hooks break on trends.google.com's RSS redirect chain.
        self._client = httpx.AsyncClient(
            http2=False,
            timeout=25.0,
            headers=_HEADERS,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
        self._client = None

    async def _fetch_geo(self, geo: str) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        try:
            await self._rate_limit()
            self._record_request_metric(method="rss")
            resp = await self._client.get(_BASE, params={"geo": geo})
            resp.raise_for_status()
            body = resp.text
        except httpx.HTTPStatusError as e:
            _log.warning("trends_global.http_error", geo=geo, status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("trends_global.request_error", geo=geo, error=str(e))
            return []

        out: list[dict[str, Any]] = []
        for block in _ITEM_RE.findall(body):
            tm = _TITLE_RE.search(block)
            if not tm:
                continue
            term = _unescape(tm.group(1))
            if not term:
                continue
            traffic_m = _TRAFFIC_RE.search(block)
            traffic = _approx_to_int(traffic_m.group(1)) if traffic_m else 0
            news_m = _NEWS_RE.search(block)
            headline = _unescape(news_m.group(1)) if news_m else ""
            pub_m = _PUBDATE_RE.search(block)
            posted = ""
            if pub_m:
                with contextlib.suppress(Exception):
                    posted = parsedate_to_datetime(pub_m.group(1).strip()).astimezone(UTC).isoformat()
            out.append({"geo": geo, "term": term, "traffic": traffic,
                        "headline": headline, "posted": posted})
        return out

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        # Sequential (not gathered) — Google throttles parallel trend requests.
        raws: list[dict[str, Any]] = []
        for geo in _geos():
            for it in await self._fetch_geo(geo):
                term = it["term"]
                geo_c = it["geo"]
                raws.append({
                    "title": term,
                    "url": f"https://trends.google.com/trends/explore?q={term.replace(' ', '+')}&geo={geo_c}",
                    "platform": Platform.GOOGLE_TRENDS_GLOBAL.value,
                    "scraped_at": datetime.now(UTC).isoformat(),
                    "author": f"google-trends-{geo_c}",
                    "score": float(it["traffic"]),
                    "views": it["traffic"],
                    "sentiment": score_text(f"{term} {it['headline']}"),
                    "raw_json": {
                        "geo": geo_c,
                        "traffic": it["traffic"],
                        "headline": it["headline"],
                        "posted": it["posted"],
                    },
                })

        valid = validate_batch(raws, Platform.GOOGLE_TRENDS_GLOBAL.value)
        valid.sort(key=lambda r: r.get("views") or 0, reverse=True)
        for item in valid[:limit]:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = str(raw.get("title") or "").strip()
            url = raw.get("url") or ""
            if not title or not url:
                return None
            rj = raw.get("raw_json") or {}
            geo = str(rj.get("geo") or "")
            traffic = int(rj.get("traffic") or 0)
            headline = str(rj.get("headline") or "")
            posted_str = str(rj.get("posted") or "")
            sentiment_val: float = raw.get("sentiment") or 0.0

            posted_at: datetime | None = None
            if posted_str:
                with contextlib.suppress(ValueError):
                    posted_at = datetime.fromisoformat(posted_str)

            tag = re.sub(r"[^a-z0-9_\-]", "-", title.lower().replace(" ", "-"))[:128].strip("-")
            tags = {"google-trends", "trending"}
            if tag:
                tags.add(tag)
            if geo:
                tags.add(geo.lower())

            external_id = hashlib.sha1(f"{geo}:{title.lower()}".encode()).hexdigest()[:24]  # noqa: S324

            h = compute_content_hash(
                platform=Platform.GOOGLE_TRENDS_GLOBAL,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=headline[:2000] if headline else None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.GOOGLE_TRENDS_GLOBAL,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                raw_text=headline[:2000] if headline else None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset(t for t in tags if t),
                intent=IntentType.SEARCH,
                engagement=EngagementMetrics(views=traffic),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.70 if posted_at else 0.55,
                    source_confidence=0.80,
                ),
                content_hash=h,
                platform_specific={
                    "geo": geo,
                    "approx_traffic": traffic,
                    "headline": headline,
                    "sentiment": sentiment_val,
                },
            )
        except Exception as e:
            _log.warning("trends_global.parse.failed", error=str(e))
            return None


__all__ = [
    "SCRAPER_VERSION",
    "GoogleTrendsGlobalAdapter",
    "GoogleTrendsGlobalConfig",
]
