"""GDELT 2.0 DOC API adapter — global news/event radar.

GDELT monitors news media in 100+ languages from every country on earth and
refreshes every 15 minutes. The DOC 2.0 ArtList endpoint is fully public — no
API key, no auth. This single adapter gives AEGIS true planetary coverage:
every region, country and continent.

  https://api.gdeltproject.org/api/v2/doc/doc?query=<q>&mode=ArtList&format=json

The ``sourcecountry`` field on every article is preserved into
``platform_specific["source_country"]`` so downstream geo/demand layers can
attribute momentum to a region.

PLATFORM: gdelt | TIER: T4_cultural
ToS Risk: GREEN — official public API, explicitly free for research use.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

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

_log = structlog.get_logger("aegis.scrape.gdelt")

SCRAPER_VERSION = "gdelt-0.1.0"

_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
_HEADERS = {
    "User-Agent": "AegisPulse/0.3 (market-intelligence; contact@aegis.dev)",
    "Accept": "application/json",
}
# Broad market/economy radar query used when no explicit topic is supplied.
# Surfaces emerging momentum across business, products, markets, technology.
_DEFAULT_QUERY = (
    '(market OR economy OR launch OR demand OR shortage OR surge OR trend) '
    'sourcelang:english'
)


def _clean_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def _parse_seendate(raw: str) -> datetime | None:
    # GDELT format: 20260619T103000Z
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class GdeltConfig(AdapterConfig):
    name: str = "gdelt"
    per_source_rps: float = 0.5  # GDELT asks for gentle polling
    timeout_seconds: float = 30.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


class GdeltAdapter(SourceAdapter[dict[str, Any]]):
    """GDELT global news radar — keyless, every-country DOC 2.0 ArtList feed."""

    def __init__(self, config: GdeltConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "gdelt"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "api.gdeltproject.org",
            http2=False,
            timeout=30.0,
            headers=_HEADERS,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        # Shared pooled client (ADP-7) — release the reference, never close.
        self._client = None

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        query: str | None = None,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            return
        q = (query or "").strip() or _DEFAULT_QUERY
        params = {
            "query": q,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(min(max(limit, 1), 250)),
            "sort": "DateDesc",
            "timespan": "1d",
        }
        articles: list[dict[str, Any]] = []
        try:
            await self._rate_limit()
            self._record_request_metric(method="json_api")
            resp = await self._client.get(_BASE, params=params)
            resp.raise_for_status()
            payload = resp.json()
            articles = payload.get("articles") or []
        except httpx.HTTPStatusError as e:
            _log.warning("gdelt.http_error", status=e.response.status_code)
            return
        except Exception as e:
            _log.warning("gdelt.request_error", error=str(e))
            return

        seen: set[str] = set()
        raws: list[dict[str, Any]] = []
        for art in articles:
            url = _clean_url(art.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            title = (art.get("title") or "").strip()
            raws.append({
                "title": title,
                "url": url,
                "platform": Platform.GDELT.value,
                "scraped_at": datetime.now(UTC).isoformat(),
                "author": art.get("domain") or None,
                "score": 0.0,
                "sentiment": score_text(title),
                "raw_json": {
                    "seendate": art.get("seendate") or "",
                    "domain": art.get("domain") or "",
                    "language": art.get("language") or "",
                    "source_country": art.get("sourcecountry") or "",
                    "social_image": art.get("socialimage") or "",
                },
            })

        valid = validate_batch(raws, Platform.GDELT.value)
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
            rj = raw.get("raw_json") or {}
            posted_at = _parse_seendate(rj.get("seendate") or "")
            country = (rj.get("source_country") or "").strip()
            language = (rj.get("language") or "").strip()
            domain = (rj.get("domain") or "").strip()
            sentiment_val: float = raw.get("sentiment") or 0.0

            tags: set[str] = {"gdelt", "global-news"}
            if country:
                tags.add(country.lower().replace(" ", "-"))
            if language:
                tags.add(language.lower())

            external_id = hashlib.sha1(url.encode()).hexdigest()[:24]  # noqa: S324

            h = compute_content_hash(
                platform=Platform.GDELT,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.GDELT,
                tier=SourceTier.TIER_4_CULTURAL,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                modality=ContentModality.TEXT,
                tags=frozenset(t for t in tags if t),
                intent=IntentType.ENGAGE,
                engagement=EngagementMetrics(),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.75 if (title and posted_at) else 0.50,
                    source_confidence=0.85,
                ),
                content_hash=h,
                platform_specific={
                    "source_country": country,
                    "language": language,
                    "domain": domain,
                    "sentiment": sentiment_val,
                },
            )
        except Exception as e:
            _log.warning("gdelt.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "GdeltAdapter", "GdeltConfig"]
