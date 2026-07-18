"""Phase 0 foundation unit tests.

Covers:
- ConcurrencyGovernor semaphore limits
- z_score_batch with known inputs
- classify_pulse for all 4 outputs
- validate_batch drops invalid signals and logs
- ScraperAgent.record_failure with same vs different error types
- ScraperAgent.is_cooling respects cooldown windows
- fingerprint_response produces deterministic hashes
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.scrape.governor import ConcurrencyGovernor, TokenBucket
from aegis.scrape.normalizer import (
    apply_tier_weight,
    normalize_swarm_batch,
    percentile_rank_batch,
    z_score_batch,
)
from aegis.scrape.result import AdapterCapabilities, AdapterStatus, AgentHealth
from aegis.scrape.schema_guard import fingerprint_response, validate_batch, validate_signal
from aegis.scrape.sentiment import classify_pulse, score_text
from aegis.scrape.swarm_agents import (
    COOLDOWN_BY_ERROR,
    DOWN_THRESHOLD_BY_ERROR,
    ScraperAgent,
    SwarmAgentPool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_capabilities(tier: str = "T3_search") -> AdapterCapabilities:
    return AdapterCapabilities(platform="test_platform", tier=tier)


def _make_agent(name: str = "test_agent", tier: str = "T3_search") -> ScraperAgent:
    return ScraperAgent(
        name=name,
        platform="test_platform",
        adapter_fn=AsyncMock(return_value=[]),
        capabilities=_make_capabilities(tier),
    )


def _make_signal(
    title: str = "Test Title Long Enough",
    platform: str = "reddit",
    url: str = "https://example.com",
    scraped_at: str = "2026-05-19T00:00:00Z",
) -> dict:
    return {
        "title": title,
        "platform": platform,
        "url": url,
        "scraped_at": scraped_at,
        "score": 100,
    }


# ---------------------------------------------------------------------------
# 1. ConcurrencyGovernor semaphore limits
# ---------------------------------------------------------------------------

class TestConcurrencyGovernor:
    def test_global_semaphore_is_semaphore(self) -> None:
        gov = ConcurrencyGovernor(max_concurrent=3)
        assert isinstance(gov.global_slot(), asyncio.Semaphore)

    def test_flaresolverr_semaphore_is_semaphore(self) -> None:
        gov = ConcurrencyGovernor(max_flaresolverr=2)
        assert isinstance(gov.flaresolverr_slot(), asyncio.Semaphore)

    def test_semaphore_limits_respected(self) -> None:
        gov = ConcurrencyGovernor(max_concurrent=2)
        sem = gov.global_slot()
        assert sem._value == 2  # type: ignore[attr-defined]

    def test_flaresolverr_limit_respected(self) -> None:
        gov = ConcurrencyGovernor(max_flaresolverr=1)
        sem = gov.flaresolverr_slot()
        assert sem._value == 1  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_jitter_completes(self) -> None:
        gov = ConcurrencyGovernor(jitter_max_ms=10)
        # Should not raise and should take <= 10ms
        t0 = time.monotonic()
        await gov.jitter()
        assert time.monotonic() - t0 < 0.5  # well within 500ms guard

    @pytest.mark.asyncio
    async def test_domain_slot_completes(self) -> None:
        gov = ConcurrencyGovernor()
        await gov.domain_slot("example.com")  # should not raise

    def test_token_bucket_consume_deducts(self) -> None:
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        assert bucket.consume(1) is True
        assert bucket._tokens < 5

    def test_token_bucket_empty_returns_false(self) -> None:
        bucket = TokenBucket(capacity=2, refill_rate=0.01)
        bucket.consume(2)
        assert bucket.consume(1) is False

    @pytest.mark.asyncio
    async def test_global_slot_blocks_excess(self) -> None:
        gov = ConcurrencyGovernor(max_concurrent=1)
        sem = gov.global_slot()
        acquired = []

        async def grab() -> None:
            async with sem:
                acquired.append(1)
                await asyncio.sleep(0.01)

        await asyncio.gather(grab(), grab())
        assert len(acquired) == 2  # both ran, sequentially


# ---------------------------------------------------------------------------
# 2. z_score_batch with known inputs
# ---------------------------------------------------------------------------

class TestZScoreBatch:
    def test_basic_normalization(self) -> None:
        signals = [{"score": 10}, {"score": 20}, {"score": 30}]
        result = z_score_batch(signals)
        scores = [s["score_normalized"] for s in result]
        # mean should be ~0, std ~1
        import statistics
        assert abs(statistics.mean(scores)) < 0.01
        assert abs(statistics.stdev(scores) - 1.0) < 0.01

    def test_single_signal_passthrough(self) -> None:
        signals = [{"score": 42}]
        result = z_score_batch(signals)
        # Single signal: no normalization possible
        assert result == signals

    def test_constant_scores_passthrough(self) -> None:
        signals = [{"score": 5}, {"score": 5}, {"score": 5}]
        result = z_score_batch(signals)
        # stdev = 0, skip normalization
        assert result == signals

    def test_custom_score_field(self) -> None:
        signals = [{"likes": 100}, {"likes": 200}]
        result = z_score_batch(signals, score_field="likes")
        assert "score_normalized" in result[0]

    def test_missing_score_treated_as_zero(self) -> None:
        signals = [{"score": None}, {"score": 10}]
        result = z_score_batch(signals)
        assert "score_normalized" in result[0]

    def test_empty_list(self) -> None:
        assert z_score_batch([]) == []

    def test_original_not_mutated(self) -> None:
        orig = [{"score": 1}, {"score": 2}]
        z_score_batch(orig)
        assert "score_normalized" not in orig[0]


# ---------------------------------------------------------------------------
# 3. classify_pulse — all 4 outputs
# ---------------------------------------------------------------------------

class TestClassifyPulse:
    def test_bullish(self) -> None:
        signals = [{"sentiment": 0.5}, {"sentiment": 0.6}, {"sentiment": 0.4}]
        assert classify_pulse(signals) == "bullish"

    def test_bearish(self) -> None:
        signals = [{"sentiment": -0.4}, {"sentiment": -0.5}, {"sentiment": -0.3}]
        assert classify_pulse(signals) == "bearish"

    def test_neutral(self) -> None:
        signals = [{"sentiment": 0.05}, {"sentiment": -0.05}, {"sentiment": 0.0}]
        assert classify_pulse(signals) == "neutral"

    def test_volatile(self) -> None:
        # High variance even if mean ≈ 0
        signals = [{"sentiment": 1.0}, {"sentiment": -1.0}, {"sentiment": 0.9}, {"sentiment": -0.9}]
        assert classify_pulse(signals) == "volatile"

    def test_no_sentiments_returns_neutral(self) -> None:
        signals = [{"title": "No sentiment here"}, {}]
        assert classify_pulse(signals) == "neutral"

    def test_score_text_bullish_finance(self) -> None:
        score = score_text("Stock market shows record rally and breakout")
        assert score > 0

    def test_score_text_bearish_finance(self) -> None:
        score = score_text("Market crash leads to massive selloff and recession fears")
        assert score < 0

    def test_score_text_empty(self) -> None:
        assert score_text("") == 0.0

    def test_score_text_neutral(self) -> None:
        score = score_text("The weather is sunny today")
        assert score == 0.0


# ---------------------------------------------------------------------------
# 4. validate_batch — drops invalid signals and logs
# ---------------------------------------------------------------------------

class TestValidateBatch:
    def test_valid_signal_passes(self) -> None:
        signals = [_make_signal()]
        result = validate_batch(signals, "reddit")
        assert len(result) == 1

    def test_missing_title_dropped(self) -> None:
        signals = [{"url": "https://x.com", "platform": "reddit", "scraped_at": "now"}]
        result = validate_batch(signals, "reddit")
        assert len(result) == 0

    def test_missing_url_dropped(self) -> None:
        signals = [{"title": "Test", "platform": "reddit", "scraped_at": "now"}]
        result = validate_batch(signals, "reddit")
        assert len(result) == 0

    def test_missing_platform_dropped(self) -> None:
        signals = [{"title": "Test", "url": "https://x.com", "scraped_at": "now"}]
        result = validate_batch(signals, "test")
        assert len(result) == 0

    def test_null_required_field_dropped(self) -> None:
        signals = [{"title": None, "url": "https://x.com", "platform": "reddit", "scraped_at": "now"}]
        result = validate_batch(signals, "reddit")
        assert len(result) == 0

    def test_mixed_batch(self) -> None:
        signals = [
            _make_signal("Valid Signal One"),
            {"url": "https://x.com"},  # missing title, platform, scraped_at
            _make_signal("Valid Signal Two"),
        ]
        result = validate_batch(signals, "reddit")
        assert len(result) == 2

    def test_violations_logged(self) -> None:
        signals = [{"url": "https://x.com"}]
        with patch("aegis.scrape.schema_guard._log") as mock_log:
            validate_batch(signals, "reddit")
            mock_log.warning.assert_called()

    def test_validate_signal_returns_violations(self) -> None:
        ok, violations = validate_signal({}, "reddit")
        assert not ok
        assert len(violations) > 0


# ---------------------------------------------------------------------------
# 5. ScraperAgent.record_failure — same vs different error types
# ---------------------------------------------------------------------------

class TestScraperAgentHealth:
    def test_initial_health_unknown(self) -> None:
        agent = _make_agent()
        assert agent.health == AgentHealth.UNKNOWN

    def test_success_sets_healthy(self) -> None:
        agent = _make_agent()
        agent.record_success(10, 250.0)
        assert agent.health == AgentHealth.HEALTHY
        assert agent.last_signal_count == 10

    def test_consecutive_same_error_increments_counter(self) -> None:
        agent = _make_agent()
        agent.record_failure(AdapterStatus.HTTP_ERROR, 100.0)
        agent.record_failure(AdapterStatus.HTTP_ERROR, 100.0)
        assert agent.consecutive_failures == 2

    def test_different_error_type_resets_counter(self) -> None:
        agent = _make_agent()
        agent.record_failure(AdapterStatus.HTTP_ERROR, 100.0)
        agent.record_failure(AdapterStatus.TIMEOUT, 100.0)
        # counter resets because error type changed
        assert agent.consecutive_failures == 1

    def test_reaches_down_threshold_marks_down(self) -> None:
        agent = _make_agent()
        threshold = DOWN_THRESHOLD_BY_ERROR[AdapterStatus.HTTP_ERROR]
        for _ in range(threshold):
            agent.record_failure(AdapterStatus.HTTP_ERROR, 100.0)
        assert agent.health == AgentHealth.DOWN

    def test_below_down_threshold_marks_degraded(self) -> None:
        agent = _make_agent()
        agent.record_failure(AdapterStatus.HTTP_ERROR, 100.0)
        assert agent.health == AgentHealth.DEGRADED

    def test_empty_is_not_failure(self) -> None:
        agent = _make_agent()
        agent.record_failure(AdapterStatus.EMPTY, 50.0)
        assert agent.health == AgentHealth.UNKNOWN  # unchanged
        assert agent.consecutive_failures == 0

    def test_success_resets_failure_counter(self) -> None:
        agent = _make_agent()
        agent.record_failure(AdapterStatus.HTTP_ERROR, 100.0)
        agent.record_failure(AdapterStatus.HTTP_ERROR, 100.0)
        assert agent.consecutive_failures == 2
        agent.record_success(5, 200.0)
        assert agent.consecutive_failures == 0
        assert agent.health == AgentHealth.HEALTHY

    def test_blocked_has_lower_down_threshold(self) -> None:
        assert DOWN_THRESHOLD_BY_ERROR[AdapterStatus.BLOCKED] < DOWN_THRESHOLD_BY_ERROR[AdapterStatus.HTTP_ERROR]

    def test_to_dict_contains_expected_keys(self) -> None:
        agent = _make_agent()
        d = agent.to_dict()
        assert "name" in d
        assert "health" in d
        assert "avg_latency_ms" in d
        assert "is_cooling" in d


# ---------------------------------------------------------------------------
# 6. ScraperAgent.is_cooling respects cooldown windows
# ---------------------------------------------------------------------------

class TestCoolingWindow:
    def test_not_cooling_initially(self) -> None:
        agent = _make_agent()
        assert agent.is_cooling is False

    def test_marked_down_sets_cooling(self) -> None:
        agent = _make_agent()
        threshold = DOWN_THRESHOLD_BY_ERROR[AdapterStatus.BLOCKED]
        for _ in range(threshold):
            agent.record_failure(AdapterStatus.BLOCKED, 100.0)
        # Should be cooling after hitting DOWN threshold for BLOCKED
        assert agent.is_cooling is True

    def test_cooling_expires(self) -> None:
        agent = _make_agent()
        # Manually set cooling to expire in the past
        agent._cooling_until = time.monotonic() - 1.0
        assert agent.is_cooling is False

    def test_rate_limited_gets_longer_cooldown_than_timeout(self) -> None:
        assert COOLDOWN_BY_ERROR[AdapterStatus.RATE_LIMITED] > COOLDOWN_BY_ERROR[AdapterStatus.TIMEOUT]

    def test_blocked_gets_longest_cooldown(self) -> None:
        assert COOLDOWN_BY_ERROR[AdapterStatus.BLOCKED] >= COOLDOWN_BY_ERROR[AdapterStatus.RATE_LIMITED]


# ---------------------------------------------------------------------------
# 7. fingerprint_response produces deterministic hashes
# ---------------------------------------------------------------------------

class TestFingerprintResponse:
    def test_deterministic_for_same_input(self) -> None:
        data = {"items": [{"id": 1, "title": "foo"}], "total": 1}
        h1 = fingerprint_response(data, "test")
        h2 = fingerprint_response(data, "test")
        assert h1 == h2

    def test_different_values_same_structure_same_hash(self) -> None:
        data1 = {"id": 1, "title": "alpha"}
        data2 = {"id": 999, "title": "beta"}
        # Same keys → same structural hash
        assert fingerprint_response(data1, "test") == fingerprint_response(data2, "test")

    def test_different_structure_different_hash(self) -> None:
        data1 = {"id": 1, "title": "test"}
        data2 = {"id": 1, "title": "test", "extra_key": "value"}
        assert fingerprint_response(data1, "test") != fingerprint_response(data2, "test")

    def test_list_input(self) -> None:
        data = [{"id": 1}, {"id": 2}]
        h = fingerprint_response(data, "test")
        assert isinstance(h, str)
        assert len(h) == 16  # SHA-256 truncated to 16 hex chars

    def test_empty_dict(self) -> None:
        h = fingerprint_response({}, "test")
        assert isinstance(h, str)
        assert len(h) == 16

    def test_platform_arg_does_not_affect_hash(self) -> None:
        data = {"key": "value"}
        # platform is only for logging context, not for hash
        h1 = fingerprint_response(data, "reddit")
        h2 = fingerprint_response(data, "amazon")
        assert h1 == h2


# ---------------------------------------------------------------------------
# 8. SwarmAgentPool — basic run_wave behaviour
# ---------------------------------------------------------------------------

class TestSwarmAgentPool:
    def _make_pool(
        self, adapter_fn: AsyncMock | None = None, max_concurrent: int = 5
    ) -> tuple[SwarmAgentPool, ScraperAgent]:
        if adapter_fn is None:
            adapter_fn = AsyncMock(return_value=[{"title": "t", "score": 1}])
        agent = ScraperAgent(
            name="mock_agent",
            platform="mock",
            adapter_fn=adapter_fn,
            capabilities=_make_capabilities(),
        )
        gov = ConcurrencyGovernor(max_concurrent=max_concurrent, jitter_max_ms=0)
        pool = SwarmAgentPool(agents=[agent], governor=gov)
        return pool, agent

    @pytest.mark.asyncio
    async def test_run_agent_success(self) -> None:
        fn = AsyncMock(return_value=[{"title": "Test", "score": 5}])
        pool, agent = self._make_pool(adapter_fn=fn)
        settings = MagicMock()
        settings.swarm_wave_timeout_s = 10.0
        settings.scrape.swarm_wave_timeout_s = 10.0
        result = await pool.run_agent(agent, settings, None, 10)
        assert result.status == AdapterStatus.SUCCESS
        assert result.signal_count == 1

    @pytest.mark.asyncio
    async def test_run_agent_empty(self) -> None:
        fn = AsyncMock(return_value=[])
        pool, agent = self._make_pool(adapter_fn=fn)
        settings = MagicMock()
        settings.swarm_wave_timeout_s = 10.0
        settings.scrape.swarm_wave_timeout_s = 10.0
        result = await pool.run_agent(agent, settings, None, 10)
        assert result.status == AdapterStatus.EMPTY
        assert result.signal_count == 0

    @pytest.mark.asyncio
    async def test_run_agent_timeout(self) -> None:
        async def slow(*_args: object, **_kwargs: object) -> list:
            await asyncio.sleep(60)
            return []

        agent = ScraperAgent(
            name="slow_agent",
            platform="slow",
            adapter_fn=slow,
            capabilities=_make_capabilities(),
        )
        gov = ConcurrencyGovernor(max_concurrent=5, jitter_max_ms=0)
        pool = SwarmAgentPool(agents=[agent], governor=gov)
        settings = MagicMock()
        settings.swarm_wave_timeout_s = 0.01  # 10ms — will timeout immediately
        settings.scrape.swarm_wave_timeout_s = 0.01
        result = await pool.run_agent(agent, settings, None, 10)
        assert result.status == AdapterStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_run_wave_returns_all_results(self) -> None:
        pool, agent = self._make_pool()
        settings = MagicMock()
        settings.swarm_wave_timeout_s = 10.0
        settings.scrape.swarm_wave_timeout_s = 10.0
        results = await pool.run_wave(["mock_agent"], settings, None, 10)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_cooling_agent_skipped(self) -> None:
        pool, agent = self._make_pool()
        agent._cooling_until = time.monotonic() + 9999
        settings = MagicMock()
        settings.swarm_wave_timeout_s = 10.0
        result = await pool.run_agent(agent, settings, None, 10)
        assert result.error_msg == "cooling_window_active"

    def test_get_healthy_agents_filters_down(self) -> None:
        pool, agent = self._make_pool()
        agent.health = AgentHealth.DOWN
        result = pool.get_healthy_agents()
        assert result == []

    def test_get_healthy_agents_includes_unknown(self) -> None:
        pool, agent = self._make_pool()
        agent.health = AgentHealth.UNKNOWN
        result = pool.get_healthy_agents()
        assert agent in result

    def test_get_healthy_agents_tier_filter(self) -> None:
        pool, agent = self._make_pool()
        agent.health = AgentHealth.HEALTHY
        result_match = pool.get_healthy_agents(tier="T3_search")
        result_nomatch = pool.get_healthy_agents(tier="T1_intent")
        assert agent in result_match
        assert agent not in result_nomatch

    def test_health_report_contains_agent(self) -> None:
        pool, _ = self._make_pool()
        report = pool.health_report()
        assert "mock_agent" in report

    @pytest.mark.asyncio
    async def test_restore_health_no_redis(self) -> None:
        pool, _ = self._make_pool()
        # Should not raise when redis_client is None
        await pool.restore_health_from_redis()


# ---------------------------------------------------------------------------
# 9. Normalizer pipeline tests
# ---------------------------------------------------------------------------

class TestNormalizerPipeline:
    def test_percentile_rank_range(self) -> None:
        signals = [{"score_normalized": float(i)} for i in range(10)]
        result = percentile_rank_batch(signals)
        ranks = [s["percentile_rank"] for s in result]
        assert min(ranks) == 0.0
        assert max(ranks) == 100.0

    def test_apply_tier_weight_t1(self) -> None:
        signals = [{"score_normalized": 1.0, "tier": "T1_intent"}]
        result = apply_tier_weight(signals)
        assert result[0]["weighted_score"] == 1.0  # weight 1.0

    def test_apply_tier_weight_t3(self) -> None:
        signals = [{"score_normalized": 1.0, "tier": "T3_search"}]
        result = apply_tier_weight(signals)
        assert result[0]["weighted_score"] == 0.7

    def test_normalize_swarm_batch_round_trip(self) -> None:
        signals = [
            {"score": 10, "platform": "reddit", "tier": "T1_intent"},
            {"score": 50, "platform": "reddit", "tier": "T1_intent"},
            {"score": 90, "platform": "amazon", "tier": "T3_search"},
        ]
        result = normalize_swarm_batch(signals)
        assert len(result) == 3
        assert all("weighted_score" in s for s in result)
        assert all("percentile_rank" in s for s in result)
