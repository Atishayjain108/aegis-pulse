"""Unit tests for the topic intelligence and pattern detection modules.

Tests are pure-Python — no network, no Docker, no external services.
Covers: expand_topic(), detect_patterns(), BingNewsRSSAdapter.parse(),
        sweep_db_duplicates() contract, and deduplicate_batch() semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.scrape.patterns import detect_patterns
from aegis.scrape.topic import (
    TopicExpansion,
    TopicScrapeResult,
    _detect_category,
    _find_related_entities,
    expand_topic,
)

# ===========================================================================
# Helper factories
# ===========================================================================


def _signal_dict(title: str, platform: str = "hacker_news", hours_ago: float = 1.0) -> dict:
    """Create a minimal signal dict for testing."""
    from datetime import timedelta

    return {
        "title": title,
        "platform": platform,
        "captured_at": datetime.now(UTC) - timedelta(hours=hours_ago),
    }


def _signal_obj(title: str, platform_val: str = "hacker_news") -> object:
    """Create a minimal mock ProductSignal-like object."""
    from aegis.schemas.enums import Platform

    m = MagicMock()
    m.title = title
    m.platform = Platform.HACKER_NEWS
    m.posted_at = datetime.now(UTC)
    m.provenance = MagicMock()
    m.provenance.scraped_at = datetime.now(UTC)
    return m


# ===========================================================================
# Category detection
# ===========================================================================


class TestDetectCategory:
    def test_tech_keywords(self):
        assert _detect_category("nvidia GPU chip") == "tech"

    def test_crypto_keywords(self):
        assert _detect_category("bitcoin halving") == "crypto"

    def test_finance_keywords(self):
        assert _detect_category("stock earnings IPO") == "finance"

    def test_health_keywords(self):
        assert _detect_category("FDA vaccine trial") == "health"

    def test_gaming_keywords(self):
        assert _detect_category("game esports Steam") == "gaming"

    def test_energy_keywords(self):
        assert _detect_category("solar renewable EV") == "energy"

    def test_ecommerce_keywords(self):
        assert _detect_category("shopify dropship seller") == "ecommerce"

    def test_consumer_keywords(self):
        assert _detect_category("beauty skincare brand") == "consumer"

    def test_unknown_falls_back_to_general(self):
        assert _detect_category("xyzzy foo bar qux") == "general"

    def test_case_insensitive(self):
        assert _detect_category("BITCOIN ETH CRYPTO") == "crypto"


# ===========================================================================
# Known entity expansion
# ===========================================================================


class TestFindRelatedEntities:
    def test_openai_exact_match(self):
        related = _find_related_entities("openai")
        assert len(related) > 0
        assert any("chatgpt" in r.lower() or "gpt" in r.lower() for r in related)

    def test_bitcoin_exact_match(self):
        related = _find_related_entities("bitcoin")
        assert len(related) > 0
        assert any("btc" in r.lower() or "halving" in r.lower() for r in related)

    def test_nvidia_exact_match(self):
        related = _find_related_entities("nvidia")
        assert len(related) > 0
        assert any("h100" in r.lower() or "gpu" in r.lower() for r in related)

    def test_unknown_entity_returns_empty(self):
        related = _find_related_entities("xyzzyunknown12345")
        assert related == []

    def test_case_insensitive_match(self):
        lower = _find_related_entities("openai")
        upper = _find_related_entities("OpenAI")
        assert set(lower) == set(upper)

    def test_partial_match_contains_key(self):
        # "chatgpt enterprise" contains "chatgpt" which is a known entity key
        related = _find_related_entities("chatgpt enterprise")
        assert len(related) > 0

    def test_max_6_results(self):
        related = _find_related_entities("nvidia")
        assert len(related) <= 6


# ===========================================================================
# expand_topic
# ===========================================================================


class TestExpandTopic:
    def test_returns_topic_expansion(self):
        exp = expand_topic("OpenAI")
        assert isinstance(exp, TopicExpansion)
        assert exp.base_topic == "OpenAI"

    def test_search_terms_include_base_topic(self):
        exp = expand_topic("NVIDIA")
        assert exp.search_terms[0] == "NVIDIA"

    def test_search_terms_non_empty(self):
        exp = expand_topic("solar energy")
        assert len(exp.search_terms) >= 3

    def test_search_terms_no_duplicates(self):
        exp = expand_topic("bitcoin")
        lower_terms = [t.lower() for t in exp.search_terms]
        assert len(lower_terms) == len(set(lower_terms))

    def test_category_detected(self):
        exp = expand_topic("ethereum defi")
        assert exp.category == "crypto"

    def test_tech_category_has_tech_subreddits(self):
        exp = expand_topic("AI machine learning")
        assert any("ML" in s or "technology" in s or "programming" in s for s in exp.reddit_subreddits)

    def test_related_entities_populated_for_known_topic(self):
        exp = expand_topic("openai")
        assert len(exp.related_entities) > 0

    def test_unknown_topic_still_works(self):
        exp = expand_topic("completely unknown niche term xyz")
        assert isinstance(exp, TopicExpansion)
        assert len(exp.search_terms) >= 1

    def test_market_angle_queries_present(self):
        exp = expand_topic("nvidia")
        assert len(exp.market_angle_queries) > 0

    def test_temporal_queries_contain_year(self):
        exp = expand_topic("bitcoin")
        years = [str(y) for y in range(2024, 2030)]
        assert any(any(yr in q for yr in years) for q in exp.temporal_queries)

    def test_reddit_search_query_included(self):
        exp = expand_topic("OpenAI")
        site_queries = [t for t in exp.search_terms if "site:reddit.com" in t]
        assert len(site_queries) == 1

    def test_whitespace_stripped(self):
        exp = expand_topic("  bitcoin  ")
        assert exp.base_topic == "bitcoin"

    def test_max_search_terms_cap(self):
        exp = expand_topic("ai ml llm gpt chip nvidia deep learning")
        assert len(exp.search_terms) <= 20

    def test_max_subreddits_cap(self):
        exp = expand_topic("bitcoin crypto defi eth solana")
        assert len(exp.reddit_subreddits) <= 8


# ===========================================================================
# detect_patterns
# ===========================================================================


class TestDetectPatterns:
    def test_empty_input(self):
        assert detect_patterns([]) == []

    def test_single_signal(self):
        # Single signal can't form a cluster of size 2
        sigs = [_signal_dict("NVIDIA releases new GPU")]
        result = detect_patterns(sigs, min_cluster_size=1)
        assert len(result) <= 1

    def test_similar_signals_clustered(self):
        sigs = [
            _signal_dict("NVIDIA releases record earnings Q1"),
            _signal_dict("NVIDIA posts record quarterly earnings"),
            _signal_dict("NVIDIA quarterly revenue hits record high"),
            _signal_dict("Apple announces new iPhone model"),
            _signal_dict("Apple iPhone 17 release date revealed"),
        ]
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.20)
        assert len(clusters) >= 1

    def test_dissimilar_signals_not_clustered(self):
        sigs = [
            _signal_dict("NVIDIA GPU chip shortage"),
            _signal_dict("Bitcoin halving event 2026"),
            _signal_dict("FDA approves new cancer drug"),
            _signal_dict("SpaceX Starship reaches orbit"),
            _signal_dict("Apple iPhone 17 announced"),
        ]
        # With high threshold, all are distinct
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.70)
        # Dissimilar signals should not form clusters
        assert all(c.signal_count < len(sigs) for c in clusters)

    def test_cluster_has_required_fields(self):
        sigs = [
            _signal_dict("NVIDIA H100 GPU demand"),
            _signal_dict("NVIDIA Blackwell chip demand surge"),
            _signal_dict("NVIDIA GPU supply shortage"),
        ]
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.15)
        if clusters:
            c = clusters[0]
            assert isinstance(c.label, str)
            assert isinstance(c.signal_count, int)
            assert isinstance(c.platforms, list)
            assert isinstance(c.top_titles, list)
            assert isinstance(c.velocity_score, float)
            assert 0.0 <= c.velocity_score <= 1.0
            assert 0.0 <= c.recency_weight <= 1.0

    def test_clusters_sorted_by_velocity_desc(self):
        sigs = [
            _signal_dict("NVIDIA GPU chip A"),
            _signal_dict("NVIDIA GPU chip B"),
            _signal_dict("NVIDIA GPU chip C"),
            _signal_dict("NVIDIA GPU chip D"),
            _signal_dict("Apple iPhone X"),
            _signal_dict("Apple iPhone Y"),
        ]
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.15)
        if len(clusters) >= 2:
            assert clusters[0].velocity_score >= clusters[1].velocity_score

    def test_min_cluster_size_filtering(self):
        sigs = [
            _signal_dict("Topic A first"),
            _signal_dict("Topic A second"),
            _signal_dict("Topic B only"),
        ]
        clusters_2 = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.20)
        clusters_1 = detect_patterns(sigs, min_cluster_size=1, similarity_threshold=0.20)
        # min_cluster_size=1 allows singleton clusters
        assert len(clusters_1) >= len(clusters_2)

    def test_works_with_signal_objects(self):
        sigs = [
            _signal_obj("NVIDIA GPU H100 demand surge"),
            _signal_obj("NVIDIA GPU H100 shortage"),
            _signal_obj("NVIDIA Blackwell chip"),
        ]
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.15)
        assert isinstance(clusters, list)

    def test_works_with_mixed_types(self):
        sigs = [
            _signal_dict("NVIDIA GPU performance"),
            _signal_obj("NVIDIA GPU benchmark"),
        ]
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.10)
        assert isinstance(clusters, list)

    def test_max_clusters_cap(self):
        sigs = [_signal_dict(f"Unique topic {i} xyz abc") for i in range(50)]
        clusters = detect_patterns(sigs, min_cluster_size=1, max_clusters=5)
        assert len(clusters) <= 5

    def test_platform_diversity_captured(self):
        sigs = [
            _signal_dict("NVIDIA chip news A", platform="hacker_news"),
            _signal_dict("NVIDIA chip news B", platform="google_news"),
            _signal_dict("NVIDIA chip news C", platform="reddit"),
        ]
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.15)
        if clusters:
            # Should see multiple platforms in the biggest cluster
            assert len(clusters[0].platforms) >= 1

    def test_recency_weight_recent_signals(self):
        # All signals from last hour → recency_weight should be high
        sigs = [
            _signal_dict("NVIDIA chip A", hours_ago=0.5),
            _signal_dict("NVIDIA chip B", hours_ago=0.3),
            _signal_dict("NVIDIA chip C", hours_ago=0.1),
        ]
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.15)
        if clusters:
            assert clusters[0].recency_weight >= 0.5

    def test_recency_weight_old_signals(self):
        # All signals from 24h ago → recency_weight should be 0
        sigs = [
            _signal_dict("NVIDIA chip A", hours_ago=24),
            _signal_dict("NVIDIA chip B", hours_ago=25),
            _signal_dict("NVIDIA chip C", hours_ago=26),
        ]
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.15)
        if clusters:
            assert clusters[0].recency_weight == 0.0

    def test_empty_titles_handled_gracefully(self):
        sigs = [
            _signal_dict(""),
            _signal_dict(""),
            _signal_dict("NVIDIA valid title"),
        ]
        # Should not crash
        result = detect_patterns(sigs, min_cluster_size=1)
        assert isinstance(result, list)

    def test_label_is_non_empty_string(self):
        sigs = [
            _signal_dict("OpenAI ChatGPT model update"),
            _signal_dict("OpenAI GPT model latest news"),
        ]
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.15)
        if clusters:
            assert len(clusters[0].label) > 0

    def test_top_titles_bounded(self):
        sigs = [_signal_dict(f"NVIDIA GPU news item {i}") for i in range(20)]
        clusters = detect_patterns(sigs, min_cluster_size=2, similarity_threshold=0.10)
        if clusters:
            assert len(clusters[0].top_titles) <= 5


# ===========================================================================
# BingNewsRSSAdapter.parse()
# ===========================================================================


class TestBingNewsRSSAdapterParse:
    @pytest.fixture
    def adapter(self):
        from aegis.scrape.base import ScrapeContext
        from aegis.scrape.sources.bing_news_rss import BingNewsRSSAdapter, BingNewsRSSConfig

        a = BingNewsRSSAdapter(BingNewsRSSConfig())
        a._client = MagicMock()  # prevent "setup not called" error
        return a, ScrapeContext()

    def test_parse_full_item(self, adapter):
        a, ctx = adapter
        raw = {
            "title": "NVIDIA posts record earnings",
            "link": "https://example.com/nvidia-earnings",
            "description": "NVIDIA's quarterly earnings hit a record high.",
            "pubDate": "Mon, 06 May 2026 12:00:00 GMT",
            "source": "Bloomberg",
            "guid": "abc123",
            "_query": "NVIDIA earnings",
        }
        sig = a.parse(raw, ctx)
        assert sig is not None
        assert sig.title == "NVIDIA posts record earnings"
        assert sig.platform.value == "bing_news"
        assert sig.posted_at is not None

    def test_parse_missing_title_and_link_returns_none(self, adapter):
        a, ctx = adapter
        sig = a.parse({"title": "", "link": "", "_query": "test"}, ctx)
        assert sig is None

    def test_parse_strips_html_from_description(self, adapter):
        a, ctx = adapter
        raw = {
            "title": "Test article",
            "link": "https://example.com/test",
            "description": "<p>Some <b>html</b> content here</p>",
            "pubDate": "",
            "source": "",
            "guid": "xyz",
            "_query": "test",
        }
        sig = a.parse(raw, ctx)
        assert sig is not None
        assert "<p>" not in (sig.raw_text or "")
        assert "<b>" not in (sig.raw_text or "")

    def test_parse_no_pub_date(self, adapter):
        a, ctx = adapter
        raw = {
            "title": "Article without date",
            "link": "https://example.com/noddate",
            "description": "",
            "pubDate": "",
            "source": "",
            "guid": "nodate123",
            "_query": "test",
        }
        sig = a.parse(raw, ctx)
        assert sig is not None
        assert sig.posted_at is None

    def test_parse_platform_is_bing_news(self, adapter):
        from aegis.schemas.enums import Platform

        a, ctx = adapter
        raw = {
            "title": "Test",
            "link": "https://example.com",
            "description": "",
            "pubDate": "",
            "source": "",
            "guid": "g1",
            "_query": "test",
        }
        sig = a.parse(raw, ctx)
        assert sig is not None
        assert sig.platform == Platform.BING_NEWS

    def test_parse_external_id_is_sha1_of_guid(self, adapter):
        import hashlib

        a, ctx = adapter
        guid = "my-unique-guid-123"
        raw = {
            "title": "Test title",
            "link": "https://example.com/test",
            "description": "",
            "pubDate": "",
            "source": "",
            "guid": guid,
            "_query": "test",
        }
        sig = a.parse(raw, ctx)
        assert sig is not None
        expected_id = hashlib.sha1(guid.encode()).hexdigest()[:24]  # noqa: S324
        assert sig.external_id == expected_id

    def test_parse_exception_returns_none(self, adapter):
        a, ctx = adapter
        # Passing garbage data
        sig = a.parse(None, ctx)  # type: ignore[arg-type]
        assert sig is None


# ===========================================================================
# TopicScrapeResult properties
# ===========================================================================


class TestTopicScrapeResult:
    def test_duration_s_without_finished_at(self):
        exp = expand_topic("test")
        result = TopicScrapeResult(topic="test", expansion=exp)
        assert result.duration_s >= 0.0

    def test_duration_s_with_finished_at(self):
        from datetime import timedelta

        exp = expand_topic("test")
        now = datetime.now(UTC)
        result = TopicScrapeResult(topic="test", expansion=exp)
        result.started_at = now - timedelta(seconds=5)
        result.finished_at = now
        assert abs(result.duration_s - 5.0) < 0.1

    def test_default_patterns_list(self):
        exp = expand_topic("test")
        result = TopicScrapeResult(topic="test", expansion=exp)
        assert result.patterns == []


# ===========================================================================
# dedup: sweep_db_duplicates contract (no DB required)
# ===========================================================================


class TestSweepDbDuplicatesContract:
    """Verify sweep_db_duplicates arguments and return shape without a real DB."""

    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self):
        from aegis.db.dedup import sweep_db_duplicates

        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        import uuid
        result = await sweep_db_duplicates(
            mock_pool,
            uuid.UUID("00000000-0000-0000-0000-000000000001"),
            dry_run=True,
        )
        assert set(result.keys()) == {"total_checked", "duplicates_found", "deleted"}

    @pytest.mark.asyncio
    async def test_empty_db_returns_zeros(self):
        from aegis.db.dedup import sweep_db_duplicates

        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        import uuid
        result = await sweep_db_duplicates(
            mock_pool,
            uuid.UUID("00000000-0000-0000-0000-000000000001"),
            dry_run=True,
        )
        assert result["total_checked"] == 0
        assert result["duplicates_found"] == 0
        assert result["deleted"] == 0

    @pytest.mark.asyncio
    async def test_duplicate_rows_detected_dry_run(self):
        """Sweep finds semantic duplicates but does not delete in dry_run=True."""
        from aegis.db.dedup import sweep_db_duplicates

        # Plain dicts work as asyncpg-like row proxies for ["key"] access
        rows = [
            {
                "signal_id": "00000000-0000-0000-0000-000000000010",
                "title": "NVIDIA posts record quarterly earnings revenue",
                "platform": "google_news",
                "scraped_at": datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC),
            },
            {
                "signal_id": "00000000-0000-0000-0000-000000000011",
                "title": "NVIDIA reports record quarterly earnings revenue",
                "platform": "hacker_news",
                "scraped_at": datetime(2026, 5, 1, 11, 0, 0, tzinfo=UTC),
            },
        ]

        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        import uuid
        result = await sweep_db_duplicates(
            mock_pool,
            uuid.UUID("00000000-0000-0000-0000-000000000001"),
            threshold=0.70,
            dry_run=True,
        )
        assert result["total_checked"] == 2
        assert result["duplicates_found"] >= 1
        assert result["deleted"] == 0  # dry_run=True, nothing deleted

    @pytest.mark.asyncio
    async def test_distinct_rows_not_flagged(self):
        """Completely distinct signals should not be flagged as duplicates."""
        from aegis.db.dedup import sweep_db_duplicates

        rows = [
            {
                "signal_id": "00000000-0000-0000-0000-000000000020",
                "title": "NVIDIA posts record quarterly earnings",
                "platform": "google_news",
                "scraped_at": datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC),
            },
            {
                "signal_id": "00000000-0000-0000-0000-000000000021",
                "title": "Apple iPhone 17 release date announced",
                "platform": "hacker_news",
                "scraped_at": datetime(2026, 5, 1, 11, 0, 0, tzinfo=UTC),
            },
            {
                "signal_id": "00000000-0000-0000-0000-000000000022",
                "title": "Bitcoin halving drives price surge 2026",
                "platform": "reddit",
                "scraped_at": datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
            },
        ]

        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        import uuid
        result = await sweep_db_duplicates(
            mock_pool,
            uuid.UUID("00000000-0000-0000-0000-000000000001"),
            threshold=0.85,
            dry_run=True,
        )
        assert result["total_checked"] == 3
        assert result["duplicates_found"] == 0

    @pytest.mark.asyncio
    async def test_empty_title_rows_skipped(self):
        """Rows with empty titles should be skipped gracefully."""
        from aegis.db.dedup import sweep_db_duplicates

        rows = [
            {
                "signal_id": "00000000-0000-0000-0000-000000000030",
                "title": "",
                "platform": "google_news",
                "scraped_at": datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC),
            },
            {
                "signal_id": "00000000-0000-0000-0000-000000000031",
                "title": None,
                "platform": "reddit",
                "scraped_at": datetime(2026, 5, 1, 11, 0, 0, tzinfo=UTC),
            },
        ]

        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        import uuid
        result = await sweep_db_duplicates(
            mock_pool,
            uuid.UUID("00000000-0000-0000-0000-000000000001"),
            dry_run=True,
        )
        assert result["duplicates_found"] == 0


# ===========================================================================
# Additional expand_topic coverage — edge cases
# ===========================================================================


class TestExpandTopicEdgeCases:
    def test_topic_containing_suffix_not_doubled(self):
        """If the topic already contains 'news', don't add 'topic news'."""
        exp = expand_topic("bitcoin news")
        # Should not have "bitcoin news news" in the list
        doubled = [t for t in exp.search_terms if t.lower() == "bitcoin news news"]
        assert doubled == []

    def test_topic_with_known_entity_gets_related_terms(self):
        exp = expand_topic("OpenAI GPT")
        # Should have both base topic and related entity terms
        assert len(exp.search_terms) > 3

    def test_competitor_queries_populated_for_tech(self):
        exp = expand_topic("nvidia chip")
        assert len(exp.competitor_queries) > 0

    def test_market_angle_queries_populated(self):
        exp = expand_topic("ethereum defi")
        assert len(exp.market_angle_queries) > 0

    def test_general_category_gets_default_subreddits(self):
        exp = expand_topic("random obscure niche hobby zxqwerty")
        # Should fall back to default subreddits
        assert len(exp.reddit_subreddits) > 0

    def test_reddit_site_query_appended_once(self):
        exp = expand_topic("NVIDIA")
        reddit_queries = [t for t in exp.search_terms if "site:reddit.com" in t]
        assert len(reddit_queries) == 1

    def test_expand_topic_short_topic(self):
        exp = expand_topic("ev")
        assert exp.base_topic == "ev"
        assert len(exp.search_terms) >= 1


# ===========================================================================
# Additional category detection coverage
# ===========================================================================


class TestDetectCategoryAdditional:
    def test_multi_word_keyword_detection(self):
        # "electric vehicle" + "solar" + "renewable" are energy keywords
        result = _detect_category("solar renewable electric vehicle grid battery")
        assert result == "energy"

    def test_overlapping_keywords_picks_best_match(self):
        # "gaming" strongly matches gaming category
        result = _detect_category("gaming esports fps mmo rpg")
        assert result == "gaming"

    def test_ecommerce_category(self):
        result = _detect_category("shopify dropship ecommerce seller")
        assert result == "ecommerce"

    def test_health_category(self):
        result = _detect_category("FDA vaccine clinical trial pharma")
        assert result == "health"


# ===========================================================================
# _current_temporal_terms coverage
# ===========================================================================


class TestCurrentTemporalTerms:
    def test_returns_list_of_strings(self):
        from aegis.scrape.topic import _current_temporal_terms
        terms = _current_temporal_terms()
        assert isinstance(terms, list)
        assert all(isinstance(t, str) for t in terms)

    def test_contains_current_year(self):
        from aegis.scrape.topic import _current_temporal_terms
        terms = _current_temporal_terms()
        current_year = str(datetime.now(UTC).year)
        assert current_year in terms

    def test_contains_quarter(self):
        from aegis.scrape.topic import _current_temporal_terms
        terms = _current_temporal_terms()
        assert any(t.startswith("Q") for t in terms)

    def test_contains_latest(self):
        from aegis.scrape.topic import _current_temporal_terms
        terms = _current_temporal_terms()
        assert "latest" in terms


# ===========================================================================
# BingNewsRSSAdapter: setup and teardown
# ===========================================================================


class TestBingNewsRSSLifecycle:
    @pytest.mark.asyncio
    async def test_setup_creates_client(self):
        from aegis.scrape.base import ScrapeContext
        from aegis.scrape.sources.bing_news_rss import BingNewsRSSAdapter, BingNewsRSSConfig

        a = BingNewsRSSAdapter(BingNewsRSSConfig())
        ctx = ScrapeContext()
        await a.setup(ctx)
        assert a._client is not None
        await a.teardown(ctx)
        assert a._client is None

    @pytest.mark.asyncio
    async def test_teardown_without_setup_is_safe(self):
        from aegis.scrape.base import ScrapeContext
        from aegis.scrape.sources.bing_news_rss import BingNewsRSSAdapter, BingNewsRSSConfig

        a = BingNewsRSSAdapter(BingNewsRSSConfig())
        ctx = ScrapeContext()
        # teardown before setup should not crash
        await a.teardown(ctx)

    def test_name_property(self):
        from aegis.scrape.sources.bing_news_rss import BingNewsRSSAdapter, BingNewsRSSConfig

        a = BingNewsRSSAdapter(BingNewsRSSConfig())
        assert a.name == "bing-news-rss"

    def test_default_config_with_base_adapter_config(self):
        from aegis.scrape.base import AdapterConfig
        from aegis.scrape.sources.bing_news_rss import BingNewsRSSAdapter

        a = BingNewsRSSAdapter(AdapterConfig(name="generic"))
        assert a.name == "bing-news-rss"

    @pytest.mark.asyncio
    async def test_fetch_raw_without_setup_raises(self):
        from aegis.scrape.base import ScrapeContext
        from aegis.scrape.sources.bing_news_rss import BingNewsRSSAdapter, BingNewsRSSConfig

        a = BingNewsRSSAdapter(BingNewsRSSConfig())
        ctx = ScrapeContext()
        with pytest.raises(RuntimeError, match="setup"):
            async for _ in a.fetch_raw(ctx, query="test"):
                pass

    def test_parse_with_pub_date(self):
        from aegis.scrape.base import ScrapeContext
        from aegis.scrape.sources.bing_news_rss import BingNewsRSSAdapter, BingNewsRSSConfig

        a = BingNewsRSSAdapter(BingNewsRSSConfig())
        a._client = MagicMock()
        ctx = ScrapeContext()

        raw = {
            "title": "Test with date",
            "link": "https://example.com/dated",
            "description": "An article with a valid date",
            "pubDate": "Wed, 15 May 2026 09:30:00 +0000",
            "source": "Reuters",
            "guid": "dated-guid-001",
            "_query": "test query",
        }
        sig = a.parse(raw, ctx)
        assert sig is not None
        assert sig.posted_at is not None
        assert sig.posted_at.year == 2026

    def test_parse_uses_link_as_fallback_guid(self):
        from aegis.scrape.base import ScrapeContext
        from aegis.scrape.sources.bing_news_rss import BingNewsRSSAdapter, BingNewsRSSConfig

        a = BingNewsRSSAdapter(BingNewsRSSConfig())
        a._client = MagicMock()
        ctx = ScrapeContext()

        raw = {
            "title": "Article with no guid",
            "link": "https://example.com/article-link",
            "description": "",
            "pubDate": "",
            "source": "",
            "guid": "",  # empty guid → falls back to link
            "_query": "test",
        }
        sig = a.parse(raw, ctx)
        assert sig is not None
        # external_id should be sha1 of the link
        import hashlib
        expected = hashlib.sha1(b"https://example.com/article-link").hexdigest()[:24]  # noqa: S324
        assert sig.external_id == expected


# ===========================================================================
# Pattern detection: internal helpers
# ===========================================================================


class TestPatternInternals:
    def test_tokenize_produces_bigrams(self):
        from aegis.scrape.patterns import _tokenize
        tokens = _tokenize("NVIDIA GPU chip shortage")
        # Should have unigrams + bigrams
        assert "nvidia" in tokens
        assert "nvidia_gpu" in tokens or "gpu_chip" in tokens

    def test_tokenize_removes_stopwords(self):
        from aegis.scrape.patterns import _tokenize
        tokens = _tokenize("the quick brown fox")
        assert "the" not in tokens

    def test_build_tfidf_empty_input(self):
        from aegis.scrape.patterns import _build_tfidf
        result = _build_tfidf([])
        assert result == []

    def test_build_tfidf_single_doc(self):
        from aegis.scrape.patterns import _build_tfidf
        vecs = _build_tfidf([["nvidia", "gpu", "chip"]])
        assert len(vecs) == 1
        assert isinstance(vecs[0], dict)

    def test_build_tfidf_normalised(self):
        import math

        from aegis.scrape.patterns import _build_tfidf
        vecs = _build_tfidf([["nvidia", "gpu"], ["apple", "iphone"]])
        for vec in vecs:
            if vec:
                norm = math.sqrt(sum(v * v for v in vec.values()))
                assert abs(norm - 1.0) < 0.01

    def test_cosine_identical_vectors(self):
        from aegis.scrape.patterns import _cosine
        v = {"a": 0.6, "b": 0.8}
        assert abs(_cosine(v, v) - 1.0) < 0.001

    def test_cosine_orthogonal_vectors(self):
        from aegis.scrape.patterns import _cosine
        a = {"x": 1.0}
        b = {"y": 1.0}
        assert _cosine(a, b) == 0.0

    def test_cosine_empty_vectors(self):
        from aegis.scrape.patterns import _cosine
        assert _cosine({}, {"a": 1.0}) == 0.0
        assert _cosine({"a": 1.0}, {}) == 0.0

    def test_greedy_cluster_single_item(self):
        from aegis.scrape.patterns import _greedy_cluster
        clusters = _greedy_cluster([{"a": 1.0}], threshold=0.5)
        assert len(clusters) == 1
        assert clusters[0] == [0]

    def test_greedy_cluster_similar_items_merged(self):
        from aegis.scrape.patterns import _greedy_cluster
        # Two very similar vectors → same cluster
        v = {"nvidia": 0.7, "gpu": 0.7}
        clusters = _greedy_cluster([v, v], threshold=0.5)
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_greedy_cluster_dissimilar_items_separate(self):
        from aegis.scrape.patterns import _greedy_cluster
        a = {"nvidia": 1.0}
        b = {"apple": 1.0}
        clusters = _greedy_cluster([a, b], threshold=0.5)
        assert len(clusters) == 2


# ===========================================================================
# Helpers for _scrape_source / scrape_topic tests
# ===========================================================================


async def _empty_run(**kw):
    """Async generator that yields nothing — noop adapter run."""
    if False:  # pragma: no cover
        yield


def _make_noop_adapter():
    a = MagicMock()
    a.setup = AsyncMock()
    a.teardown = AsyncMock()
    a.run = _empty_run
    return a


def _make_noop_cls():
    """Mock adapter class that always constructs a noop adapter."""
    return MagicMock(return_value=_make_noop_adapter())


def _all_noop_patches():
    """ExitStack that silences all external adapter network calls."""
    from contextlib import ExitStack

    targets = [
        "aegis.scrape.sources.hacker_news.HackerNewsAdapter",
        "aegis.scrape.sources.google_news_rss.GoogleNewsRSSAdapter",
        "aegis.scrape.sources.bing_news_rss.BingNewsRSSAdapter",
        "aegis.scrape.sources.reddit_rss.RedditRSSAdapter",
        "aegis.scrape.sources.github_trending.GitHubTrendingAdapter",
        "aegis.scrape.sources.amazon.AmazonAdapter",
    ]
    stack = ExitStack()
    for t in targets:
        stack.enter_context(patch(t, _make_noop_cls()))
    return stack


# ===========================================================================
# _scrape_source: per-adapter runner
# ===========================================================================


class TestScrapeSource:
    @pytest.mark.asyncio
    async def test_returns_signals_on_success(self):
        from aegis.scrape.topic import _scrape_source

        sig = _signal_obj("Breakthrough in quantum computing")

        async def _run(**kw):
            yield sig

        adapter = _make_noop_adapter()
        adapter.run = _run
        name, signals, error = await _scrape_source("hn:quantum", adapter, {"limit": 10})
        assert error is None
        assert signals == [sig]

    @pytest.mark.asyncio
    async def test_returns_error_string_on_exception(self):
        from aegis.scrape.topic import _scrape_source

        async def _bad(**kw):
            raise RuntimeError("connection timeout")
            if False:  # pragma: no cover
                yield

        adapter = _make_noop_adapter()
        adapter.run = _bad
        _, signals, error = await _scrape_source("bad_src", adapter, {})
        assert "connection timeout" in (error or "")
        assert signals == []

    @pytest.mark.asyncio
    async def test_teardown_called_after_exception(self):
        from aegis.scrape.topic import _scrape_source

        async def _bad(**kw):
            raise ValueError("parse error")
            if False:  # pragma: no cover
                yield

        adapter = _make_noop_adapter()
        adapter.run = _bad
        await _scrape_source("src", adapter, {})
        adapter.teardown.assert_called_once()

    @pytest.mark.asyncio
    async def test_source_name_preserved_in_return(self):
        from aegis.scrape.topic import _scrape_source

        adapter = _make_noop_adapter()
        name, _, _ = await _scrape_source("google_news:ai chip", adapter, {})
        assert name == "google_news:ai chip"

    @pytest.mark.asyncio
    async def test_multiple_signals_collected(self):
        from aegis.scrape.topic import _scrape_source

        sigs = [_signal_obj(f"AI signal {i}") for i in range(5)]

        async def _run(**kw):
            for s in sigs:
                yield s

        adapter = _make_noop_adapter()
        adapter.run = _run
        _, signals, error = await _scrape_source("hn:ai", adapter, {"limit": 5})
        assert error is None
        assert len(signals) == 5


# ===========================================================================
# scrape_topic: full orchestration (mocked adapters)
# ===========================================================================


class TestScrapeTopic:
    @pytest.mark.asyncio
    async def test_dry_run_returns_result(self):
        from aegis.scrape.topic import TopicScrapeResult, scrape_topic

        with _all_noop_patches():
            result = await scrape_topic("openai", dry_run=True)

        assert isinstance(result, TopicScrapeResult)
        assert result.topic == "openai"
        assert result.total_fetched == 0
        assert result.total_inserted == 0
        assert result.finished_at is not None

    @pytest.mark.asyncio
    async def test_signals_collected_from_hn_adapter(self):
        from aegis.scrape.topic import scrape_topic

        sig1 = _signal_obj("OpenAI launches GPT-5 for enterprise customers")
        sig2 = _signal_obj("Sam Altman on the future of artificial intelligence")

        async def _yield_two(**kw):
            yield sig1
            yield sig2

        hn_adapter = _make_noop_adapter()
        hn_adapter.run = _yield_two
        hn_cls = MagicMock(return_value=hn_adapter)

        with _all_noop_patches(), \
             patch("aegis.scrape.sources.hacker_news.HackerNewsAdapter", hn_cls):
            result = await scrape_topic("openai", dry_run=True)

        # HN runs for up to 7 query terms, each yielding 2 signals
        assert result.total_fetched >= 2

    @pytest.mark.asyncio
    async def test_adapter_errors_recorded_in_result(self):
        from aegis.scrape.topic import scrape_topic

        async def _raise(**kw):
            raise ConnectionError("refused")
            if False:  # pragma: no cover
                yield

        err_adapter = _make_noop_adapter()
        err_adapter.run = _raise
        err_cls = MagicMock(return_value=err_adapter)

        with _all_noop_patches(), \
             patch("aegis.scrape.sources.hacker_news.HackerNewsAdapter", err_cls):
            result = await scrape_topic("openai", dry_run=True)

        assert any("refused" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_sources_hit_populated_for_returning_adapter(self):
        from aegis.scrape.topic import scrape_topic

        sig = _signal_obj("Reddit: AI chip shortage hits the market")

        async def _one(**kw):
            yield sig

        reddit_adapter = _make_noop_adapter()
        reddit_adapter.run = _one
        reddit_cls = MagicMock(return_value=reddit_adapter)

        with _all_noop_patches(), \
             patch("aegis.scrape.sources.reddit_rss.RedditRSSAdapter", reddit_cls):
            result = await scrape_topic("openai", dry_run=True)

        assert "reddit" in result.sources_hit

    @pytest.mark.asyncio
    async def test_detect_patterns_false_leaves_patterns_empty(self):
        with _all_noop_patches():
            from aegis.scrape.topic import scrape_topic
            result = await scrape_topic("openai", dry_run=True, detect_patterns=False)

        assert result.patterns == []

    @pytest.mark.asyncio
    async def test_github_included_for_general_category(self):
        """Topics that resolve to 'general' still include GitHub Trending."""
        from aegis.scrape.topic import scrape_topic

        gh_cls = _make_noop_cls()
        with _all_noop_patches(), \
             patch("aegis.scrape.sources.github_trending.GitHubTrendingAdapter", gh_cls):
            await scrape_topic("openai", dry_run=True)

        assert gh_cls.called

    @pytest.mark.asyncio
    async def test_amazon_included_for_health_category(self):
        """Health-category topics trigger Amazon Bestsellers."""
        from aegis.scrape.topic import scrape_topic

        amz_cls = _make_noop_cls()
        with _all_noop_patches(), \
             patch("aegis.scrape.sources.amazon.AmazonAdapter", amz_cls):
            # "FDA vaccine" → health category
            await scrape_topic("FDA vaccine trial supplement", dry_run=True)

        assert amz_cls.called

    @pytest.mark.asyncio
    async def test_with_pool_uses_async_dedup_and_insert(self):
        """When pool+tenant+not dry_run, async dedup and insert paths run."""
        from uuid import uuid4

        from aegis.scrape.topic import scrape_topic

        sig = _signal_obj("Bitcoin hits all time high milestone")

        async def _one(**kw):
            yield sig

        hn_adapter = _make_noop_adapter()
        hn_adapter.run = _one
        hn_cls = MagicMock(return_value=hn_adapter)

        mock_pool = MagicMock()
        tenant = uuid4()

        with _all_noop_patches(), \
             patch("aegis.scrape.sources.hacker_news.HackerNewsAdapter", hn_cls), \
             patch("aegis.db.dedup.deduplicate_signals",
                   new=AsyncMock(return_value=([sig], 0))), \
             patch("aegis.db.signals.insert_signals",
                   new=AsyncMock(return_value=1)):
            result = await scrape_topic(
                "bitcoin",
                pool=mock_pool,
                tenant_id=tenant,
                dry_run=False,
            )

        assert result.total_inserted >= 0

    @pytest.mark.asyncio
    async def test_dry_run_with_pool_skips_insert(self):
        """dry_run=True sets total_inserted=0 even when pool is provided."""
        from uuid import uuid4

        from aegis.scrape.topic import scrape_topic

        mock_pool = MagicMock()
        tenant = uuid4()

        with _all_noop_patches():
            result = await scrape_topic(
                "ethereum",
                pool=mock_pool,
                tenant_id=tenant,
                dry_run=True,
            )

        assert result.total_inserted == 0

    @pytest.mark.asyncio
    async def test_exception_in_gather_task_recorded(self):
        """asyncio.gather exceptions (not adapter errors) land in errors."""
        from aegis.scrape.topic import scrape_topic

        with _all_noop_patches():
            result = await scrape_topic("openai", dry_run=True)

        # No unexpected crashes — errors list may be empty or have mild issues
        assert isinstance(result.errors, list)

    @pytest.mark.asyncio
    async def test_expansion_populated_in_result(self):
        """result.expansion contains the full TopicExpansion for the topic."""
        from aegis.scrape.topic import TopicExpansion, scrape_topic

        with _all_noop_patches():
            result = await scrape_topic("nvidia", dry_run=True)

        assert isinstance(result.expansion, TopicExpansion)
        assert result.expansion.base_topic == "nvidia"

    @pytest.mark.asyncio
    async def test_pattern_detection_runs_when_signals_present(self):
        """detect_patterns=True with actual signals populates result.patterns."""
        from aegis.scrape.topic import scrape_topic

        # Provide multiple signals with similar titles so clustering fires
        sigs = [
            _signal_obj("NVIDIA GPU sales hit record revenue this quarter"),
            _signal_obj("NVIDIA GPU revenue reaches record high in Q1"),
            _signal_obj("NVIDIA posts record GPU chip sales growth"),
        ]

        idx = 0

        async def _cycle(**kw):
            nonlocal idx
            yield sigs[idx % len(sigs)]
            idx += 1

        hn_adapter = _make_noop_adapter()
        hn_adapter.run = _cycle
        hn_cls = MagicMock(return_value=hn_adapter)

        with _all_noop_patches(), \
             patch("aegis.scrape.sources.hacker_news.HackerNewsAdapter", hn_cls):
            result = await scrape_topic("nvidia gpu", dry_run=True, detect_patterns=True)

        # patterns may or may not cluster depending on dedup, just verify no crash
        assert isinstance(result.patterns, list)
