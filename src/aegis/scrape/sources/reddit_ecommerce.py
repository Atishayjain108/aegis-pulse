"""Reddit E-commerce adapter — r/ecommerce, r/shopify, r/IndiaStartups, and related subs.

Same fetch pattern as reddit_finance.py — public JSON API, gzip-only, http2=False.

ToS Risk: GREEN — publicly documented JSON endpoint.
Rate limit: 1 req/3 s.
"""
from __future__ import annotations

import asyncio
import contextlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
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
    Author,
    ConfidenceMetadata,
    EngagementMetrics,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter
from aegis.scrape.http_client import get_or_create_client
from aegis.scrape.sentiment import score_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = structlog.get_logger("aegis.scrape.reddit_ecommerce")

SCRAPER_VERSION = "reddit-ecommerce-0.1.0"

_REDDIT_BASE = "https://www.reddit.com"
_USER_AGENT = "AEGIS-Pulse/1.0 (market-intelligence-bot)"
_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}

_ATOM_NS = "http://www.w3.org/2005/Atom"


def _atom_entry_to_post(entry: ET.Element, sub: str) -> dict[str, Any]:
    ns = {"a": _ATOM_NS}

    def _t(tag: str) -> str:
        el = entry.find(tag, ns)
        return el.text or "" if el is not None else ""

    raw_id = _t("a:id")
    post_id = raw_id.split(",")[0].replace("t3_", "") if raw_id else ""
    link_el = entry.find("a:link", ns)
    href = link_el.get("href", "") if link_el is not None else ""
    author_el = entry.find("a:author/a:name", ns)
    author_name = author_el.text or "" if author_el is not None else ""
    cat_el = entry.find("a:category", ns)
    subreddit_name = cat_el.get("term", sub) if cat_el is not None else sub
    updated = _t("a:updated")
    created_utc: float | None = None
    if updated:
        with contextlib.suppress(ValueError):
            created_utc = datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
    return {
        "id": post_id or raw_id,
        "title": _t("a:title"),
        "selftext": "",
        "subreddit": subreddit_name,
        "author": author_name,
        "score": 0,
        "num_comments": 0,
        "url": href,
        "permalink": href.replace("https://www.reddit.com", "") if href else None,
        "created_utc": created_utc,
        "is_self": False,
        "over_18": False,
        "_subreddit_override": sub,
    }


# Trimmed to a focused 5-subreddit set so the Reddit JSON API rate limit
# (~1 req/3s anonymous) does not blow the 20s per-adapter harvest budget — 11
# subreddits serialized previously timed out and returned nothing.
_DEFAULT_SUBREDDITS: tuple[str, ...] = (
    "ecommerce",
    "dropshipping",
    "FulfillmentByAmazon",
    "Entrepreneur",
    "IndiaStartups",
)


@dataclass(frozen=True, slots=True)
class RedditEcommerceConfig(AdapterConfig):
    name: str = "reddit_ecommerce"
    per_source_rps: float = 0.5
    timeout_seconds: float = 20.0
    max_retries: int = 3
    use_cloudflare_bypass: bool = False
    subreddits: tuple[str, ...] = field(default=_DEFAULT_SUBREDDITS)
    listing: str = "hot"
    posts_per_sub: int = 25


class RedditEcommerceAdapter(SourceAdapter[dict[str, Any]]):
    """Reddit e-commerce subreddits via public JSON API."""

    def __init__(self, config: RedditEcommerceConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._re_config = (
            config if isinstance(config, RedditEcommerceConfig) else RedditEcommerceConfig()
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "reddit_ecommerce"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "www.reddit.com",
            http2=False,
            timeout=self._re_config.timeout_seconds,
            headers=_HEADERS,
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        # Shared pooled client (PASS5-5B / ADP-7) — release the reference, never close.
        self._client = None

    async def _fetch_subreddit(self, sub: str, per_sub: int) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        # Reddit JSON API (/r/sub/hot.json) returns 403 as of 2026.
        url = f"{_REDDIT_BASE}/r/{sub}/{self._re_config.listing}.rss"
        await self._rate_limit()
        self._record_request_metric(method="reddit_atom")
        try:
            resp = await self._client.get(url, params={"limit": min(100, per_sub)})
            if resp.status_code == 429:
                _log.warning("reddit_ecommerce.rate_limited", subreddit=sub)
                return []
            resp.raise_for_status()
            root = ET.fromstring(resp.content)  # noqa: S314
        except httpx.HTTPStatusError as e:
            _log.warning("reddit_ecommerce.http_error", status=e.response.status_code, sub=sub)
            return []
        except ET.ParseError as e:
            _log.warning("reddit_ecommerce.xml_parse_error", error=str(e), sub=sub)
            return []
        except Exception as e:
            _log.warning("reddit_ecommerce.request_error", error=str(e), sub=sub)
            return []

        entries = root.findall(f"{{{_ATOM_NS}}}entry")
        return [_atom_entry_to_post(e, sub) for e in entries[:per_sub]]

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        cfg = self._re_config
        per_sub = max(1, limit // max(1, len(cfg.subreddits)))

        results = await asyncio.gather(
            *[self._fetch_subreddit(sub, per_sub) for sub in cfg.subreddits],
            return_exceptions=True,
        )

        total = 0
        for result in results:
            if isinstance(result, BaseException):
                _log.warning("reddit_ecommerce.gather_error", error=str(result))
                continue
            for post in result:
                if total >= limit or self.is_cancelled:
                    return
                yield post
                total += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            post_id = str(raw.get("id") or "")
            if not post_id:
                return None

            if raw.get("over_18"):
                return None

            external_id = f"t3_{post_id}"
            title = str(raw.get("title") or "")
            selftext = str(raw.get("selftext") or "")
            subreddit = str(raw.get("subreddit") or raw.get("_subreddit_override") or "")
            author_name = str(raw.get("author") or "")
            score = int(raw.get("score") or 0)
            num_comments = int(raw.get("num_comments") or 0)
            permalink = raw.get("permalink")
            url = raw.get("url")
            created_utc = raw.get("created_utc")
            is_self = bool(raw.get("is_self", False))

            canonical_url = (
                f"https://www.reddit.com{permalink}"
                if permalink
                else (url or f"https://www.reddit.com/comments/{post_id}/")
            )

            posted_at: datetime | None = None
            if created_utc is not None:
                with contextlib.suppress(ValueError, OSError):
                    posted_at = datetime.fromtimestamp(float(created_utc), tz=UTC)

            author: Author | None = None
            if author_name and author_name not in ("", "[deleted]"):
                author = Author(
                    platform_user_id=author_name,
                    handle=author_name,
                    profile_url=f"https://reddit.com/user/{author_name}",  # type: ignore[arg-type]
                )

            raw_text: str | None = None
            if is_self and selftext and selftext not in ("[deleted]", "[removed]"):
                raw_text = selftext[:20_000]
            elif not is_self and url:
                raw_text = url

            sentiment = score_text(title + " " + (selftext[:500] if selftext else ""))

            h = compute_content_hash(
                platform=Platform.REDDIT_ECOMMERCE,
                external_id=external_id,
                url=canonical_url,
                title=title[:512] if title else None,
                raw_text=raw_text,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.REDDIT_ECOMMERCE,
                tier=SourceTier.TIER_1_INTENT,
                external_id=external_id,
                url=canonical_url,  # type: ignore[arg-type]
                title=title[:512] if title else None,
                raw_text=raw_text,
                modality=ContentModality.TEXT,
                tags=frozenset({subreddit.lower()} if subreddit else ()),
                intent=IntentType.ENGAGE,
                author=author,
                engagement=EngagementMetrics(likes=score, comments=num_comments),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.GREEN,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.80 if title else 0.40,
                    source_confidence=0.90,
                ),
                content_hash=h,
                platform_specific={
                    "subreddit": subreddit,
                    "post_id": post_id,
                    "score": score,
                    "num_comments": num_comments,
                    "sentiment": sentiment,
                },
            )
        except Exception as e:
            _log.warning("reddit_ecommerce.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "_USER_AGENT", "RedditEcommerceAdapter", "RedditEcommerceConfig"]
