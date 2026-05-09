"""Twitter/X source adapter via Nitter (no API key required).

Nitter is an open-source, privacy-respecting Twitter front-end.
It serves Twitter content without requiring authentication or API keys.

NOTE: Most public Nitter instances went dark after Twitter/X revoked
free API access in 2023. This adapter is best-effort — configure a
self-hosted or currently-working instance via NitterConfig.instances.

Public instances to try (availability changes frequently):
  - https://nitter.tiekoetter.com  (may require browser challenge)
  - https://nitter.poast.org       (may block non-browser UA)
  - https://nitter.net             (currently returns empty bodies)

ToS Risk: AMBER — reads publicly available Twitter content via an
unofficial proxy. We rate-limit aggressively. No authentication needed.

Usage:
    adapter = NitterAdapter(NitterConfig())
    async for signal in adapter.run(query="trending products"):
        ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)

SCRAPER_VERSION = "nitter-0.1.0"

# Public Nitter instances — tried in order, fallback to next on failure.
_NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

_TWEET_DATE_FMTS = (
    "%b %d, %Y · %I:%M %p UTC",
    "%b %d, %Y",
    "%Y-%m-%dT%H:%M:%S%z",
)


@dataclass(frozen=True, slots=True)
class NitterConfig(AdapterConfig):
    """Nitter adapter config."""

    name: str = "nitter"
    per_source_rps: float = 0.5   # 1 request per 2 seconds — conservative
    timeout_seconds: float = 30.0
    max_retries: int = 3
    use_cloudflare_bypass: bool = False

    instances: tuple[str, ...] = field(default_factory=lambda: tuple(_NITTER_INSTANCES))
    """Nitter instance base URLs. Tried in order on failure."""

    nitter_f: str = "search"
    """Nitter endpoint: 'search' (query) or 'hashtag' ."""


class NitterAdapter(SourceAdapter[dict[str, Any]]):
    """Twitter data via public Nitter instances (no API key required).

    Parses Nitter's HTML search page. Provides tweet text, author info,
    and engagement (likes/retweets from displayed counters).

    Note: Nitter instances may be intermittently unavailable. The adapter
    falls back through the configured instance list before giving up.
    """

    def __init__(self, config: NitterConfig | AdapterConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._nt_config = config if isinstance(config, NitterConfig) else NitterConfig()
        self._client: httpx.AsyncClient | None = None
        self._active_instance: str = self._nt_config.instances[0] if self._nt_config.instances else _NITTER_INSTANCES[0]

    @property
    def name(self) -> str:
        return "nitter"

    async def setup(self, ctx: ScrapeContext) -> None:
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._nt_config.timeout_seconds),
            headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        )

    async def teardown(self, ctx: ScrapeContext) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _try_fetch(self, url: str) -> str | None:
        """Try fetching URL, return HTML text or None on failure."""
        if self._client is None:
            return None
        try:
            resp = await self._client.get(url)
            if resp.status_code == 200:
                return resp.text
            log.warning("nitter.fetch.bad_status", status=resp.status_code, url=url)
            return None
        except httpx.RequestError as e:
            log.warning("nitter.fetch.request_error", error=str(e), url=url)
            return None

    async def fetch_raw(  # type: ignore[override]
        self,
        ctx: ScrapeContext,
        *,
        query: str = "trending",
        hashtag: str | None = None,
        limit: int = 50,
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("NitterAdapter.setup() must run before fetch_raw()")

        # Use hashtag if provided, else use search query.
        target = hashtag or query
        total_yielded = 0
        cursor: str | None = None

        instances = list(self._nt_config.instances) or _NITTER_INSTANCES
        active = instances[0]

        while total_yielded < limit and not self.is_cancelled:
            await self._rate_limit()
            self._record_request_metric(method="nitter_html")

            if hashtag:
                path = f"/{target}/media"  # hashtag timeline
            else:
                path = "/search?" + str(httpx.QueryParams({"q": target, "f": "tweets"}))
            if cursor:
                sep = "&" if "?" in path else "?"
                path = f"{path}{sep}cursor={cursor}"

            html: str | None = None
            for inst in instances:
                url = f"{inst}{path}"
                html = await self._try_fetch(url)
                if html:
                    active = inst
                    break
                log.warning("nitter.instance.failed", instance=inst)

            if not html:
                log.warning("nitter.all_instances_failed", target=target)
                break

            tweets = _parse_nitter_tweets(html)
            if not tweets:
                break

            for tweet in tweets:
                if total_yielded >= limit or self.is_cancelled:
                    return
                yield {"tweet": tweet, "instance": active}
                total_yielded += 1

            cursor = _extract_next_cursor(html)
            if not cursor:
                break

    def parse(self, raw: dict[str, Any], ctx: ScrapeContext) -> ProductSignal | None:
        try:
            tweet = raw.get("tweet", {})
            tweet_id: str = str(tweet.get("id") or "")
            if not tweet_id:
                return None

            text: str = str(tweet.get("text") or "")
            author_handle: str = str(tweet.get("author_handle") or "")
            author_name: str = str(tweet.get("author_name") or author_handle)
            likes: int = int(tweet.get("likes") or 0)
            retweets: int = int(tweet.get("retweets") or 0)
            replies: int = int(tweet.get("replies") or 0)
            date_str: str | None = tweet.get("date")

            posted_at: datetime | None = None
            if date_str:
                for fmt in _TWEET_DATE_FMTS:
                    try:
                        dt = datetime.strptime(date_str, fmt)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        posted_at = dt
                        break
                    except ValueError:
                        continue

            url = f"https://twitter.com/{author_handle}/status/{tweet_id}" if author_handle else None
            nitter_url = f"{raw.get('instance', _NITTER_INSTANCES[0])}/{author_handle}/status/{tweet_id}"

            author: Author | None = None
            if author_handle:
                author = Author(
                    platform_user_id=author_handle,
                    handle=author_handle,
                    display_name=author_name or None,
                    profile_url=f"https://twitter.com/{author_handle}",  # type: ignore[arg-type]
                )

            # Extract hashtags from tweet text
            hashtags = frozenset(
                tag.lower()
                for tag in re.findall(r"#(\w+)", text)
                if len(tag) <= 128
            )

            h = compute_content_hash(
                platform=Platform.X_TWITTER,
                external_id=tweet_id,
                url=url or nitter_url,
                title=None,
                raw_text=text or None,
                posted_at=posted_at,
            )

            return ProductSignal(
                platform=Platform.X_TWITTER,
                tier=SourceTier.TIER_4_CULTURAL,
                external_id=tweet_id,
                url=url or nitter_url,  # type: ignore[arg-type]
                title=None,
                raw_text=text or None,
                modality=ContentModality.TEXT,
                tags=hashtags,
                intent=IntentType.ENGAGE,
                author=author,
                engagement=EngagementMetrics(
                    likes=likes,
                    comments=replies,
                    shares=retweets,
                ),
                posted_at=posted_at,
                provenance=ScrapeProvenance(
                    method=ScrapeMethod.PUBLIC_API_UNOFFICIAL,
                    scraped_at=datetime.now(UTC),
                    scraper_version=SCRAPER_VERSION,
                    tos_risk=ToSRisk.AMBER,
                ),
                confidence=ConfidenceMetadata(
                    completeness=0.75 if (text and author_handle) else 0.4,
                    source_confidence=0.70,
                ),
                content_hash=h,
                platform_specific={
                    "tweet_id": tweet_id,
                    "likes": likes,
                    "retweets": retweets,
                    "replies": replies,
                    "via_nitter": raw.get("instance"),
                },
            )
        except Exception as e:
            log.warning("nitter.parse.failed", error=str(e))
            return None


def _parse_nitter_tweets(html: str) -> list[dict[str, Any]]:
    """Parse tweet cards from Nitter HTML.

    Nitter renders tweet cards with class ``timeline-item``. We extract
    the tweet ID, author handle, text, and engagement counters using
    lightweight regex — no full HTML parser dependency.
    """
    tweets: list[dict[str, Any]] = []
    try:
        # Split on tweet containers
        blocks = re.split(r'<div class="timeline-item[^"]*"', html)
        for block in blocks[1:]:  # first split is pre-content
            tweet = _parse_tweet_block(block)
            if tweet:
                tweets.append(tweet)
    except Exception as e:
        log.warning("nitter.parse_html.failed", error=str(e))
    return tweets


def _parse_tweet_block(block: str) -> dict[str, Any] | None:
    """Extract fields from one Nitter tweet block."""
    try:
        # Tweet ID from permalink
        id_match = re.search(r'/status/(\d+)', block)
        if not id_match:
            return None
        tweet_id = id_match.group(1)

        # Author handle
        handle_match = re.search(r'href="/([^/"]+)/status/', block)
        author_handle = handle_match.group(1) if handle_match else ""

        # Author display name
        name_match = re.search(r'class="fullname"[^>]*>([^<]+)<', block)
        author_name = _clean_text(name_match.group(1)) if name_match else author_handle

        # Tweet text
        text_match = re.search(
            r'class="tweet-content[^"]*"[^>]*>(.*?)</div>',
            block,
            re.DOTALL,
        )
        raw_text = _clean_text(text_match.group(1)) if text_match else ""

        # Date
        date_match = re.search(r'class="tweet-date"[^>]*>.*?title="([^"]+)"', block, re.DOTALL)
        date_str = date_match.group(1) if date_match else None

        # Engagement counters
        likes = _extract_counter(block, "icon-heart")
        retweets = _extract_counter(block, "icon-retweet")
        replies = _extract_counter(block, "icon-comment")

        return {
            "id": tweet_id,
            "author_handle": author_handle,
            "author_name": author_name,
            "text": raw_text,
            "date": date_str,
            "likes": likes,
            "retweets": retweets,
            "replies": replies,
        }
    except Exception:
        return None


def _extract_counter(block: str, icon_class: str) -> int:
    """Extract a numeric counter next to an icon class."""
    pattern = rf'{icon_class}.*?<span class="[^"]*"[^>]*>(\d[\d,]*)</span>'
    m = re.search(pattern, block, re.DOTALL)
    if not m:
        return 0
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return 0


def _extract_next_cursor(html: str) -> str | None:
    """Extract the pagination cursor from a Nitter page."""
    m = re.search(r'cursor=([^"&]+)', html)
    return m.group(1) if m else None


def _clean_text(html_fragment: str) -> str:
    """Strip HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


__all__ = ["NitterAdapter", "NitterConfig", "SCRAPER_VERSION"]
