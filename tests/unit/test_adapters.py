"""Unit tests for source adapter parse methods (no network I/O)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from aegis.schemas.enums import (
    ContentModality,
    IntentType,
    Platform,
    SourceTier,
    ToSRisk,
)
from aegis.scrape.base import ScrapeContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ctx() -> ScrapeContext:
    return ScrapeContext()


# ---------------------------------------------------------------------------
# Google Trends
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_google_trends_parse_basic():
    from aegis.scrape.sources.google_trends import GoogleTrendsAdapter, GoogleTrendsConfig

    adapter = GoogleTrendsAdapter(GoogleTrendsConfig())
    raw = {
        "keyword": "wireless earbuds",
        "value": 85,
        "geo": "US",
        "timeframe": "today 7-d",
        "is_partial": False,
        "date": "2024-01-15",
    }
    signal = adapter.parse(raw, ctx())
    assert signal is not None
    assert signal.platform == Platform.GOOGLE_TRENDS
    assert signal.title == "wireless earbuds"
    assert "wireless_earbuds" in signal.tags
    assert signal.engagement.views == 85


@pytest.mark.unit
def test_google_trends_parse_with_datetime_object():
    from aegis.scrape.sources.google_trends import GoogleTrendsAdapter, GoogleTrendsConfig

    adapter = GoogleTrendsAdapter(GoogleTrendsConfig())
    raw = {
        "keyword": "summer dress",
        "value": 100,
        "geo": "IN",
        "date": datetime(2024, 6, 1, tzinfo=UTC),
    }
    signal = adapter.parse(raw, ctx())
    assert signal is not None
    assert signal.posted_at is not None
    assert signal.posted_at.year == 2024


@pytest.mark.unit
def test_google_trends_parse_missing_keyword_returns_none():
    from aegis.scrape.sources.google_trends import GoogleTrendsAdapter, GoogleTrendsConfig

    adapter = GoogleTrendsAdapter(GoogleTrendsConfig())
    assert adapter.parse({}, ctx()) is None
    assert adapter.parse({"value": 50}, ctx()) is None


@pytest.mark.unit
def test_google_trends_parse_long_keyword_tag_truncated():
    from aegis.scrape.sources.google_trends import GoogleTrendsAdapter, GoogleTrendsConfig

    adapter = GoogleTrendsAdapter(GoogleTrendsConfig())
    long_kw = "a " * 70  # 140 chars
    raw = {"keyword": long_kw.strip(), "value": 10, "geo": "US"}
    signal = adapter.parse(raw, ctx())
    assert signal is not None
    for tag in signal.tags:
        assert len(tag) <= 128


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_youtube_parse_basic():
    from aegis.scrape.sources.youtube import YouTubeAdapter, YouTubeConfig

    adapter = YouTubeAdapter(YouTubeConfig(api_key="fake-key"))
    raw = {
        "item": {
            "id": {"videoId": "dQw4w9WgXcQ"},
            "snippet": {
                "title": "Never Gonna Give You Up",
                "description": "Classic 80s hit",
                "channelId": "UCuAXFkgsw1L7xaCfnd5JJOw",
                "channelTitle": "Rick Astley",
                "publishedAt": "2009-10-25T06:57:33Z",
                "thumbnails": {
                    "high": {
                        "url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
                        "width": 480,
                        "height": 360,
                    }
                },
            },
        },
        "detail": {
            "statistics": {
                "viewCount": "1400000000",
                "likeCount": "15000000",
                "commentCount": "2000000",
            },
            "snippet": {
                "tags": ["80s", "pop", "classic"],
            },
        },
    }
    signal = adapter.parse(raw, ctx())
    assert signal is not None
    assert signal.platform == Platform.YOUTUBE
    assert signal.external_id == "dQw4w9WgXcQ"
    assert signal.engagement.views == 1_400_000_000
    assert signal.engagement.likes == 15_000_000
    assert "80s" in signal.tags
    assert len(signal.media) == 1


@pytest.mark.unit
def test_youtube_parse_no_video_id_returns_none():
    from aegis.scrape.sources.youtube import YouTubeAdapter, YouTubeConfig

    adapter = YouTubeAdapter(YouTubeConfig(api_key="fake-key"))
    assert adapter.parse({"item": {"id": {}}, "detail": {}}, ctx()) is None
    assert adapter.parse({}, ctx()) is None


@pytest.mark.unit
def test_youtube_parse_no_thumbnail_gives_video_modality():
    from aegis.scrape.sources.youtube import YouTubeAdapter, YouTubeConfig

    adapter = YouTubeAdapter(YouTubeConfig(api_key="fake-key"))
    raw = {
        "item": {
            "id": {"videoId": "abc123"},
            "snippet": {
                "title": "Test Video",
                "channelId": "CH1",
                "channelTitle": "Channel",
                "thumbnails": {},
            },
        },
        "detail": {"statistics": {}},
    }
    signal = adapter.parse(raw, ctx())
    assert signal is not None
    assert signal.modality == ContentModality.VIDEO
    assert len(signal.media) == 0


@pytest.mark.unit
def test_youtube_config_missing_api_key_raises():
    from aegis.scrape.sources.youtube import YouTubeConfig

    with pytest.raises(ValueError, match="api_key"):
        YouTubeConfig(api_key="")


# ---------------------------------------------------------------------------
# Reddit (mock PRAW objects with SimpleNamespace)
# ---------------------------------------------------------------------------


def _mock_submission(
    *,
    id="abc123",
    title="Best mechanical keyboard?",
    selftext="Looking for recs",
    score=500,
    num_comments=42,
    created_utc=1705312800.0,
    permalink="/r/MechanicalKeyboards/comments/abc123/best_keyboard/",
    subreddit_display_name="MechanicalKeyboards",
    is_self=True,
    over_18=False,
    stickied=False,
    author_name="redditor99",
):
    return SimpleNamespace(
        id=id,
        title=title,
        selftext=selftext,
        score=score,
        num_comments=num_comments,
        created_utc=created_utc,
        permalink=permalink,
        is_self=is_self,
        over_18=over_18,
        stickied=stickied,
        author=SimpleNamespace(name=author_name) if author_name else None,
        subreddit=SimpleNamespace(display_name=subreddit_display_name),
    )


def _mock_comment(
    *,
    id="cmt456",
    body="You should try the Keychron K2!",
    score=100,
    created_utc=1705315200.0,
    permalink="/r/MechanicalKeyboards/comments/abc123/best_keyboard/cmt456",
    subreddit_display_name="MechanicalKeyboards",
    author_name="keyboardfan",
    parent_id="t3_abc123",
):
    return SimpleNamespace(
        id=id,
        body=body,
        score=score,
        created_utc=created_utc,
        permalink=permalink,
        parent_id=parent_id,
        subreddit=SimpleNamespace(display_name=subreddit_display_name),
        author=SimpleNamespace(name=author_name) if author_name else None,
    )


@pytest.mark.unit
def test_reddit_parse_submission():
    from aegis.scrape.sources.reddit import RedditAdapter, RedditConfig

    adapter = RedditAdapter(
        RedditConfig(client_id="x", client_secret="y", user_agent="script:test:1.0 (by /u/testbot)")
    )
    signal = adapter.parse(_mock_submission(), ctx())
    assert signal is not None
    assert signal.platform == Platform.REDDIT
    assert signal.external_id == "t3_abc123"
    assert signal.title == "Best mechanical keyboard?"
    assert signal.engagement.likes == 500
    assert signal.engagement.comments == 42
    assert "mechanicalkeyboards" in signal.tags


@pytest.mark.unit
def test_reddit_parse_submission_stickied_returns_none():
    from aegis.scrape.sources.reddit import RedditAdapter, RedditConfig

    adapter = RedditAdapter(
        RedditConfig(client_id="x", client_secret="y", user_agent="script:test:1.0 (by /u/testbot)")
    )
    assert adapter.parse(_mock_submission(stickied=True), ctx()) is None


@pytest.mark.unit
def test_reddit_parse_submission_nsfw_returns_none():
    from aegis.scrape.sources.reddit import RedditAdapter, RedditConfig

    adapter = RedditAdapter(
        RedditConfig(client_id="x", client_secret="y", user_agent="script:test:1.0 (by /u/testbot)")
    )
    assert adapter.parse(_mock_submission(over_18=True), ctx()) is None


@pytest.mark.unit
def test_reddit_parse_comment():
    from aegis.scrape.sources.reddit import RedditAdapter, RedditConfig

    adapter = RedditAdapter(
        RedditConfig(client_id="x", client_secret="y", user_agent="script:test:1.0 (by /u/testbot)")
    )
    signal = adapter.parse(_mock_comment(), ctx())
    assert signal is not None
    assert signal.platform == Platform.REDDIT
    assert signal.external_id == "t1_cmt456"
    assert signal.raw_text == "You should try the Keychron K2!"


@pytest.mark.unit
def test_reddit_parse_deleted_comment_returns_none():
    from aegis.scrape.sources.reddit import RedditAdapter, RedditConfig

    adapter = RedditAdapter(
        RedditConfig(client_id="x", client_secret="y", user_agent="script:test:1.0 (by /u/testbot)")
    )
    assert adapter.parse(_mock_comment(body="[deleted]"), ctx()) is None
    assert adapter.parse(_mock_comment(body="[removed]"), ctx()) is None


@pytest.mark.unit
def test_reddit_parse_no_author_ok():
    from aegis.scrape.sources.reddit import RedditAdapter, RedditConfig

    adapter = RedditAdapter(
        RedditConfig(client_id="x", client_secret="y", user_agent="script:test:1.0 (by /u/testbot)")
    )
    signal = adapter.parse(_mock_submission(author_name=None), ctx())
    assert signal is not None
    assert signal.author is None


@pytest.mark.unit
def test_reddit_completeness_link_post():
    from aegis.scrape.sources.reddit import RedditAdapter

    assert RedditAdapter._completeness(submission=True, has_body=True) == 1.0
    assert RedditAdapter._completeness(submission=True, has_body=False) == 0.7
    assert RedditAdapter._completeness(submission=False, has_body=True) == 1.0


@pytest.mark.unit
def test_reddit_classify_intent():
    from aegis.scrape.sources.reddit import RedditAdapter

    assert RedditAdapter._classify_intent("where can i buy this") == IntentType.PURCHASE
    assert RedditAdapter._classify_intent("just bought one yesterday") == IntentType.PURCHASE
    assert RedditAdapter._classify_intent("looking for a good keyboard") == IntentType.SEARCH
    assert RedditAdapter._classify_intent("this is so cool") == IntentType.ENGAGE


# ---------------------------------------------------------------------------
# Instagram (gated adapter)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_instagram_setup_raises_without_tos_flag():
    import asyncio

    from aegis.scrape.sources.instagram import InstagramAdapter, InstagramConfig

    adapter = InstagramAdapter(InstagramConfig(allow_red_tos=False))
    with pytest.raises(RuntimeError, match="allow_red_tos"):
        asyncio.run(adapter.setup(ctx()))


@pytest.mark.unit
def test_instagram_parse_basic():
    from aegis.scrape.sources.instagram import InstagramAdapter, InstagramConfig

    adapter = InstagramAdapter(InstagramConfig(allow_red_tos=True))
    # Simulate an instaloader Post-like object
    post = SimpleNamespace(
        shortcode="CxYz1234ABC",
        caption="Amazing summer collection #fashion",
        likes=5000,
        comments=120,
        is_video=False,
        date_utc=datetime(2024, 7, 1, 12, 0, 0),
        owner_profile=SimpleNamespace(
            userid="12345678",
            username="fashionbrand",
            followers=50000,
        ),
        tagged_users=[],
    )
    signal = adapter.parse(post, ctx())
    assert signal is not None
    assert signal.platform == Platform.INSTAGRAM
    assert signal.external_id == "CxYz1234ABC"
    assert signal.engagement.likes == 5000
    assert signal.modality == ContentModality.IMAGE


@pytest.mark.unit
def test_instagram_parse_video_post():
    from aegis.scrape.sources.instagram import InstagramAdapter, InstagramConfig

    adapter = InstagramAdapter(InstagramConfig(allow_red_tos=True))
    post = SimpleNamespace(
        shortcode="Reel1234",
        caption="Check this out!",
        likes=9999,
        comments=300,
        is_video=True,
        date_utc=datetime(2024, 8, 10, 0, 0, 0),
        owner_profile=None,
        tagged_users=[],
    )
    signal = adapter.parse(post, ctx())
    assert signal is not None
    assert signal.modality == ContentModality.VIDEO


@pytest.mark.unit
def test_instagram_parse_no_shortcode_returns_none():
    from aegis.scrape.sources.instagram import InstagramAdapter, InstagramConfig

    adapter = InstagramAdapter(InstagramConfig(allow_red_tos=True))
    post = SimpleNamespace(shortcode="", caption="test", likes=0, comments=0,
                           is_video=False, date_utc=None, owner_profile=None, tagged_users=[])
    assert adapter.parse(post, ctx()) is None


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tos_risk_ordering():
    assert ToSRisk.GREEN != ToSRisk.AMBER
    assert ToSRisk.AMBER != ToSRisk.RED


@pytest.mark.unit
def test_platform_enum_values():
    platforms = {p.value for p in Platform}
    assert "reddit" in platforms
    assert "amazon" in platforms
    assert "tiktok" in platforms


@pytest.mark.unit
def test_source_tier_enum():
    tiers = list(SourceTier)
    assert len(tiers) >= 5


@pytest.mark.unit
def test_content_modality_enum():
    assert ContentModality.TEXT != ContentModality.IMAGE
    assert ContentModality.VIDEO != ContentModality.AUDIO


@pytest.mark.unit
def test_intent_type_enum():
    intents = list(IntentType)
    assert IntentType.UNKNOWN in intents
    assert IntentType.PURCHASE in intents
    assert IntentType.SEARCH in intents


# ---------------------------------------------------------------------------
# Base adapter / ScrapeContext
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scrape_context_defaults():
    c = ScrapeContext()
    assert c.items_seen == 0
    assert c.items_emitted == 0
    assert c.profile is None


@pytest.mark.unit
def test_scrape_context_correlation_id_unique():
    c1 = ScrapeContext()
    c2 = ScrapeContext()
    assert c1.correlation_id != c2.correlation_id


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_config_loads_defaults(monkeypatch):
    """Settings loads with all defaults when env vars are not set."""
    # Ensure required vars have at least a placeholder
    monkeypatch.setenv("AEGIS_PG_DSN", "postgresql://localhost/test")
    monkeypatch.setenv("AEGIS_REDIS_URL", "redis://localhost:6379/0")
    from aegis.config import Settings
    s = Settings()
    assert s.pg_dsn_str.startswith("postgresql")
