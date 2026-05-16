"""Pinterest source adapter.

Scrapes Pinterest public search results using their unofficial JSON API.
No authentication required for public board/pin data.

Endpoint: https://www.pinterest.com/resource/SearchResource/get/
This is the same API the Pinterest web app uses.

ToS Risk: AMBER — public content; scraping tolerated in practice for
read-only research. We rate-limit aggressively and respect 429 responses.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
    Author,
    ConfidenceMetadata,
    EngagementMetrics,
    MediaRef,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)

SCRAPER_VERSION = "pinterest-0.1.0"

_PIN_BASE = "https://www.pinterest.com/pin/"
_USER_BASE = "https://www.pinterest.com/"
_SEARCH_URL = "https://www.pinterest.com/resource/SearchResource/get/"


@dataclass(frozen=True, slots=True)
class PinterestConfig(AdapterConfig):
    """Pinterest adapter config."""

    name: str = "pinterest"
    per_source_rps: float = 0.3
    timeout_seconds: float = 30.0
    max_retries: int = 3
    use_cloudflare_bypass: bool = False
    mobile_user_agent: bool = False


class PinterestAdapter(SourceAdapter[dict[str, Any]]):
    """Pinterest adapter using the unofficial search JSON API."""

    def __init__(self, config: PinterestConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._pt_config = config if isinstance(config, PinterestConfig) else PinterestConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "pinterest"

    async def setup(self, ctx: ScrapeContext) -> None:
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._pt_config.timeout_seconds),
            headers={
                "User-Agent": ua,
                "Accept": "application/json, text/javascript, */*, q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.pinterest.com/",
            },
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        query: str = "trending",
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("PinterestAdapter.setup() must run before fetch_raw()")

        bookmark: str = ""
        total_yielded = 0

        while total_yielded < limit and not self.is_cancelled:
            await self._rate_limit()
            self._record_request_metric(method="pinterest_search")

            options: dict[str, Any] = {
                "query": query,
                "scope": "pins",
                "page_size": min(25, limit - total_yielded),
                "add_vase": True,
                "auto_correction_disabled": False,
            }
            if bookmark:
                options["bookmarks"] = [bookmark]

            params = {
                "source_url": f"/search/pins/?q={query}&rs=typed",
                "data": __import__("json").dumps({"options": options, "context": {}}),
                "_": str(int(datetime.now(UTC).timestamp() * 1000)),
            }

            try:
                resp = await self._client.get(_SEARCH_URL, params=params)
                if resp.status_code == 429:
                    log.warning("pinterest.rate_limited")
                    break
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    log.warning(
                        "pinterest.fetch.blocked",
                        status=403,
                        hint="Pinterest's unofficial search API now requires an authenticated "
                        "session (returns 403). The pinterest adapter needs a valid browser "
                        "session cookie to work. No signals will be collected.",
                    )
                else:
                    log.warning("pinterest.fetch.http_error", status=e.response.status_code)
                break
            except httpx.RequestError as e:
                log.warning("pinterest.fetch.request_error", error=str(e))
                break

            resource_resp = data.get("resource_response") or {}
            result_data = resource_resp.get("data") or {}
            results: list[dict[str, Any]] = result_data.get("results") or []

            if not results:
                break

            for pin in results:
                if total_yielded >= limit or self.is_cancelled:
                    return
                yield pin
                total_yielded += 1

            # Pagination bookmark
            bookmarks = result_data.get("bookmark")
            if not bookmarks or bookmarks in ("-end-", bookmark):
                break
            bookmark = bookmarks

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            pin_id: str = str(raw.get("id") or "")
            if not pin_id:
                return None

            description: str = str(raw.get("description") or raw.get("grid_title") or "")
            title: str = str(raw.get("title") or "")
            save_count: int | None = _int_or_none(raw.get("aggregated_pin_data", {}).get("saves"))
            comment_count: int | None = _int_or_none(raw.get("comment_count"))

            created_at_str: str | None = raw.get("created_at")
            posted_at: datetime | None = None
            if created_at_str:
                try:
                    posted_at = datetime.strptime(created_at_str, "%a, %d %b %Y %H:%M:%S %z")
                except ValueError:
                    with contextlib.suppress(ValueError):
                        posted_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))

            url = f"{_PIN_BASE}{pin_id}/"

            # Author
            pinner = raw.get("pinner") or {}
            author: Author | None = None
            if pinner:
                uid = str(pinner.get("id") or "")
                handle = str(pinner.get("username") or "")
                if uid or handle:
                    author = Author(
                        platform_user_id=uid or handle,
                        handle=handle or None,
                        display_name=pinner.get("full_name") or None,
                        follower_count=_int_or_none(pinner.get("follower_count")),
                        profile_url=f"{_USER_BASE}{handle}/",  # type: ignore[arg-type]
                    )

            # Image
            images = raw.get("images") or {}
            orig = images.get("orig") or {}
            media: tuple[MediaRef, ...] = ()
            if orig.get("url"):
                media = (
                    MediaRef(
                        url=orig["url"],  # type: ignore[arg-type]
                        modality=ContentModality.IMAGE,
                        width_px=_int_or_none(orig.get("width")),
                        height_px=_int_or_none(orig.get("height")),
                    ),
                )

            is_video = bool(raw.get("videos"))
            modality = (
                ContentModality.VIDEO
                if is_video
                else (ContentModality.MULTIMODAL if media else ContentModality.IMAGE)
            )

            h = compute_content_hash(
                platform=Platform.PINTEREST,
                external_id=pin_id,
                url=url,
                title=title or description or None,
                raw_text=description or None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.PINTEREST,
                tier=SourceTier.TIER_1_INTENT,
                external_id=pin_id,
                url=url,  # type: ignore[arg-type]
                title=title or None,
                raw_text=description or None,
                modality=modality,
                tags=frozenset(),
                intent=IntentType.SAVE,
                author=author,
                engagement=EngagementMetrics(
                    saves=save_count,
                    comments=comment_count,
                ),
                media=media,
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.75 if title else 0.55,
                    source_confidence=0.75,
                ),
                content_hash=h,
                platform_specific={
                    "pin_id": pin_id,
                    "save_count": save_count,
                    "board_id": raw.get("board", {}).get("id"),
                    "is_video": is_video,
                },
            )
        except Exception as e:
            log.warning("pinterest.parse.failed", error=str(e))
            return None


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


__all__ = ["PinterestAdapter", "PinterestConfig", "SCRAPER_VERSION"]
