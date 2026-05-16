"""Google Trends source adapter.

Uses the pytrends library (unofficial Google Trends API wrapper).
No authentication required. Returns interest-over-time data (0–100 index)
for up to 5 keywords per request.

Since pytrends is synchronous we bridge every call via ``asyncio.to_thread``.

ToS Risk: GREEN for public aggregate trend data.
Rate-limit: Google blocks aggressive scrapers; we stay at 0.2 req/s.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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

SCRAPER_VERSION = "google-trends-0.1.0"


@dataclass(frozen=True, slots=True)
class GoogleTrendsConfig(AdapterConfig):
    """Google Trends adapter config."""

    name: str = "google-trends"
    per_source_rps: float = 0.2  # conservative — Google rate-limits aggressively
    timeout_seconds: float = 60.0
    max_retries: int = 2
    use_cloudflare_bypass: bool = False

    geo: str = ""
    """Country/region code, or '' for worldwide. E.g. 'US', 'IN', 'GB'."""

    timeframe: str = "now 7-d"
    """Time range: 'now 1-H' | 'now 4-H' | 'now 1-d' | 'now 7-d' |
    'today 1-m' | 'today 3-m' | 'today 12-m' | 'today 5-y'."""

    gprop: str = ""
    """Property: '' (web) | 'news' | 'images' | 'youtube' | 'froogle'."""

    cat: int = 0
    """Category filter; 0 = all."""


class GoogleTrendsAdapter(SourceAdapter[dict[str, Any]]):
    """Google Trends adapter. Uses pytrends in a thread pool."""

    parse_is_blocking: bool = True  # parse() touches pandas objects

    def __init__(self, config: GoogleTrendsConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._gt_config = config if isinstance(config, GoogleTrendsConfig) else GoogleTrendsConfig()
        self._pytrends: Any = None

    @property
    def name(self) -> str:
        return "google-trends"

    async def setup(self, ctx: ScrapeContext) -> None:
        try:
            from pytrends.request import TrendReq  # type: ignore[import-untyped]
        except ImportError as e:
            raise RuntimeError(
                "GoogleTrendsAdapter requires pytrends — " "install with `uv sync --extra scrape`"
            ) from e

        def _build() -> Any:
            return TrendReq(
                hl="en-US",
                tz=0,
                timeout=(10, 25),
                retries=1,
                backoff_factor=0.5,
            )

        self._pytrends = await asyncio.to_thread(_build)

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        query: str | None = None,
        queries: list[str] | None = None,
        limit: int = 100,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._pytrends is None:
            raise RuntimeError("GoogleTrendsAdapter.setup() must run before fetch_raw()")

        kw_list: list[str] = list(queries or [])
        if query and query not in kw_list:
            kw_list.append(query)
        if not kw_list:
            kw_list = ["trending"]

        cfg = self._gt_config
        # pytrends accepts at most 5 keywords per payload.
        chunks = [kw_list[i : i + 5] for i in range(0, len(kw_list), 5)]
        total_yielded = 0

        for chunk in chunks:
            if total_yielded >= limit or self.is_cancelled:
                return

            await self._rate_limit()
            self._record_request_metric(method="pytrends")

            def _fetch(kws: list[str] = chunk) -> dict[str, Any]:
                pt = self._pytrends
                pt.build_payload(
                    kws,
                    cat=cfg.cat,
                    timeframe=cfg.timeframe,
                    geo=cfg.geo,
                    gprop=cfg.gprop,
                )
                return {
                    "interest_over_time": pt.interest_over_time(),
                    "keywords": kws,
                }

            try:
                result = await asyncio.to_thread(_fetch)
            except Exception as e:
                log.warning(
                    "google_trends.fetch.failed",
                    error=str(e),
                    keywords=chunk,
                )
                continue

            iot = result.get("interest_over_time")
            if iot is None or (hasattr(iot, "empty") and iot.empty):
                continue

            try:
                records = iot.reset_index().to_dict("records")
            except Exception as e:
                log.warning("google_trends.df.failed", error=str(e))
                continue

            for record in records:
                for kw in chunk:
                    if total_yielded >= limit or self.is_cancelled:
                        return
                    if kw not in record:
                        continue
                    yield {
                        "keyword": kw,
                        "value": int(record.get(kw, 0)),
                        "date": record.get("date"),
                        "is_partial": bool(record.get("isPartial", False)),
                        "geo": cfg.geo or "WW",
                        "timeframe": cfg.timeframe,
                    }
                    total_yielded += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            keyword: str = str(raw.get("keyword") or "")
            if not keyword:
                return None

            value: int = int(raw.get("value") or 0)
            date_raw: Any = raw.get("date")
            geo: str = str(raw.get("geo") or "WW")
            timeframe: str = str(raw.get("timeframe") or "")

            posted_at: datetime | None = None
            if date_raw is not None:
                with contextlib.suppress(Exception):
                    if hasattr(date_raw, "to_pydatetime"):
                        posted_at = date_raw.to_pydatetime().replace(tzinfo=UTC)
                    elif isinstance(date_raw, datetime):
                        posted_at = date_raw.replace(tzinfo=UTC)
                    elif isinstance(date_raw, str):
                        posted_at = datetime.fromisoformat(date_raw).replace(tzinfo=UTC)

            date_str = posted_at.date().isoformat() if posted_at else "unknown"
            external_id = f"gtrends_{keyword}_{geo}_{date_str}"

            tag = keyword.lower().replace(" ", "_")
            if len(tag) > 128:
                tag = tag[:128]

            h = compute_content_hash(
                platform=Platform.GOOGLE_TRENDS,
                external_id=external_id,
                url=None,
                title=keyword,
                raw_text=None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.GOOGLE_TRENDS,
                tier=SourceTier.TIER_3_SEARCH,
                external_id=external_id,
                url=None,
                title=keyword,
                raw_text=None,
                modality=ContentModality.STRUCTURED,
                tags=frozenset({tag}),
                intent=IntentType.SEARCH,
                engagement=EngagementMetrics(views=value),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.7,
                    source_confidence=0.80,
                ),
                content_hash=h,
                platform_specific={
                    "trend_value": value,
                    "geo": geo,
                    "timeframe": timeframe,
                    "is_partial": raw.get("is_partial", False),
                },
            )
        except Exception as e:
            log.warning("google_trends.parse.failed", error=str(e))
            return None


__all__ = ["GoogleTrendsAdapter", "GoogleTrendsConfig", "SCRAPER_VERSION"]
