"""Unit tests for aegis.schemas.signal and related enums."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from aegis.schemas.enums import (
    ContentModality,
    IntentType,
    Platform,
    ScrapeMethod,
    SourceTier,
    ToSRisk,
    platform_tier,
)
from aegis.schemas.signal import (
    Author,
    ConfidenceMetadata,
    EngagementMetrics,
    MediaRef,
    Price,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


def _provenance(**kw):
    return ScrapeProvenance(
        method=ScrapeMethod.OFFICIAL_API,
        scraped_at=_NOW,
        scraper_version="test-0.1.0",
        tos_risk=ToSRisk.GREEN,
        **kw,
    )


def _confidence(**kw):
    return ConfidenceMetadata(completeness=0.8, source_confidence=0.9, **kw)


def _make_signal(**kw):
    """Build a minimal valid ProductSignal (HN / Tier-5-alternative)."""
    defaults = {
        "platform": Platform.HACKER_NEWS,
        "tier": SourceTier.TIER_5_ALTERNATIVE,
        "external_id": "12345",
        "url": "https://news.ycombinator.com/item?id=12345",
        "title": "Test HN post",
        "raw_text": None,
        "modality": ContentModality.TEXT,
        "tags": frozenset(),
        "intent": IntentType.UNKNOWN,
        "provenance": _provenance(),
        "confidence": _confidence(),
    }
    defaults.update(kw)
    h = compute_content_hash(
        platform=defaults["platform"],
        external_id=defaults["external_id"],
        url=str(defaults["url"]) if defaults.get("url") else None,
        title=defaults.get("title"),
        raw_text=defaults.get("raw_text"),
        posted_at=defaults.get("posted_at"),
    )
    return ProductSignal(**defaults, content_hash=h)


# ---------------------------------------------------------------------------
# compute_content_hash
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compute_content_hash_deterministic():
    h1 = compute_content_hash(
        platform=Platform.HACKER_NEWS,
        external_id="99",
        url="https://example.com",
        title="hello",
        raw_text=None,
        posted_at=None,
    )
    h2 = compute_content_hash(
        platform=Platform.HACKER_NEWS,
        external_id="99",
        url="https://example.com",
        title="hello",
        raw_text=None,
        posted_at=None,
    )
    assert h1 == h2


@pytest.mark.unit
def test_compute_content_hash_different_for_different_inputs():
    h1 = compute_content_hash(
        platform=Platform.HACKER_NEWS,
        external_id="1",
        url=None,
        title=None,
        raw_text=None,
        posted_at=None,
    )
    h2 = compute_content_hash(
        platform=Platform.HACKER_NEWS,
        external_id="2",
        url=None,
        title=None,
        raw_text=None,
        posted_at=None,
    )
    assert h1 != h2


@pytest.mark.unit
def test_compute_content_hash_platform_matters():
    kw = {"external_id": "1", "url": None, "title": None, "raw_text": None, "posted_at": None}
    h_hn = compute_content_hash(platform=Platform.HACKER_NEWS, **kw)
    h_tt = compute_content_hash(platform=Platform.TIKTOK, **kw)
    assert h_hn != h_tt


# ---------------------------------------------------------------------------
# platform_tier
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_platform_tier_returns_correct_tier():
    assert platform_tier(Platform.HACKER_NEWS) == SourceTier.TIER_5_ALTERNATIVE
    assert platform_tier(Platform.AMAZON) == SourceTier.TIER_3_SEARCH
    assert platform_tier(Platform.GOOGLE_TRENDS) == SourceTier.TIER_3_SEARCH


# ---------------------------------------------------------------------------
# EngagementMetrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_engagement_total_engagements():
    eng = EngagementMetrics(likes=10, comments=5, shares=2, saves=1)
    assert eng.total_engagements == 18


@pytest.mark.unit
def test_engagement_rate_returns_none_when_no_views():
    eng = EngagementMetrics(likes=10)
    assert eng.engagement_rate is None


@pytest.mark.unit
def test_engagement_rate_computed():
    eng = EngagementMetrics(views=100, likes=10, comments=2)
    assert eng.engagement_rate == pytest.approx(0.12)


@pytest.mark.unit
def test_engagement_all_none():
    eng = EngagementMetrics()
    assert eng.total_engagements == 0
    assert eng.engagement_rate is None


# ---------------------------------------------------------------------------
# Price
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_price_discount_ratio():
    p = Price(amount=Decimal("75.00"), currency="USD", original_amount=Decimal("100.00"))
    assert p.discount_ratio == pytest.approx(0.25)


@pytest.mark.unit
def test_price_discount_ratio_none_when_no_original():
    p = Price(amount=Decimal("10.00"), currency="USD")
    assert p.discount_ratio is None


@pytest.mark.unit
def test_price_invalid_currency_rejected():
    with pytest.raises(Exception):
        Price(amount=Decimal("10.00"), currency="dollar")


# ---------------------------------------------------------------------------
# Author
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_author_minimal():
    a = Author(platform_user_id="uid123")
    assert a.handle is None
    assert a.follower_count is None


@pytest.mark.unit
def test_author_full():
    a = Author(
        platform_user_id="uid123",
        handle="testuser",
        display_name="Test User",
        follower_count=1000,
    )
    assert a.follower_count == 1000


# ---------------------------------------------------------------------------
# ScrapeProvenance
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_provenance_requires_utc():
    p = _provenance()
    assert p.scraped_at.tzinfo is not None


# ---------------------------------------------------------------------------
# ProductSignal construction
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_product_signal_basic():
    s = _make_signal()
    assert isinstance(s.signal_id, UUID)
    assert s.platform == Platform.HACKER_NEWS
    assert s.tier == SourceTier.TIER_5_ALTERNATIVE


@pytest.mark.unit
def test_product_signal_tier_mismatch_rejected():
    h = compute_content_hash(
        platform=Platform.HACKER_NEWS,
        external_id="1",
        url=None,
        title=None,
        raw_text=None,
        posted_at=None,
    )
    with pytest.raises(Exception, match="tier mismatch"):
        ProductSignal(
            platform=Platform.HACKER_NEWS,
            tier=SourceTier.TIER_1_INTENT,
            external_id="1",
            modality=ContentModality.TEXT,
            provenance=_provenance(),
            confidence=_confidence(),
            content_hash=h,
        )


@pytest.mark.unit
def test_product_signal_content_hash_mismatch_rejected():
    with pytest.raises(Exception, match="content_hash mismatch"):
        ProductSignal(
            platform=Platform.HACKER_NEWS,
            tier=SourceTier.TIER_5_ALTERNATIVE,
            external_id="1",
            modality=ContentModality.TEXT,
            provenance=_provenance(),
            confidence=_confidence(),
            content_hash="a" * 32,
        )


@pytest.mark.unit
def test_product_signal_commerce_requires_price():
    # Use EBAY (TIER_2_COMMERCE) to test the price-required validator
    h = compute_content_hash(
        platform=Platform.EBAY,
        external_id="ITEM123",
        url="https://www.ebay.com/itm/ITEM123",
        title="Widget",
        raw_text=None,
        posted_at=None,
    )
    with pytest.raises(Exception, match="price"):
        ProductSignal(
            platform=Platform.EBAY,
            tier=SourceTier.TIER_2_COMMERCE,
            external_id="ITEM123",
            url="https://www.ebay.com/itm/ITEM123",
            title="Widget",
            modality=ContentModality.STRUCTURED,
            intent=IntentType.PURCHASE,
            provenance=_provenance(),
            confidence=_confidence(),
            content_hash=h,
        )


@pytest.mark.unit
def test_product_signal_commerce_with_price_ok():
    h = compute_content_hash(
        platform=Platform.EBAY,
        external_id="ITEM123",
        url="https://www.ebay.com/itm/ITEM123",
        title="Widget",
        raw_text=None,
        posted_at=None,
    )
    s = ProductSignal(
        platform=Platform.EBAY,
        tier=SourceTier.TIER_2_COMMERCE,
        external_id="ITEM123",
        url="https://www.ebay.com/itm/ITEM123",
        title="Widget",
        modality=ContentModality.STRUCTURED,
        intent=IntentType.PURCHASE,
        price=Price(amount=Decimal("9.99"), currency="USD"),
        provenance=_provenance(),
        confidence=_confidence(),
        content_hash=h,
    )
    assert s.price.amount == Decimal("9.99")


@pytest.mark.unit
def test_product_signal_posted_at_naive_rejected():
    naive = datetime(2024, 1, 1)  # no tzinfo
    h = compute_content_hash(
        platform=Platform.HACKER_NEWS,
        external_id="1",
        url=None,
        title=None,
        raw_text=None,
        posted_at=None,
    )
    with pytest.raises(Exception):
        ProductSignal(
            platform=Platform.HACKER_NEWS,
            tier=SourceTier.TIER_5_ALTERNATIVE,
            external_id="1",
            modality=ContentModality.TEXT,
            posted_at=naive,
            provenance=_provenance(),
            confidence=_confidence(),
            content_hash=h,
        )


@pytest.mark.unit
def test_product_signal_with_hash_recomputes():
    s = _make_signal()
    # with_hash on an already-correct hash returns self
    s2 = s.with_hash()
    assert s2.content_hash == s.content_hash


@pytest.mark.unit
def test_product_signal_tags_are_frozenset():
    s = _make_signal()
    assert isinstance(s.tags, frozenset)


@pytest.mark.unit
def test_product_signal_media_tuple():
    media = (
        MediaRef(
            url="https://example.com/img.jpg",
            modality=ContentModality.IMAGE,
            width_px=800,
            height_px=600,
        ),
    )
    s = _make_signal(media=media, modality=ContentModality.IMAGE)
    assert len(s.media) == 1
    assert s.media[0].width_px == 800


# ---------------------------------------------------------------------------
# Adapter parse methods (no network, no I/O)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hacker_news_adapter_parse():
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    adapter = HackerNewsAdapter(HackerNewsConfig())
    from aegis.scrape.base import ScrapeContext

    ctx = ScrapeContext()

    raw = {
        "objectID": "40000001",
        "title": "Ask HN: What's trending?",
        "url": "https://example.com/trending",
        "points": 150,
        "num_comments": 42,
        "author": "testuser",
        "created_at_i": 1705312800,
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.HACKER_NEWS
    assert signal.external_id == "40000001"
    assert signal.title is not None


@pytest.mark.unit
def test_hacker_news_adapter_parse_missing_id_returns_none():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    adapter = HackerNewsAdapter(HackerNewsConfig())
    ctx = ScrapeContext()

    signal = adapter.parse({}, ctx)
    assert signal is None


@pytest.mark.unit
def test_tiktok_adapter_parse_hashtag():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.tiktok import TikTokAdapter, TikTokConfig

    adapter = TikTokAdapter(TikTokConfig())
    ctx = ScrapeContext()

    raw = {
        "type": "hashtag",
        "hashtag_id": "ht999",
        "hashtag_name": "TrendingNow",
        "video_views": 1_000_000,
        "publish_cnt": 500,
        "rank": 1,
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.TIKTOK
    assert "trendingnow" in signal.tags


@pytest.mark.unit
def test_tiktok_adapter_parse_video():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.tiktok import TikTokAdapter, TikTokConfig

    adapter = TikTokAdapter(TikTokConfig())
    ctx = ScrapeContext()

    raw = {
        "type": "video",
        "item_id": "vid123456",
        "author_name": "creator",
        "author_id": "aid001",
        "video_description": "Amazing product #fyp",
        "play_count": 500000,
        "digg_count": 10000,
        "comment_count": 200,
        "share_count": 50,
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.modality == ContentModality.VIDEO
    assert signal.engagement.views == 500000


@pytest.mark.unit
def test_pinterest_adapter_parse():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.pinterest import PinterestAdapter, PinterestConfig

    adapter = PinterestAdapter(PinterestConfig())
    ctx = ScrapeContext()

    raw = {
        "id": "pin12345",
        "description": "Beautiful home decor ideas",
        "title": "Home Decor",
        "aggregated_pin_data": {"saves": 250},
        "comment_count": 15,
        "created_at": "Fri, 01 Mar 2024 10:00:00 +0000",
        "pinner": {
            "id": "user99",
            "username": "homedesigner",
            "full_name": "Home Designer",
            "follower_count": 5000,
        },
        "images": {
            "orig": {
                "url": "https://i.pinimg.com/originals/img.jpg",
                "width": 736,
                "height": 1104,
            }
        },
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.PINTEREST
    assert signal.author is not None
    assert signal.author.handle == "homedesigner"
    assert signal.engagement.saves == 250


@pytest.mark.unit
def test_amazon_adapter_parse():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.amazon import AmazonAdapter, AmazonConfig
    from aegis.schemas.enums import SourceTier

    adapter = AmazonAdapter(AmazonConfig())
    ctx = ScrapeContext()

    # New format: rank + category from bestsellers HTML scrape (no price)
    raw = {
        "asin": "B08N5WRWNW",
        "title": "Echo Dot 4th Gen",
        "url": "https://www.amazon.com/dp/B08N5WRWNW",
        "rank": 1,
        "category": "electronics",
    }
    signal = adapter.parse(raw, ctx)
    assert signal is not None
    assert signal.platform == Platform.AMAZON
    assert signal.tier == SourceTier.TIER_3_SEARCH
    assert signal.price is None
    assert signal.platform_specific["rank"] == 1
    assert signal.platform_specific["category"] == "electronics"
    assert signal.engagement.likes == 100  # 101 - rank(1)


@pytest.mark.unit
def test_amazon_adapter_parse_no_asin_returns_none():
    from aegis.scrape.base import ScrapeContext
    from aegis.scrape.sources.amazon import AmazonAdapter, AmazonConfig

    adapter = AmazonAdapter(AmazonConfig())
    ctx = ScrapeContext()

    signal = adapter.parse({"title": "Orphaned"}, ctx)
    assert signal is None
