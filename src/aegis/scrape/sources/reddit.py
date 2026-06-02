"""Reddit source adapter.

Uses PRAW (Python Reddit API Wrapper) in read-only mode. Reddit's official
API is rate-limited but free; we register a script-type app, attach a
client_id + client_secret, and PRAW handles OAuth2 + retries + pagination.

Design choices:

- **Read-only auth (no Reddit user account)**: only requires a Reddit
  developer app (script type), zero ToS risk for public submission/comment
  reading. ``ToS_RISK = GREEN``.
- **Async-via-thread**: PRAW is synchronous-only. Rather than maintain a
  separate async port, we bridge with ``asyncio.to_thread``. The Reddit
  rate-limit + ``per_source_concurrency`` cap mean we won't saturate a
  thread pool.
- **No proxies needed for Reddit API**: Reddit's API is generous enough
  that direct IP usage is fine for v1. Adapter accepts a proxy_pool kwarg
  for forward compatibility but doesn't enforce its use.
- **Comment depth budgeting**: a single popular submission can have 10k+
  comments. We cap descent at ``max_comments_per_submission`` (configurable)
  to keep scrape latency bounded.

What a Reddit signal looks like:

    Submission "Best cast-iron skillet — 20 years of use" in r/BuyItForLife
        → ProductSignal(
            platform=REDDIT, tier=T1_intent,
            external_id="t3_<id>", url="https://reddit.com/r/...",
            title="Best cast-iron skillet — 20 years of use",
            raw_text=submission.selftext,
            tags={"BuyItForLife"},  # subreddit becomes a tag
            author=Author(handle=submission.author.name, ...),
            engagement=EngagementMetrics(
                views=None,  # Reddit doesn't expose
                likes=submission.score,  # net upvotes
                comments=submission.num_comments,
            ),
            ...
        )

Author: AEGIS Pulse Team
"""

from __future__ import annotations

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

# Adapter version — bumped when the parser logic changes meaningfully.
# Persisted on every signal so we can later replay parse logic on stored data.
SCRAPER_VERSION = "reddit-0.1.0"


# =============================================================================
# Config
# =============================================================================


@dataclass(frozen=True, slots=True)
class RedditConfig(AdapterConfig):
    """Reddit adapter config. Inherits ``AdapterConfig`` + Reddit-specific fields.

    Defaults are tuned for Reddit's API rate limit (60 req/min for OAuth apps).
    """

    name: str = "reddit"
    per_source_concurrency: int = 4
    per_source_rps: float = 1.0  # 60 RPM = 1 RPS — stay well under
    timeout_seconds: float = 20.0
    max_retries: int = 3
    use_cloudflare_bypass: bool = False
    """Reddit's API doesn't get Cloudflare-challenged."""

    # --- Reddit-specific ---------------------------------------------------
    client_id: str = ""
    client_secret: str = ""
    user_agent: str = ""
    """Reddit REQUIRES a unique, descriptive UA. Pattern:
    ``"<platform>:<app_name>:<version> (by /u/<username>)"``.
    Validated in ``__post_init__``."""

    max_submissions: int = 100
    """Cap on submissions returned per ``run()`` call."""

    max_comments_per_submission: int = 50
    """Cap on the comment forest per submission. 0 = no comments scraped."""

    listing: str = "hot"
    """Listing endpoint: ``hot`` / ``new`` / ``top`` / ``rising``."""

    def __post_init__(self) -> None:
        if not self.client_id or not self.client_secret:
            raise ValueError(
                "RedditConfig requires client_id + client_secret "
                "(register at https://www.reddit.com/prefs/apps)",
            )
        if not self.user_agent or "by /u/" not in self.user_agent:
            raise ValueError(
                "Reddit user_agent MUST follow the pattern "
                '"<platform>:<app>:<version> (by /u/<username>)"',
            )
        if self.listing not in ("hot", "new", "top", "rising"):
            raise ValueError(f"invalid listing: {self.listing!r}")


# =============================================================================
# Per-run context — adds Reddit-specific tracking
# =============================================================================


@dataclass(slots=True)
class RedditScrapeContext(ScrapeContext):
    subreddit_name: str = ""
    """Set at run start; used in log + metric labels."""


# =============================================================================
# The adapter
# =============================================================================


class RedditAdapter(SourceAdapter[Any]):
    """Reddit submission + comment scraper using PRAW.

    Run with::

        adapter = RedditAdapter(
            RedditConfig(
                client_id="...",
                client_secret="...",
                user_agent="linux:aegis-pulse:0.1.0 (by /u/yourhandle)",
            )
        )
        async for signal in adapter.run(subreddit="BuyItForLife", limit=20):
            ...
    """

    parse_is_blocking: bool = True
    """PRAW objects are synchronous + lazily fetched; offload parse to thread."""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config: RedditConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._reddit_config: RedditConfig = config
        # PRAW Reddit instance — created in setup(), torn down in teardown().
        # Typed as Any because we don't import praw at module level (heavy
        # transitive deps; some installs may not have it).
        self._reddit: Any = None

    # ------------------------------------------------------------------
    # Required overrides
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._reddit_config.name

    def _new_context(self) -> ScrapeContext:
        return RedditScrapeContext()

    async def setup(self, ctx: ScrapeContext) -> None:
        """Create the PRAW Reddit client (read-only mode)."""
        # Lazy import — avoids forcing praw onto consumers that only use
        # other adapters.
        try:
            import praw
        except ImportError as e:
            raise RuntimeError(
                "RedditAdapter requires praw — install with `uv pip install praw`",
            ) from e

        cfg = self._reddit_config

        def _build_client() -> Any:
            return praw.Reddit(
                client_id=cfg.client_id,
                client_secret=cfg.client_secret,
                user_agent=cfg.user_agent,
                # Read-only mode: no user account, no OAuth refresh dance.
                # PRAW auto-detects this when username/password are absent.
                check_for_async=False,
                # PRAW has its own retry layer that is unhelpful for us
                # (busy-loops, no jitter). Let our resilient_call handle it.
                ratelimit_seconds=5,
            )

        # praw.Reddit() does network IO (fetches /api/v1/me to detect mode).
        # Bridge through to_thread to keep the loop responsive.
        import asyncio

        self._reddit = await asyncio.to_thread(_build_client)

        # Verify auth actually works.
        try:
            mode = await asyncio.to_thread(lambda: self._reddit.read_only)
        except Exception as e:
            raise RuntimeError(
                f"Reddit auth check failed: {type(e).__name__}: {e}. "
                "Verify client_id / client_secret are correct.",
            ) from e
        log.info("reddit.connected", read_only=mode)

    async def teardown(self, ctx: ScrapeContext) -> None:
        """Close any underlying connection. PRAW has no explicit close()."""
        # PRAW uses requests under the hood; sessions are closed on GC.
        # Drop the reference to encourage prompt release.
        self._reddit = None

    async def fetch_raw(  # type: ignore[override]  # narrow params per adapter — base accepts **Any
        self,
        ctx: ScrapeContext,
        *,
        subreddit: str,
        limit: int | None = None,
        listing: str | None = None,
        **_: Any,
    ) -> AsyncIterator[Any]:
        """Yield PRAW Submission objects from one subreddit.

        Args:
            subreddit: name without ``r/`` prefix (e.g. ``"BuyItForLife"``).
            limit: max submissions; defaults to ``config.max_submissions``.
            listing: ``hot`` / ``new`` / ``top`` / ``rising``; defaults to config.

        The function also yields each submission's top-level comments (up to
        ``max_comments_per_submission``), which produce additional signals
        with ``intent=IntentType.ENGAGE``.
        """
        if self._reddit is None:
            raise RuntimeError("RedditAdapter.setup() must run before fetch_raw()")

        if isinstance(ctx, RedditScrapeContext):
            ctx.subreddit_name = subreddit

        cfg = self._reddit_config
        max_subs = limit if limit is not None else cfg.max_submissions
        listing_kind = listing or cfg.listing

        import asyncio

        # Build the PRAW listing iterator (still synchronous).
        def _build_iter() -> Any:
            sub = self._reddit.subreddit(subreddit)
            method = {
                "hot": sub.hot,
                "new": sub.new,
                "top": sub.top,
                "rising": sub.rising,
            }[listing_kind]
            return method(limit=max_subs)

        listing_iter = await asyncio.to_thread(_build_iter)

        # PRAW iterators do paginated network IO on each __next__. We pull
        # one item at a time on a thread, yielding each as it lands, so
        # the consumer can start processing immediately.
        def _pull_one(it: Any) -> Any:
            try:
                return next(it)
            except StopIteration:
                return _SENTINEL_DONE

        max_comments = cfg.max_comments_per_submission

        # Iterate submissions
        while not self.is_cancelled:
            await self._rate_limit()
            self._record_request_metric(method="praw_listing")
            submission = await asyncio.to_thread(_pull_one, listing_iter)
            if submission is _SENTINEL_DONE:
                break
            yield submission

            if max_comments <= 0:
                continue

            # Yield comments for this submission.
            await self._rate_limit()
            self._record_request_metric(method="praw_comments")

            def _comments(sub: Any) -> list[Any]:
                # replace_more(limit=0) drops "load more comments" placeholders.
                sub.comments.replace_more(limit=0)
                return list(sub.comments)[:max_comments]

            comments = await asyncio.to_thread(_comments, submission)
            for c in comments:
                if self.is_cancelled:
                    return  # type: ignore[unreachable]  # bare return terminates async generator
                yield c

    def parse(self, raw: Any, ctx: ScrapeContext) -> ProductSignal | None:
        """Convert a PRAW Submission OR Comment into a ``ProductSignal``."""
        # We branch by checking for attributes UNIQUE to each type, rather
        # than ones that exist on both. PRAW comments have `body` + `parent_id`;
        # submissions have `selftext` + `num_comments`. Importantly, comments
        # ALSO have a passthrough `.title` (forwards to parent submission), so
        # we cannot use `title` to identify a submission.
        is_comment = hasattr(raw, "body") and hasattr(raw, "parent_id")
        is_submission = hasattr(raw, "selftext") and hasattr(raw, "num_comments")
        if is_comment and not is_submission:
            return self._parse_comment(raw, ctx)
        if is_submission:
            return self._parse_submission(raw, ctx)
        log.warning("reddit.parse.unknown_type", type=type(raw).__name__)
        return None

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_submission(self, sub: Any, ctx: ScrapeContext) -> ProductSignal | None:
        """Parse a Submission object."""
        # PRAW lazy attrs may raise if the submission was deleted/removed.
        try:
            title = str(sub.title or "")
            body = str(sub.selftext or "")
            score = int(sub.score or 0)
            num_comments = int(sub.num_comments or 0)
            created_utc = float(sub.created_utc or 0.0)
            url = f"https://reddit.com{sub.permalink}" if getattr(sub, "permalink", None) else None
            external_id = f"t3_{sub.id}"
            subreddit_name = (
                str(sub.subreddit.display_name)
                if hasattr(sub, "subreddit")
                else getattr(ctx, "subreddit_name", "")
            )
            is_self = bool(getattr(sub, "is_self", True))
            over_18 = bool(getattr(sub, "over_18", False))
            stickied = bool(getattr(sub, "stickied", False))
        except Exception as e:
            log.warning("reddit.parse.submission.bad_attr", error=str(e))
            return None

        # Filter out housekeeping content.
        if stickied:
            return None
        if over_18:
            # We could keep with a "nsfw" tag, but for v1 we drop —
            # downstream compliance gate doesn't trust NSFW signals.
            return None

        author = self._extract_author(sub)
        posted_at = datetime.fromtimestamp(created_utc, tz=UTC) if created_utc else None

        h = compute_content_hash(
            platform=Platform.REDDIT,
            external_id=external_id,
            url=url,
            title=title or None,
            raw_text=body or None,
            posted_at=posted_at,
        )

        return ProductSignal(
            platform=Platform.REDDIT,
            tier=SourceTier.TIER_1_INTENT,
            external_id=external_id,
            url=url,  # type: ignore[arg-type]
            title=title or None,
            raw_text=body or None,
            modality=ContentModality.TEXT if is_self else ContentModality.MULTIMODAL,
            tags=frozenset({subreddit_name.lower()} if subreddit_name else ()),
            intent=IntentType.ENGAGE,
            author=author,
            engagement=EngagementMetrics(
                views=None,  # Reddit hides views in API
                likes=max(0, score),  # score = ups - downs; can be negative
                comments=num_comments,
            ),
            posted_at=posted_at,
            provenance=ScrapeProvenance(
                method=ScrapeMethod.OFFICIAL_API,
                scraped_at=datetime.now(UTC),
                scraper_version=SCRAPER_VERSION,
                tos_risk=ToSRisk.GREEN,
            ),
            confidence=ConfidenceMetadata(
                completeness=self._completeness(submission=True, has_body=bool(body)),
                source_confidence=0.95,  # official API
            ),
            content_hash=h,
            platform_specific={
                "subreddit": subreddit_name,
                "is_self": is_self,
                "score": score,
                "is_submission": True,
            },
        )

    def _parse_comment(self, comment: Any, ctx: ScrapeContext) -> ProductSignal | None:
        """Parse a Comment object. ``submission`` is fetched lazily by PRAW."""
        try:
            body = str(comment.body or "")
            if body in ("[deleted]", "[removed]"):
                return None
            score = int(comment.score or 0)
            created_utc = float(comment.created_utc or 0.0)
            external_id = f"t1_{comment.id}"
            permalink = getattr(comment, "permalink", None)
            url = f"https://reddit.com{permalink}" if permalink else None
            subreddit_name = (
                str(comment.subreddit.display_name)
                if hasattr(comment, "subreddit")
                else getattr(ctx, "subreddit_name", "")
            )
        except Exception as e:
            log.warning("reddit.parse.comment.bad_attr", error=str(e))
            return None

        author = self._extract_author(comment)
        posted_at = datetime.fromtimestamp(created_utc, tz=UTC) if created_utc else None

        h = compute_content_hash(
            platform=Platform.REDDIT,
            external_id=external_id,
            url=url,
            title=None,
            raw_text=body,
            posted_at=posted_at,
        )

        return ProductSignal(
            platform=Platform.REDDIT,
            tier=SourceTier.TIER_1_INTENT,
            external_id=external_id,
            url=url,  # type: ignore[arg-type]
            title=None,
            raw_text=body,
            modality=ContentModality.TEXT,
            tags=frozenset({subreddit_name.lower()} if subreddit_name else ()),
            intent=self._classify_intent(body),
            author=author,
            engagement=EngagementMetrics(
                likes=max(0, score),
            ),
            posted_at=posted_at,
            provenance=ScrapeProvenance(
                method=ScrapeMethod.OFFICIAL_API,
                scraped_at=datetime.now(UTC),
                scraper_version=SCRAPER_VERSION,
                tos_risk=ToSRisk.GREEN,
            ),
            confidence=ConfidenceMetadata(
                completeness=self._completeness(submission=False, has_body=True),
                source_confidence=0.95,
            ),
            content_hash=h,
            platform_specific={
                "subreddit": subreddit_name,
                "score": score,
                "is_submission": False,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_author(obj: Any) -> Author | None:
        """Build an ``Author`` from a PRAW object's ``.author`` redditor.

        Handles deleted accounts (``author == None``) and shadow-banned
        accounts (``author.name`` raises) gracefully.
        """
        try:
            redditor = obj.author
            if redditor is None:
                return None
            handle = str(redditor.name)
            return Author(
                platform_user_id=handle,  # Reddit uses the handle as a stable ID
                handle=handle,
                profile_url=f"https://reddit.com/user/{handle}",  # type: ignore[arg-type]
            )
        except Exception:
            return None

    @staticmethod
    def _completeness(*, submission: bool, has_body: bool) -> float:
        """Heuristic completeness score for the confidence metadata.

        Submissions with no body (link posts) score lower; comments with a
        body score 1.0.
        """
        if submission:
            return 1.0 if has_body else 0.7
        return 1.0

    @staticmethod
    def _classify_intent(body: str) -> IntentType:
        """Heuristic: detect purchase / search intent in comment text.

        Keyword-based; the proper ML classifier lives in Phase 3 and supersedes
        this. We keep it cheap and conservative — false positives only.
        """
        low = body.lower()
        if any(
            p in low
            for p in (
                "where can i buy",
                "where to buy",
                "link to buy",
                "any link",
                "drop the link",
                "buying one",
                "ordered one",
                "just bought",
            )
        ):
            return IntentType.PURCHASE
        if any(
            p in low
            for p in (
                "looking for",
                "trying to find",
                "anyone know where",
                "recommendations for",
            )
        ):
            return IntentType.SEARCH
        return IntentType.ENGAGE


# Sentinel used by fetch_raw's iterator drain.
_SENTINEL_DONE = object()


__all__ = [
    "SCRAPER_VERSION",
    "RedditAdapter",
    "RedditConfig",
    "RedditScrapeContext",
]
