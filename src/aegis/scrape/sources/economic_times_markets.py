"""Economic Times Markets adapter — Indian financial news via RSS.

Three RSS feeds:
  - Top news: https://economictimes.indiatimes.com/rssfeedsdefault.cms
  - Markets:  https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms
  - Startups: https://economictimes.indiatimes.com/small-biz/startups/newsbuzz

Each item is tagged with raw_json["source"] = "economic_times" and
raw_json["section"] based on which feed it came from.

ToS Risk: AMBER — public RSS feeds; no login required.
PLATFORM: economic_times | TIER: T3_search
"""
from __future__ import annotations

import asyncio
import hashlib
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
from aegis.scrape.schema_guard import validate_batch
from aegis.scrape.sources._rss_base import RSSAdapterConfig, _parse_date, fetch_feed_entries

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.economic_times")

SCRAPER_VERSION = "economic-times-rss-0.1.0"

# (feed_url, section_label) pairs — order determines priority in dedup
_FEEDS_WITH_SECTIONS: tuple[tuple[str, str], ...] = (
    ("https://economictimes.indiatimes.com/rssfeedsdefault.cms", "top_news"),
    ("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "markets"),
    ("https://economictimes.indiatimes.com/small-biz/startups/newsbuzz", "startups"),
)


class EconomicTimesMarketsAdapter(SourceAdapter[dict[str, Any]]):
    """Economic Times Markets RSS adapter with per-feed section tagging.

    Fetches three RSS feeds concurrently and injects raw_json["section"] so
    downstream features can weight market vs. startup signals differently.
    """

    def __init__(self, config: RSSAdapterConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)

    @property
    def name(self) -> str:
        return "economic_times"

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        platform_str = Platform.ECONOMIC_TIMES.value

        results = await asyncio.gather(
            *[fetch_feed_entries(url, platform_str) for url, _ in _FEEDS_WITH_SECTIONS],
            return_exceptions=True,
        )

        seen_urls: set[str] = set()
        raws: list[dict[str, Any]] = []

        for (_, section), result in zip(_FEEDS_WITH_SECTIONS, results, strict=False):
            if isinstance(result, BaseException):
                _log.warning("economic_times.feed.gather_error", section=section, error=str(result))
                continue
            for item in result:
                url = item.get("url", "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                # Inject section and source into raw_json
                raw_json: dict[str, Any] = dict(item.get("raw_json") or {})
                raw_json["source"] = "economic_times"
                raw_json["section"] = section
                raws.append({**item, "raw_json": raw_json})

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
            summary: str = raw_json.get("summary") or ""
            published_str: str = raw_json.get("published") or ""
            tags_raw: list[str] = raw_json.get("tags") or []
            section: str = raw_json.get("section") or "top_news"
            sentiment_val: float = raw.get("sentiment") or 0.0

            posted_at = _parse_date(published_str)

            tag_set: frozenset[str] = (
                frozenset(t for t in tags_raw if t)
                | {"economic-times", section.replace("_", "-")}
            )

            external_id = hashlib.sha1(  # noqa: S324
                url.encode("utf-8", errors="replace")
            ).hexdigest()[:24]

            h = compute_content_hash(
                platform=Platform.ECONOMIC_TIMES,
                external_id=external_id,
                url=url,
                title=title[:512] if title else None,
                raw_text=summary[:2000] if summary else None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.ECONOMIC_TIMES,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=summary[:2000] if summary else None,
                modality=ContentModality.TEXT,
                tags=tag_set,
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
                    completeness=0.7 if (title and posted_at) else 0.45,
                    source_confidence=0.80,
                ),
                content_hash=h,
                platform_specific={
                    "source": "economic_times",
                    "section": section,
                    "sentiment": sentiment_val,
                },
            )
        except Exception as e:
            _log.warning("economic_times.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "EconomicTimesMarketsAdapter"]
