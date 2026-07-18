"""PASS2-2D / BRAIN-4: feedback-weighted agent ensemble tests."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from aegis.agents.schemas import (
    AgentDecision,
    AgentVerdict,
    GraphResult,
    Priority,
)
from aegis.agents.supervisor import (
    _WEIGHTS_REDIS_KEY,
    build_graph_result,
    compute_accuracy_weights,
    compute_final_score,
    get_agent_weight,
    refresh_agent_weights,
    reset_agent_weights,
)


@pytest.fixture(autouse=True)
def _reset_weights():
    reset_agent_weights()
    yield
    reset_agent_weights()


def _decision(agent: str, score: float = 0.8, confidence: float = 0.7) -> AgentDecision:
    return AgentDecision(
        agent=agent,
        trend_id="trend-1",
        correlation_id="corr-1",
        verdict=AgentVerdict.PROCEED,
        score=score,
        confidence=confidence,
        reasoning="test",
        details={},
    )


def _state() -> dict:
    return {
        "trend_id": "trend-1",
        "correlation_id": "corr-1",
        "decisions": [
            _decision("scout", 0.9, 0.8),
            _decision("narrative", 0.6, 0.5),
            _decision("auditor", 0.7, 0.6),
            _decision("red_team", 0.5, 0.9),
        ],
        "auditor_margin_p10": 2.5,
        "scout_verdict": AgentVerdict.PROCEED,
        "scout_score": 0.9,
    }


class _FakeWeightsRedis:
    def __init__(self, weights: dict[str, str] | None = None) -> None:
        self.hash: dict[str, str] = dict(weights or {})

    async def hgetall(self, key: str):
        return dict(self.hash)

    async def hset(self, key: str, mapping: dict):
        self.hash.update({k: str(v) for k, v in mapping.items()})

    async def expire(self, key: str, ttl: int):
        return True


class TestWeightedScore:
    def test_equal_weights_match_legacy_aggregation(self):
        """Neutral (empty) weight cache reproduces the historical blend exactly."""
        state = _state()
        baseline = compute_final_score(state)
        # All agents at the same non-neutral accuracy must also match —
        # the common factor cancels in the normalisation.
        import aegis.agents.supervisor as sup

        sup._accuracy_weights.update(dict.fromkeys(
            ["scout", "narrative", "geo_arbitrage", "auditor", "red_team"], 1.3
        ))
        assert compute_final_score(state) == pytest.approx(baseline)

    def test_high_accuracy_agent_pulls_score_toward_its_vote(self):
        state = _state()
        baseline_score, _ = compute_final_score(state)
        import aegis.agents.supervisor as sup

        # Scout (high score 0.9) becomes highly trusted; red_team (0.5) distrusted.
        sup._accuracy_weights.update({"scout": 1.4, "red_team": 0.8})
        weighted_score, _ = compute_final_score(state)
        assert weighted_score > baseline_score

    async def test_weight_values_from_accuracy(self):
        """90% accuracy → 1.4; 30% accuracy → 0.8 (weight = 0.5 + accuracy)."""
        redis = _FakeWeightsRedis({"scout": "1.4", "hedge": "0.8"})
        loaded = await refresh_agent_weights(redis)
        assert loaded["scout"] == pytest.approx(0.5 + 0.9)
        assert loaded["hedge"] == pytest.approx(0.5 + 0.3)

    def test_redis_unavailable_all_weights_default_to_one(self):
        assert get_agent_weight("scout") == 1.0
        assert get_agent_weight("never_seen_agent") == 1.0


class TestWeightLoading:
    async def test_refresh_handles_none_client(self):
        assert await refresh_agent_weights(None) == {}

    async def test_refresh_handles_redis_error(self):
        redis = MagicMock()
        redis.hgetall = AsyncMock(side_effect=RuntimeError("down"))
        assert await refresh_agent_weights(redis) == {}
        assert get_agent_weight("scout") == 1.0

    async def test_refresh_clamps_to_bounds_and_parses_updated_at(self):
        ts = datetime.now(UTC).isoformat()
        redis = _FakeWeightsRedis(
            {"scout": "9.0", "hedge": "0.1", "_updated_at": ts}
        )
        loaded = await refresh_agent_weights(redis)
        assert loaded["scout"] == 1.5
        assert loaded["hedge"] == 0.5

    async def test_cache_ttl_respected_within_one_hour(self):
        redis = _FakeWeightsRedis({"scout": "1.2"})
        call_counter = {"n": 0}
        real = redis.hgetall

        async def counted(key):
            call_counter["n"] += 1
            return await real(key)

        redis.hgetall = counted
        await refresh_agent_weights(redis)
        await refresh_agent_weights(redis)
        assert call_counter["n"] == 1  # second call served from cache


class TestGraphResultFields:
    def test_agent_weights_field_in_graph_result(self):
        import aegis.agents.supervisor as sup

        sup._accuracy_weights.update({"scout": 1.3})
        state = _state()
        state.update(
            {
                "final_verdict": AgentVerdict.PROCEED,
                "final_priority": Priority.P2_OPPORTUNITY,
                "final_score": 0.8,
                "final_confidence": 0.7,
                "halt_reason": "completed",
            }
        )
        result = build_graph_result(state, started_at=datetime.now(UTC))
        assert isinstance(result, GraphResult)
        assert result.agent_weights == {"scout": 1.3}

    def test_graph_result_defaults_empty_weights(self):
        state = _state()
        state.update(
            {
                "final_verdict": AgentVerdict.HOLD,
                "final_priority": Priority.P3_HOUSEKEEPING,
                "final_score": 0.5,
                "final_confidence": 0.5,
                "halt_reason": "completed",
            }
        )
        result = build_graph_result(state, started_at=datetime.now(UTC))
        assert result.agent_weights == {}
        assert result.weight_update_ts is None


class TestComputeAccuracyWeights:
    def _stream_entries(self) -> list:
        def body(trend: str, scout: str, hedge: str) -> dict:
            return {
                "body": json.dumps(
                    {
                        "trend_id": trend,
                        "decisions": [
                            {"agent": "scout", "verdict": scout},
                            {"agent": "hedge", "verdict": hedge},
                        ],
                    }
                )
            }

        return [
            ("1-0", body("t1", "proceed", "block")),  # roi +: scout right, hedge wrong
            ("2-0", body("t2", "proceed", "block")),  # roi -: scout wrong, hedge right
            ("3-0", body("t3", "proceed", "block")),  # roi +: scout right, hedge wrong
        ]

    def _pool(self) -> MagicMock:
        rows = [
            {"trend_id": "t1", "roi": 10.0},
            {"trend_id": "t2", "roi": -5.0},
            {"trend_id": "t3", "roi": 4.0},
        ]
        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)
        pool = MagicMock()
        pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        return pool

    async def test_accuracy_computed_and_persisted(self):
        redis = _FakeWeightsRedis()
        redis.xrevrange = AsyncMock(return_value=self._stream_entries())
        weights = await compute_accuracy_weights(redis, self._pool(), "tenant-1")
        # scout: 2/3 correct → 0.5 + 0.6667; hedge: 1/3 → 0.5 + 0.3333
        assert weights["scout"] == pytest.approx(0.5 + 2 / 3, abs=1e-3)
        assert weights["hedge"] == pytest.approx(0.5 + 1 / 3, abs=1e-3)
        assert "_updated_at" in redis.hash
        assert _WEIGHTS_REDIS_KEY  # key constant exported

    async def test_no_stream_overlap_returns_empty(self):
        redis = _FakeWeightsRedis()
        redis.xrevrange = AsyncMock(return_value=[])
        assert await compute_accuracy_weights(redis, self._pool(), "t") == {}

    async def test_stream_error_returns_empty(self):
        redis = _FakeWeightsRedis()
        redis.xrevrange = AsyncMock(side_effect=RuntimeError("down"))
        assert await compute_accuracy_weights(redis, self._pool(), "t") == {}

    async def test_db_error_returns_empty(self):
        redis = _FakeWeightsRedis()
        redis.xrevrange = AsyncMock(return_value=self._stream_entries())
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=RuntimeError("db down"))
        assert await compute_accuracy_weights(redis, pool, "t") == {}
