"""Wikimedia pageview radar — global demand-intensity signal.

The Wikimedia REST pageviews API is fully public (no key) and reports the
most-viewed articles per language edition. Pageview spikes are a leading
demand indicator for *any* topic — a product, person, event or place — often
before that demand reaches commerce or news.

By polling several language editions (en, es, hi, ja, de, pt, fr, ar) AEGIS
gets a multi-continent read on what the world is paying attention to right now.

  https://wikimedia.org/api/rest_v1/metrics/pageviews/top/{project}/all-access/{Y}/{M}/{D}

Each signal carries the originating ``project`` (≈ region/language) into
``platform_specific["project"]`` and the daily ``views`` count as a real number.

PLATFORM: wikimedia | TIER: T3_search
ToS Risk: GREEN — official public API, free for any use with attribution.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from aegis.scrape.sentiment import score_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.wikimedia")

SCRAPER_VERSION = "wikimedia-0.1.0"

_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top"
_HEADERS = {
    "User-Agent": "AegisPulse/0.3 (market-intelligence; contact@aegis.dev)",
    "Accept": "application/json",
}
# Language editions ≈ regions/continents. en(global), es(LatAm/Spain),
# hi(India), ja(Japan), de(DACH), pt(Brazil), fr(France/Africa), ar(MENA).
_PROJECTS = ["en.wikipedia", "es.wikipedia", "hi.wikipedia", "ja.wikipedia",
             "de.wikipedia", "pt.wikipedia", "fr.wikipedia", "ar.wikipedia"]
# Administrative / non-topic articles to drop from the "top" list.
_NOISE = {
    "Main_Page", "Special:Search", "Wikipedia:Featured_pictures",
    "Portal:Current_events", "Special:RecentChanges",
}


@dataclass(frozen=True, slots=True)
class WikimediaConfig(AdapterConfig):
    name: str = "wikimedia"
    per_source_rps: float = 1.0
    timeout_seconds: float = 25.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class WikimediaAdapter(SourceAdapter[dict[str, Any]]):
    """Wikimedia pageview radar — most-viewed articles per language edition."""

    def __init__(self, config: WikimediaConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "wikimedia"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "wikimedia.org",
            http2=False,
            timeout=25.0,
            headers=_HEADERS,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        self._client = None

    async def _fetch_project(self, project: str, date: datetime) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        url = f"{_BASE}/{project}/all-access/{date:%Y/%m/%d}"
        try:
            await self._rate_limit()
            self._record_request_metric(method="json_api")
            resp = await self._client.get(url)
            resp.raise_for_status()
            items = resp.json().get("items") or []
            if not items:
                return []
            out: list[dict[str, Any]] = []
            for art in items[0].get("articles") or []:
                name = art.get("article") or ""
                if not name or name in _NOISE or name.startswith(("Special:", "Wikipedia:")):
                    continue
                out.append({"project": project, "article": name,
                            "views": int(art.get("views") or 0),
                            "rank": int(art.get("rank") or 0)})
            return out
        except httpx.HTTPStatusError as e:
            _log.warning("wikimedia.http_error", project=project, status=e.response.status_code)
            return []
        except Exception as e:
            _log.warning("wikimedia.request_error", project=project, error=str(e))
            return []

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        # Pageviews lag ~1 day; use yesterday (UTC) for a complete aggregate.
        date = datetime.now(UTC) - timedelta(days=1)
        batches = await asyncio.gather(*[self._fetch_project(p, date) for p in _PROJECTS])

        raws: list[dict[str, Any]] = []
        for batch in batches:
            for art in batch:
                name = art["article"]
                title = name.replace("_", " ").strip()
                lang = art["project"].split(".", 1)[0]
                url = f"https://{lang}.wikipedia.org/wiki/{name}"
                raws.append({
                    "title": title,
                    "url": url,
                    "platform": Platform.WIKIMEDIA.value,
                    "scraped_at": datetime.now(UTC).isoformat(),
                    "author": art["project"],
                    "score": float(art["views"]),
                    "views": art["views"],
                    "sentiment": score_text(title),
                    "raw_json": {
                        "project": art["project"],
                        "views": art["views"],
                        "rank": art["rank"],
                        "date": f"{date:%Y-%m-%d}",
                    },
                })

        valid = validate_batch(raws, Platform.WIKIMEDIA.value)
        # Highest-viewed first across all editions.
        valid.sort(key=lambda r: r.get("views") or 0, reverse=True)
        for item in valid[:limit]:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = (raw.get("title") or "").strip()
            url = raw.get("url") or ""
            if not url or not title:
                return None
            rj = raw.get("raw_json") or {}
            project = rj.get("project") or ""
            views = int(rj.get("views") or 0)
            rank = int(rj.get("rank") or 0)
            date_str = rj.get("date") or ""
            sentiment_val: float = raw.get("sentiment") or 0.0

            posted_at: datetime | None = None
            if date_str:
                try:
                    posted_at = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
                except ValueError:
                    posted_at = None

            lang = project.split(".", 1)[0] if project else ""
            tags = {"wikimedia", "pageviews"}
            if lang:
                tags.add(lang)

            external_id = hashlib.sha1(f"{project}:{url}".encode()).hexdigest()[:24]  # noqa: S324

            h = compute_content_hash(
                platform=Platform.WIKIMEDIA,
                external_id=external_id,
                url=url,
                title=title[:512],
                raw_text=None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.WIKIMEDIA,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512],
                modality=ContentModality.TEXT,
                tags=frozenset(t for t in tags if t),
                intent=IntentType.SEARCH,
                engagement=EngagementMetrics(views=views),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.80 if posted_at else 0.60,
                    source_confidence=0.88,
                ),
                content_hash=h,
                platform_specific={
                    "project": project,
                    "language": lang,
                    "views": views,
                    "rank": rank,
                    "sentiment": sentiment_val,
                },
            )
        except Exception as e:
            _log.warning("wikimedia.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "WikimediaAdapter", "WikimediaConfig"]
