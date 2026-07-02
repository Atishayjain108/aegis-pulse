"""Reddit Atom RSS adapter (no API credentials required).

Reddit exposes public Atom RSS feeds for every subreddit:
  https://www.reddit.com/r/{subreddit}/.rss
  https://www.reddit.com/r/popular.rss

The old JSON API (*.json) now returns 403; the Atom feed still returns 200.
No client_id or client_secret required — only a descriptive User-Agent.

ToS Risk: GREEN — publicly listed RSS feed, no auth required.
Rate limit: stay at 1 req/3 s to stay well under Reddit's 60 req/min cap.
"""

from __future__ import annotations

import contextlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
    Author,
    ConfidenceMetadata,
    EngagementMetrics,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter
from aegis.scrape.harden_shim import HardenShim
from aegis.scrape.http_client import get_or_create_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)

SCRAPER_VERSION = "reddit-atom-0.3.0"

_REDDIT_BASE = "https://www.reddit.com"

_VALID_LISTINGS = ("hot", "new", "top", "rising", "best")

_ATOM_NS = "http://www.w3.org/2005/Atom"


def _parse_atom_ts(ts: str) -> float | None:
    """Parse an ISO-8601 timestamp from the Atom feed to a UTC unix float."""
    if not ts:
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    return None


@dataclass(frozen=True, slots=True)
class RedditRSSConfig(AdapterConfig):
    """Reddit JSON API adapter config — no API credentials needed."""

    name: str = "reddit-rss"
    per_source_rps: float = 0.33  # 1 req per 3 seconds
    timeout_seconds: float = 20.0
    max_retries: int = 3
    use_cloudflare_bypass: bool = False

    listing: str = "hot"
    """Feed type: 'hot' | 'new' | 'top' | 'rising' | 'best'."""

    subreddits: tuple[str, ...] = ("popular",)
    """Subreddits to scrape. Use 'popular' or 'all' for site-wide feeds."""


class RedditRSSAdapter(SourceAdapter[dict[str, Any]]):
    """Reddit via public JSON API — no credentials required.

    Run with::

        adapter = RedditRSSAdapter(
            RedditRSSConfig(
                subreddits=("BuyItForLife", "frugalmalefashion"),
                listing="hot",
            )
        )
        async for signal in adapter.run(limit=20):
            ...
    """

    def __init__(
        self,
        config: RedditRSSConfig | AdapterConfig,
        *,
        harden_shim: HardenShim | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self._rss_config = config if isinstance(config, RedditRSSConfig) else RedditRSSConfig()
        self._client: httpx.AsyncClient | None = None
        self._harden_shim = harden_shim
        self._harden_seq = 0

    @property
    def name(self) -> str:
        return "reddit-rss"

    async def setup(self, ctx: ScrapeContext) -> None:
        self._client = await get_or_create_client(
            "www.reddit.com",
            http2=False,
            timeout=self._rss_config.timeout_seconds,
            headers={
                # Reddit requires a descriptive User-Agent or returns 429/403.
                # Must NOT send 'br' in Accept-Encoding — Reddit's CDN blocks it.
                "User-Agent": "python:aegis-pulse:v0.1.0 (market research; non-commercial)",
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        # Shared pooled client (PASS5-5B) — release the reference, never close.
        self._client = None

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        subreddit: str | None = None,
        limit: int = 50,
        listing: str | None = None,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("RedditRSSAdapter.setup() must run before fetch_raw()")

        cfg = self._rss_config
        listing_kind = listing or cfg.listing
        if listing_kind not in _VALID_LISTINGS:
            listing_kind = "hot"

        subreddits = [subreddit] if subreddit else list(cfg.subreddits)
        total_yielded = 0
        per_sub = max(1, limit // max(1, len(subreddits)))

        for sub in subreddits:
            if total_yielded >= limit or self.is_cancelled:
                return

            # Reddit JSON API (/r/sub/hot.json) returns 403 as of 2026.
            # The Atom RSS feed (/r/sub/.rss) still returns 200 — use that.
            if sub in ("popular", "all"):
                feed_url = f"{_REDDIT_BASE}/r/{sub}.rss"
            else:
                feed_url = f"{_REDDIT_BASE}/r/{sub}/{listing_kind}.rss"

            _extra_headers: dict[str, str] = {}
            if self._harden_shim is not None:
                decision = self._harden_shim.preflight(
                    source="reddit-rss", url=feed_url, seq=self._harden_seq
                )
                self._harden_seq += 1
                if decision.skip:
                    log.warning(
                        "reddit_rss.harden_skip",
                        url=feed_url,
                        subreddit=sub,
                        reason=decision.reason,
                    )
                    continue
                if decision.fingerprint is not None:
                    _extra_headers["User-Agent"] = decision.fingerprint.tls.ua

            await self._rate_limit()
            self._record_request_metric(method="reddit_atom")

            try:
                resp = await self._client.get(
                    feed_url,
                    params={"limit": min(100, per_sub)},
                    headers=_extra_headers or None,
                )
                if resp.status_code == 429:
                    log.warning("reddit_rss.rate_limited", subreddit=sub)
                    break
                resp.raise_for_status()
                xml_bytes = resp.content
            except httpx.HTTPStatusError as e:
                log.warning("reddit_rss.http_error", status=e.response.status_code, sub=sub)
                continue
            except httpx.RequestError as e:
                log.warning("reddit_rss.request_error", error=str(e), sub=sub)
                continue

            try:
                root = _safe_fromstring(xml_bytes)
            except ET.ParseError as e:
                log.warning("reddit_rss.xml_parse_error", error=str(e), sub=sub)
                continue

            ns = {"a": _ATOM_NS}
            entries = root.findall("a:entry", ns)

            for entry in entries:
                if total_yielded >= limit or self.is_cancelled:
                    return

                def _text(tag: str) -> str:
                    el = entry.find(tag, ns)
                    return el.text or "" if el is not None else ""

                raw_id = _text("a:id")
                # Reddit Atom id format: "t3_postid,https://..."
                post_id = raw_id.split(",")[0].lstrip("t3_") if raw_id else ""

                link_el = entry.find("a:link", ns)
                href = link_el.get("href", "") if link_el is not None else ""

                author_el = entry.find("a:author/a:name", ns)
                author_name = author_el.text or "" if author_el is not None else ""

                cat_el = entry.find("a:category", ns)
                subreddit_name = cat_el.get("term", sub) if cat_el is not None else sub

                updated = _text("a:updated")

                yield {
                    "id": post_id or raw_id,
                    "title": _text("a:title"),
                    "selftext": "",
                    "subreddit": subreddit_name,
                    "author": author_name,
                    "score": 0,
                    "num_comments": 0,
                    "url": href,
                    "permalink": href.replace("https://www.reddit.com", "") if href else None,
                    "created_utc": _parse_atom_ts(updated),
                    "is_self": False,
                    "over_18": False,
                    "_subreddit_override": sub,
                }
                total_yielded += 1

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            post_id: str = str(raw.get("id") or "")
            if not post_id:
                return None

            external_id = f"t3_{post_id}"
            title: str = str(raw.get("title") or "")
            selftext: str = str(raw.get("selftext") or "")
            subreddit: str = str(raw.get("subreddit") or raw.get("_subreddit_override") or "")
            author_name: str = str(raw.get("author") or "")
            score: int = int(raw.get("score") or 0)
            num_comments: int = int(raw.get("num_comments") or 0)
            url: str | None = raw.get("url")
            permalink: str | None = raw.get("permalink")
            created_utc: float | None = raw.get("created_utc")
            is_self: bool = bool(raw.get("is_self", False))
            nsfw: bool = bool(raw.get("over_18", False))

            if nsfw:
                return None

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
                raw_text = selftext[:20_000].strip() or None
            elif not is_self and url:
                raw_text = url.strip() or None

            tags = frozenset({subreddit.lower()} if subreddit else ())

            # Strip whitespace before hashing so the hash matches what Pydantic
            # will store after its str_strip_whitespace=True model_config pass.
            title_clean = title[:512].strip() or None if title else None

            h = compute_content_hash(
                platform=Platform.REDDIT,
                external_id=external_id,
                url=canonical_url,
                title=title_clean,
                raw_text=raw_text,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.REDDIT,
                tier=SourceTier.TIER_1_INTENT,
                external_id=external_id,
                url=canonical_url,  # type: ignore[arg-type]
                title=title_clean,
                raw_text=raw_text,
                modality=ContentModality.TEXT,
                tags=tags,
                intent=IntentType.ENGAGE,
                author=author,
                engagement=EngagementMetrics(
                    likes=score,
                    comments=num_comments,
                ),
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
                    "is_self": is_self,
                },
            )
        except Exception as e:
            log.warning("reddit_rss.parse.failed", error=str(e))
            return None


__all__ = ["SCRAPER_VERSION", "RedditRSSAdapter", "RedditRSSConfig"]
