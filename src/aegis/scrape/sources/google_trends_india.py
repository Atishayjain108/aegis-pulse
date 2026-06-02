"""Google Trends India adapter — daily trending searches for India + global cross-reference.

Uses pytrends (synchronous) bridged to asyncio via loop.run_in_executor.
Fetches India trending searches and cross-references against US top-10.

ToS Risk: GREEN — public aggregate trend data.
Rate limit: 0.2 req/s — Google blocks aggressive scrapers.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.google_trends_india")

SCRAPER_VERSION = "google-trends-india-0.1.0"


@dataclass(frozen=True, slots=True)
class GoogleTrendsIndiaConfig(AdapterConfig):
    name: str = "google_trends_india"
    per_source_rps: float = 0.2
    timeout_seconds: float = 60.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False


def _sync_scrape(limit: int) -> list[dict[str, Any]]:
    """Synchronous pytrends call — runs in executor to avoid blocking the event loop."""
    try:
        from pytrends.request import TrendReq  # type: ignore[import-untyped]
    except ImportError:
        _log.error("google_trends_india.missing_dependency", dep="pytrends")
        return []

    try:
        pt = TrendReq(hl="en-IN", tz=330, timeout=(10, 25), retries=1, backoff_factor=0.5)
        india_df = pt.trending_searches(pn="india")
        global_df = pt.trending_searches(pn="united_states")

        india_terms: list[str] = india_df.head(limit).iloc[:, 0].tolist()
        global_terms: set[str] = set(global_df.head(10).iloc[:, 0].tolist())

        scraped_at = datetime.now(UTC).isoformat()
        signals: list[dict[str, Any]] = []
        for i, term in enumerate(india_terms[:limit]):
            term_str = str(term).strip()
            if not term_str:
                continue
            signals.append({
                "title": term_str,
                "platform": Platform.GOOGLE_TRENDS_INDIA.value,
                "tier": SourceTier.TIER_3_SEARCH.value,
                "url": f"https://trends.google.com/trends/explore?q={term_str}&geo=IN",
                "score": float(limit - i),
                "sentiment": 0.0,
                "raw_json": {"also_in_global": term_str in global_terms, "rank": i + 1},
                "scraped_at": scraped_at,
                "author": None,
                "views": None,
                "likes": None,
                "comments": None,
                "shares": None,
                "saves": None,
            })
        return signals
    except Exception as e:
        _log.error("google_trends_india.pytrends_failed", error=str(e))
        return []


class GoogleTrendsIndiaAdapter(SourceAdapter[dict[str, Any]]):
    """Google Trends India: daily trending searches via pytrends in executor."""

    @property
    def name(self) -> str:
        return "google_trends_india"

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        await self._rate_limit()
        self._record_request_metric(method="pytrends")

        loop = asyncio.get_event_loop()
        raw_signals = await loop.run_in_executor(None, _sync_scrape, limit)

        for item in raw_signals:
            if self.is_cancelled:
                return
            yield item

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            title = str(raw.get("title") or "").strip()
            if not title:
                return None

            url_str = raw.get("url") or ""
            raw_json = raw.get("raw_json") or {}
            rank: int = int(raw_json.get("rank") or 1)
            also_global: bool = bool(raw_json.get("also_in_global", False))
            score = float(raw.get("score") or 0.0)

            external_id = f"gtrends_india_{title.lower().replace(' ', '_')}"

            tag = title.lower().replace(" ", "_")
            if len(tag) > 128:
                tag = tag[:128]

            h = compute_content_hash(
                platform=Platform.GOOGLE_TRENDS_INDIA,
                external_id=external_id,
                url=url_str or None,
                title=title,
                raw_text=None,
                posted_at=None,
            )

            return ProductSignal(
                platform=Platform.GOOGLE_TRENDS_INDIA,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url_str or None,  # type: ignore[arg-type]
                title=title,
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({tag, "india-trending"}),
                intent=IntentType.SEARCH,
                engagement=EngagementMetrics(views=int(score)),
                posted_at=None,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.65,
                    source_confidence=0.80,
                ),
                content_hash=h,
                platform_specific={
                    "rank": rank,
                    "also_in_global": also_global,
                    "geo": "IN",
                },
            )
        except Exception as e:
            _log.warning("google_trends_india.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "GoogleTrendsIndiaAdapter", "GoogleTrendsIndiaConfig"]
