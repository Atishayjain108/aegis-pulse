"""PASS4 — Intelligent Adapter Routing Engine tests.

Covers the TopicClassifier (4A), AdapterRouter (4B), and the scrape_topic
wiring (4C): explicit adapter_override, routing metadata, and the
AEGIS_SCRAPE_TOPIC_ROUTING_EXTRAS hermeticity gate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from aegis.scrape.adapter_router import (
    _DEFAULT_HEALTH,
    AdapterRecommendation,
    AdapterRouter,
)
from aegis.scrape.topic_classifier import (
    ADAPTER_AFFINITY,
    TopicClassifier,
    TopicType,
)

# ===========================================================================
# TopicClassifier (4A)
# ===========================================================================


class TestTopicClassifier:
    def test_financial_query(self):
        assert TopicClassifier().classify("HDFC Bank stock") == TopicType.FINANCIAL_TREND

    def test_ecommerce_query(self):
        assert (
            TopicClassifier().classify("wireless earbuds buy")
            == TopicType.ECOMMERCE_PRODUCT
        )

    def test_supplier_query(self):
        assert (
            TopicClassifier().classify("wholesale manufacturer bulk")
            == TopicType.SUPPLIER_DISCOVERY
        )

    def test_regulatory_query(self):
        assert TopicClassifier().classify("FDA recall medicine") == TopicType.REGULATORY

    def test_no_keywords_defaults_to_consumer_trend(self):
        assert (
            TopicClassifier().classify("zanzibar holiday photos")
            == TopicType.CONSUMER_TREND
        )

    def test_bigram_scores_higher_than_single_word(self):
        # "mutual fund" bigram (financial, +2) outweighs "buy" (ecommerce, +1)
        assert (
            TopicClassifier().classify("buy mutual fund")
            == TopicType.FINANCIAL_TREND
        )

    def test_every_affinity_topic_type_has_adapters(self):
        for topic_type, affinity in ADAPTER_AFFINITY.items():
            assert affinity, f"empty affinity map for {topic_type}"
            for name, weight in affinity.items():
                assert 0.0 < weight <= 1.0, f"{topic_type}:{name} weight {weight}"

    def test_affinity_names_resolve_in_swarm_registry(self):
        """Every routed name must be runnable via the swarm adapter registry."""
        from aegis.scrape.swarm import _REGISTRY

        for topic_type, affinity in ADAPTER_AFFINITY.items():
            for name in affinity:
                assert name in _REGISTRY, (
                    f"{topic_type}: '{name}' not in swarm registry"
                )


# ===========================================================================
# AdapterRouter (4B) — the 8 spec-mandated behaviors
# ===========================================================================


class TestAdapterRouter:
    def test_financial_topic_routes_to_market_adapters(self):
        recs = AdapterRouter().route("HDFC Bank stock")
        assert recs[0].adapter_name in ("nse_bse", "moneycontrol", "economic_times")

    def test_ecommerce_topic_routes_to_marketplaces(self):
        recs = AdapterRouter().route("wireless earbuds buy")
        assert recs[0].adapter_name in ("amazon", "amazon_in", "flipkart")

    def test_supplier_topic_routes_to_registered_adapter(self):
        # indiamart was WAF-gated and dropped from the registry (audit P1-15);
        # supplier topics now route to registered adapters only.
        recs = AdapterRouter().route("wholesale manufacturer bulk")
        from aegis.scrape.swarm import _REGISTRY

        assert recs, "supplier topic must route somewhere"
        assert recs[0].adapter_name in _REGISTRY

    def test_regulatory_topic_routes_to_news(self):
        recs = AdapterRouter().route("FDA recall medicine")
        assert recs[0].adapter_name in ("google-news", "bing-news", "reuters")

    def test_credentials_required_excluded_without_key(self, monkeypatch):
        monkeypatch.delenv("AEGIS_REDDIT_CLIENT_ID", raising=False)
        affinity = {TopicType.TECH_NEWS: {"reddit": 0.99, "hacker-news": 0.95}}
        with patch.dict(ADAPTER_AFFINITY, affinity):
            recs = AdapterRouter().route(
                "AI chips", exclude_credentials_required=True
            )
        names = [r.adapter_name for r in recs]
        assert "reddit" not in names
        assert "hacker-news" in names

    def test_credentials_included_when_key_set(self, monkeypatch):
        monkeypatch.setenv("AEGIS_REDDIT_CLIENT_ID", "abc123")
        affinity = {TopicType.TECH_NEWS: {"reddit": 0.99, "hacker-news": 0.95}}
        with patch.dict(ADAPTER_AFFINITY, affinity):
            recs = AdapterRouter().route(
                "AI chips", exclude_credentials_required=True
            )
        reddit = next(r for r in recs if r.adapter_name == "reddit")
        assert reddit.requires_credentials is True
        assert reddit.credentials_available is True

    def test_flaresolverr_adapters_excluded(self):
        recs = AdapterRouter().route("wireless earbuds buy", exclude_flaresolverr=True)
        names = [r.adapter_name for r in recs]
        assert "flipkart" not in names
        assert "myntra" not in names

    def test_empty_health_tracker_uses_default_health(self):
        recs = AdapterRouter(health_tracker=None).route("HDFC Bank stock")
        assert all(r.health_score == _DEFAULT_HEALTH for r in recs)

    def test_sorted_by_final_score_descending_with_priorities(self):
        recs = AdapterRouter().route("HDFC Bank stock", top_n=6)
        scores = [r.final_score for r in recs]
        assert scores == sorted(scores, reverse=True)
        assert [r.priority for r in recs] == list(range(1, len(recs) + 1))

    def test_top_n_respected(self):
        recs = AdapterRouter().route("HDFC Bank stock", top_n=3)
        assert len(recs) == 3

    def test_explicit_topic_type_overrides_classifier(self):
        recs = AdapterRouter().route(
            "anything at all", explicit_topic_type=TopicType.SUPPLIER_DISCOVERY
        )
        assert recs[0].topic_type == TopicType.SUPPLIER_DISCOVERY
        # top supplier adapter is now a registered one (indiamart dropped, P1-15)
        assert recs[0].adapter_name == "amazon_in"

    def test_unhealthy_adapter_ranked_below_healthy(self):
        """SwarmAgentPool-style tracker: DOWN nse_bse loses its top spot."""

        def _agent(state: str, cooling: bool = False) -> MagicMock:
            a = MagicMock()
            a.is_cooling = cooling
            a.health = MagicMock()
            a.health.value = state
            return a

        pool = MagicMock(spec=["agents"])
        pool.agents = {
            "nse_bse": _agent("down"),
            "moneycontrol": _agent("healthy"),
        }
        recs = AdapterRouter(health_tracker=pool).route("HDFC Bank stock")
        ranked = [r.adapter_name for r in recs]
        assert ranked.index("moneycontrol") < ranked.index("nse_bse")
        nse = next(r for r in recs if r.adapter_name == "nse_bse")
        assert nse.health_score == pytest.approx(0.1)

    def test_cooling_adapter_scored_low(self):
        agent = MagicMock()
        agent.is_cooling = True
        pool = MagicMock(spec=["agents"])
        pool.agents = {"moneycontrol": agent}
        recs = AdapterRouter(health_tracker=pool).route("HDFC Bank stock")
        mc = next(r for r in recs if r.adapter_name == "moneycontrol")
        assert mc.health_score == pytest.approx(0.1)

    def test_broken_health_tracker_degrades_gracefully(self):
        class _Broken:
            @property
            def agents(self) -> dict:
                raise RuntimeError("redis down")

        recs = AdapterRouter(health_tracker=_Broken()).route("HDFC Bank stock")
        assert recs  # no exception, default health used
        assert all(r.health_score == _DEFAULT_HEALTH for r in recs)

    def test_recommendation_reason_populated(self):
        recs = AdapterRouter().route("HDFC Bank stock")
        assert all(isinstance(r, AdapterRecommendation) and r.reason for r in recs)


# ===========================================================================
# scrape_topic wiring (4C)
# ===========================================================================


def _noop_adapter(signals: list | None = None) -> MagicMock:
    async def _run(**kw):
        for s in signals or []:
            yield s

    a = MagicMock()
    a.setup = MagicMock(side_effect=_async_none)
    a.teardown = MagicMock(side_effect=_async_none)
    a.run = _run
    return a


async def _async_none(*a, **kw):
    return None


def _signal_obj(title: str) -> object:
    from aegis.schemas.enums import Platform

    m = MagicMock()
    m.title = title
    m.platform = Platform.HACKER_NEWS
    m.posted_at = datetime.now(UTC)
    m.provenance = MagicMock()
    m.provenance.scraped_at = datetime.now(UTC)
    return m


class TestScrapeTopicRouting:
    @pytest.mark.asyncio
    async def test_adapter_override_runs_only_named_adapters(self):
        from aegis.scrape.topic import scrape_topic

        sig = _signal_obj("NSE Nifty hits record high")
        built: list[str] = []

        def _builder(name: str):
            built.append(name)
            return _noop_adapter([sig])

        with patch("aegis.scrape.topic._build_registry_adapter", _builder):
            result = await scrape_topic(
                "nifty", dry_run=True, adapter_override=["nse_bse", "moneycontrol"]
            )

        assert built == ["nse_bse", "moneycontrol"]
        assert result.routed_adapters == ["nse_bse", "moneycontrol"]
        assert result.total_fetched == 2
        assert "nse_bse" in result.sources_hit

    @pytest.mark.asyncio
    async def test_adapter_override_unknown_name_recorded_as_error(self):
        from aegis.scrape.topic import scrape_topic

        with patch("aegis.scrape.topic._build_registry_adapter", lambda _n: None):
            result = await scrape_topic(
                "nifty", dry_run=True, adapter_override=["no_such_adapter"]
            )

        assert any("no_such_adapter" in e for e in result.errors)
        assert result.total_fetched == 0

    @pytest.mark.asyncio
    async def test_default_path_sets_routing_metadata_without_extras(self, monkeypatch):
        """Extras gate off (test env) → metadata populated, no registry builds."""
        from aegis.scrape.topic import scrape_topic
        from tests.unit.test_topic_and_patterns import _all_noop_patches

        monkeypatch.setenv("AEGIS_SCRAPE_TOPIC_ROUTING_EXTRAS", "false")
        builder = MagicMock(return_value=None)
        with _all_noop_patches(), patch(
            "aegis.scrape.topic._build_registry_adapter", builder
        ):
            result = await scrape_topic("HDFC Bank stock", dry_run=True)

        assert result.topic_type == "financial_trend"
        assert "nse_bse" in result.routed_adapters
        builder.assert_not_called()

    @pytest.mark.asyncio
    async def test_extras_scheduled_when_gate_enabled(self, monkeypatch):
        from aegis.scrape.topic import scrape_topic
        from tests.unit.test_topic_and_patterns import _all_noop_patches

        monkeypatch.setenv("AEGIS_SCRAPE_TOPIC_ROUTING_EXTRAS", "true")
        sig = _signal_obj("Moneycontrol: Sensex update")
        built: list[str] = []

        def _builder(name: str):
            built.append(name)
            return _noop_adapter([sig])

        with _all_noop_patches(), patch(
            "aegis.scrape.topic._build_registry_adapter", _builder
        ):
            result = await scrape_topic("HDFC Bank stock", dry_run=True)

        assert built, "routed extras should have been scheduled"
        assert set(built).issubset(set(result.routed_adapters))
        # core-covered names must never be double-scheduled as extras
        assert not {"hacker-news", "google-news", "bing-news"} & set(built)

    @pytest.mark.asyncio
    async def test_routing_failure_falls_back_to_core_only(self):
        from aegis.scrape.topic import scrape_topic
        from tests.unit.test_topic_and_patterns import _all_noop_patches

        with _all_noop_patches(), patch(
            "aegis.scrape.adapter_router.AdapterRouter.route",
            side_effect=RuntimeError("boom"),
        ):
            result = await scrape_topic("HDFC Bank stock", dry_run=True)

        # No crash; routing metadata absent, core path still ran to completion
        assert result.topic_type is None
        assert result.routed_adapters == []
        assert result.finished_at is not None

    def test_build_registry_adapter_unknown_name_returns_none(self):
        from aegis.scrape.topic import _build_registry_adapter

        assert _build_registry_adapter("definitely_not_registered") is None

    def test_build_registry_adapter_resolves_known_name(self):
        from aegis.scrape.topic import _build_registry_adapter

        adapter = _build_registry_adapter("hacker-news")
        assert adapter is not None
        assert type(adapter).__name__ == "HackerNewsAdapter"

    def test_routing_extras_gate_parses_env(self, monkeypatch):
        from aegis.scrape.topic import _routing_extras_enabled

        monkeypatch.setenv("AEGIS_SCRAPE_TOPIC_ROUTING_EXTRAS", "false")
        assert _routing_extras_enabled() is False
        monkeypatch.setenv("AEGIS_SCRAPE_TOPIC_ROUTING_EXTRAS", "TRUE")
        assert _routing_extras_enabled() is True
        monkeypatch.delenv("AEGIS_SCRAPE_TOPIC_ROUTING_EXTRAS")
        assert _routing_extras_enabled() is True


def test_resolve_registry_entry_accepts_hyphen_and_underscore() -> None:
    """Adapter resolution must accept both file-style (reddit_finance) and
    registry-style (hacker-news) names regardless of - vs _ convention."""
    from aegis.scrape.swarm import _REGISTRY
    from aegis.scrape.topic import _resolve_registry_entry

    # hyphenated registry key resolvable via underscore form and vice-versa.
    assert _resolve_registry_entry("hacker_news", _REGISTRY) is not None
    assert _resolve_registry_entry("hacker-news", _REGISTRY) is not None
    assert _resolve_registry_entry("reddit-finance", _REGISTRY) is not None
    assert _resolve_registry_entry("reddit_finance", _REGISTRY) is not None
    assert _resolve_registry_entry("totally_unknown_xyz", _REGISTRY) is None
