"""Enumerations shared across the signal schema and storage layers.

These are kept separate from ``signal.py`` so that storage code (e.g. DB
migrations, ClickHouse sinks) can import just the enums without pulling in
the pydantic model surface.

Author: AEGIS Pulse Team
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class Platform(StrEnum):
    """Every upstream source we ingest from.

    String values are stable — they are persisted to Postgres (as enum values)
    and referenced in downstream feature configs. Do NOT rename an existing
    member; add new members at the end.
    """

    # --- Tier 1: intent signals ------------------------------------------------
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    PINTEREST = "pinterest"
    REDDIT = "reddit"

    # --- Tier 2: commerce ------------------------------------------------------
    AMAZON = "amazon"
    SHOPIFY = "shopify"
    ETSY = "etsy"
    ALIEXPRESS = "aliexpress"
    DHGATE = "dhgate"
    GOOGLE_SHOPPING = "google_shopping"
    EBAY = "ebay"

    # --- Tier 3: search + ad intel --------------------------------------------
    GOOGLE_TRENDS = "google_trends"
    META_AD_LIBRARY = "meta_ad_library"
    TIKTOK_CREATIVE = "tiktok_creative_center"
    USPTO = "uspto"
    EUIPO = "euipo"
    COMMON_CRAWL = "common_crawl"

    # --- Tier 4: cultural + macro ---------------------------------------------
    X_TWITTER = "x_twitter"
    NITTER = "nitter"
    BLUESKY = "bluesky"
    MASTODON = "mastodon"
    GDELT = "gdelt"
    TWITCH = "twitch"
    DISCORD = "discord"
    APP_STORE = "app_store"
    GOOGLE_PLAY = "google_play"

    # --- Tier 5: alternative data (NEW in Omega v2) ---------------------------
    GITHUB_TRENDING = "github_trending"
    HACKER_NEWS = "hacker_news"
    PRODUCT_HUNT = "product_hunt"
    SHOPIFY_APPS = "shopify_apps"
    WAYBACK = "wayback_machine"


@unique
class SourceTier(StrEnum):
    """Logical tier of a platform. Used for feature weighting:

    T1 (intent) > T2 (commerce) > T3 (search/ad) > T4 (cultural) > T5 (alternative)
    """

    TIER_1_INTENT = "T1_intent"
    TIER_2_COMMERCE = "T2_commerce"
    TIER_3_SEARCH = "T3_search"
    TIER_4_CULTURAL = "T4_cultural"
    TIER_5_ALTERNATIVE = "T5_alternative"


# Platform → Tier mapping. Kept in code (not DB) because it rarely changes
# and must be consistent across all services (scrape, features, agents).
_PLATFORM_TIER: dict[Platform, SourceTier] = {
    Platform.TIKTOK: SourceTier.TIER_1_INTENT,
    Platform.YOUTUBE: SourceTier.TIER_1_INTENT,
    Platform.INSTAGRAM: SourceTier.TIER_1_INTENT,
    Platform.PINTEREST: SourceTier.TIER_1_INTENT,
    Platform.REDDIT: SourceTier.TIER_1_INTENT,
    Platform.AMAZON: SourceTier.TIER_3_SEARCH,  # scraper collects ranking data, not prices
    Platform.SHOPIFY: SourceTier.TIER_2_COMMERCE,
    Platform.ETSY: SourceTier.TIER_2_COMMERCE,
    Platform.ALIEXPRESS: SourceTier.TIER_2_COMMERCE,
    Platform.DHGATE: SourceTier.TIER_2_COMMERCE,
    Platform.GOOGLE_SHOPPING: SourceTier.TIER_2_COMMERCE,
    Platform.EBAY: SourceTier.TIER_2_COMMERCE,
    Platform.GOOGLE_TRENDS: SourceTier.TIER_3_SEARCH,
    Platform.META_AD_LIBRARY: SourceTier.TIER_3_SEARCH,
    Platform.TIKTOK_CREATIVE: SourceTier.TIER_3_SEARCH,
    Platform.USPTO: SourceTier.TIER_3_SEARCH,
    Platform.EUIPO: SourceTier.TIER_3_SEARCH,
    Platform.COMMON_CRAWL: SourceTier.TIER_3_SEARCH,
    Platform.X_TWITTER: SourceTier.TIER_4_CULTURAL,
    Platform.NITTER: SourceTier.TIER_4_CULTURAL,
    Platform.BLUESKY: SourceTier.TIER_4_CULTURAL,
    Platform.MASTODON: SourceTier.TIER_4_CULTURAL,
    Platform.GDELT: SourceTier.TIER_4_CULTURAL,
    Platform.TWITCH: SourceTier.TIER_4_CULTURAL,
    Platform.DISCORD: SourceTier.TIER_4_CULTURAL,
    Platform.APP_STORE: SourceTier.TIER_4_CULTURAL,
    Platform.GOOGLE_PLAY: SourceTier.TIER_4_CULTURAL,
    Platform.GITHUB_TRENDING: SourceTier.TIER_5_ALTERNATIVE,
    Platform.HACKER_NEWS: SourceTier.TIER_5_ALTERNATIVE,
    Platform.PRODUCT_HUNT: SourceTier.TIER_5_ALTERNATIVE,
    Platform.SHOPIFY_APPS: SourceTier.TIER_5_ALTERNATIVE,
    Platform.WAYBACK: SourceTier.TIER_5_ALTERNATIVE,
}


def platform_tier(platform: Platform) -> SourceTier:
    """Return the canonical tier for a given platform.

    Raises:
        KeyError: if a new Platform member was added without updating
            ``_PLATFORM_TIER``. The error is explicit so CI catches it.
    """
    return _PLATFORM_TIER[platform]


@unique
class ContentModality(StrEnum):
    """Modality of a signal's primary content — drives embedding + feature paths."""

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    MULTIMODAL = "multimodal"
    STRUCTURED = "structured"
    """e.g. a trademark filing or price snapshot — no media, just typed fields."""


@unique
class IntentType(StrEnum):
    """Inferred user intent behind a signal.

    Ordered loosely by predictive value for arbitrage (highest first)."""

    PURCHASE = "purchase"
    """User explicitly states purchase intent (``'where do i buy'``, ``'link pls'``)."""
    SAVE = "save"
    """User saved/bookmarked — strong latent demand (Pinterest, Reels saves)."""
    SEARCH = "search"
    """Active search — Google Trends, on-platform search."""
    ENGAGE = "engage"
    """Comment, reply, share — discussion, not yet intent."""
    PASSIVE = "passive"
    """Likes, views — vanity metrics; lowest signal value."""
    UNKNOWN = "unknown"


@unique
class ScrapeMethod(StrEnum):
    """How a signal was collected. Recorded for audit, debugging, and ToS gating."""

    OFFICIAL_API = "official_api"
    PUBLIC_API_UNOFFICIAL = "public_api_unofficial"
    PLAYWRIGHT_HEADLESS = "playwright_headless"
    PATCHRIGHT_STEALTH = "patchright_stealth"
    CURL_IMPERSONATE = "curl_impersonate"
    UNDETECTED_CHROMEDRIVER = "undetected_chromedriver"
    RSS_FEED = "rss_feed"
    FIREHOSE_STREAM = "firehose_stream"
    """Always-on streaming (Bluesky, GDELT, Twitch IRC)."""


@unique
class ConfidenceBand(StrEnum):
    """Bucketed confidence for human-readable tagging. The raw float stays on
    the signal object; this is for dashboards / alerts where ranges are easier
    to reason about than "0.73"."""

    VERY_LOW = "very_low"  # [0.0, 0.2)
    LOW = "low"  # [0.2, 0.4)
    MEDIUM = "medium"  # [0.4, 0.6)
    HIGH = "high"  # [0.6, 0.8)
    VERY_HIGH = "very_high"  # [0.8, 1.0]


def confidence_band(value: float) -> ConfidenceBand:
    """Map a confidence float in [0, 1] to a bucket.

    Args:
        value: confidence score, must be within [0, 1].

    Raises:
        ValueError: if value is outside [0, 1].
    """
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"confidence must be in [0, 1], got {value!r}")
    if value < 0.2:
        return ConfidenceBand.VERY_LOW
    if value < 0.4:
        return ConfidenceBand.LOW
    if value < 0.6:
        return ConfidenceBand.MEDIUM
    if value < 0.8:
        return ConfidenceBand.HIGH
    return ConfidenceBand.VERY_HIGH


@unique
class ToSRisk(StrEnum):
    """Per-source ToS risk score. Drives scraping behaviour (strict ``robots.txt``
    honoring, CAPTCHA handling) and legal gate in Phase 8.

    - ``GREEN``: official API or explicitly public data (USPTO, GDELT).
    - ``AMBER``: public content, scraping tolerated in practice (Reddit, HN).
    - ``RED``: actively defended against bots (TikTok, Instagram).
    - ``BLACK``: authenticated-only; do NOT scrape at v1 — requires legal review.
    """

    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    BLACK = "black"


__all__ = [
    "ConfidenceBand",
    "ContentModality",
    "IntentType",
    "Platform",
    "ScrapeMethod",
    "SourceTier",
    "ToSRisk",
    "confidence_band",
    "platform_tier",
]
