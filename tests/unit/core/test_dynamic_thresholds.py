"""PASS2-2C: dynamic threshold adaptation tests."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from aegis.core.dynamic_thresholds import (
    _REDIS_KEY,
    DynamicThresholds,
    ThresholdState,
)


class _FakeRedis:
    """get/set fake with call counting."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.get_calls = 0

    async def get(self, key: str):
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        return True


def _outcome_rows(n_win: int, n_loss: int) -> list[dict]:
    rows = [
        {"prediction_score": 0.8, "roi": 25.0, "status": "successful"}
        for _ in range(n_win)
    ]
    rows += [
        {"prediction_score": 0.8, "roi": -10.0, "status": "failed"}
        for _ in range(n_loss)
    ]
    return rows


def _fake_pool(rows: list[dict]) -> MagicMock:
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


class TestUpdateFromOutcomes:
    async def test_high_precision_relaxes_thresholds(self):
        redis = _FakeRedis()
        dt = DynamicThresholds(redis=redis)
        before = await dt._get()
        # 90% winners → precision above the 0.80 target → relax.
        state = await dt.update_from_outcomes(_fake_pool(_outcome_rows(45, 5)), "t1")
        assert state is not None
        assert state.confidence_gate == pytest.approx(before.confidence_gate - 0.02)
        assert state.velocity_slope == pytest.approx(before.velocity_slope - 0.02)
        # Relaxing compliance = HIGHER block threshold (fewer blocks).
        assert state.comply_block == pytest.approx(before.comply_block + 0.02)

    async def test_low_precision_tightens_thresholds(self):
        redis = _FakeRedis()
        dt = DynamicThresholds(redis=redis)
        before = await dt._get()
        # 40% winners → precision below target → tighten.
        state = await dt.update_from_outcomes(_fake_pool(_outcome_rows(20, 30)), "t1")
        assert state is not None
        assert state.confidence_gate == pytest.approx(before.confidence_gate + 0.02)
        assert state.velocity_slope == pytest.approx(before.velocity_slope + 0.02)
        assert state.comply_block == pytest.approx(before.comply_block - 0.02)

    async def test_bounds_enforced(self):
        redis = _FakeRedis()
        redis.store[_REDIS_KEY] = json.dumps(
            {
                "confidence_gate": 0.95,
                "velocity_slope": 5.0,
                "comply_block": 0.50,
                "updated_at": datetime.now(UTC).isoformat(),
                "outcome_count": 100,
                "precision": 0.5,
            }
        )
        dt = DynamicThresholds(redis=redis)
        # Low precision tightens — but everything is already at its bound.
        state = await dt.update_from_outcomes(_fake_pool(_outcome_rows(10, 40)), "t1")
        assert state is not None
        assert state.confidence_gate <= 0.95
        assert state.velocity_slope <= 5.0
        assert state.comply_block >= 0.50

    async def test_confidence_never_below_floor(self):
        redis = _FakeRedis()
        redis.store[_REDIS_KEY] = json.dumps(
            {
                "confidence_gate": 0.60,
                "velocity_slope": 1.0,
                "comply_block": 0.90,
                "updated_at": datetime.now(UTC).isoformat(),
                "outcome_count": 100,
                "precision": 0.9,
            }
        )
        dt = DynamicThresholds(redis=redis)
        # High precision relaxes — but already at the relaxed bound.
        state = await dt.update_from_outcomes(_fake_pool(_outcome_rows(45, 5)), "t1")
        assert state is not None
        assert state.confidence_gate >= 0.60
        assert state.velocity_slope >= 1.0
        assert state.comply_block <= 0.90

    async def test_insufficient_data_returns_none(self):
        dt = DynamicThresholds(redis=_FakeRedis())
        state = await dt.update_from_outcomes(_fake_pool(_outcome_rows(3, 2)), "t1")
        assert state is None

    async def test_db_error_returns_none_never_raises(self):
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=RuntimeError("db down"))
        dt = DynamicThresholds(redis=_FakeRedis())
        assert await dt.update_from_outcomes(pool, "t1") is None

    async def test_update_persists_to_redis(self):
        redis = _FakeRedis()
        dt = DynamicThresholds(redis=redis)
        await dt.update_from_outcomes(_fake_pool(_outcome_rows(45, 5)), "t1")
        saved = json.loads(redis.store[_REDIS_KEY])
        assert saved["outcome_count"] == 50
        assert saved["precision"] == 0.9


class TestReadPath:
    async def test_redis_unavailable_falls_back_to_env_defaults(self, monkeypatch):
        monkeypatch.setenv("AEGIS_SCRAPE_CONFIDENCE_THRESHOLD", "0.77")
        dt = DynamicThresholds(redis=None)
        assert await dt.get_confidence_gate() == 0.77
        assert await dt.get_velocity_slope() == 2.0
        assert await dt.get_comply_block() == 0.70

    async def test_redis_error_falls_back_to_defaults(self):
        redis = MagicMock()
        redis.get = AsyncMock(side_effect=RuntimeError("conn refused"))
        dt = DynamicThresholds(redis=redis)
        assert await dt.get_confidence_gate() == 0.85

    async def test_one_hour_cache_skips_redis_on_second_get(self):
        redis = _FakeRedis()
        redis.store[_REDIS_KEY] = json.dumps(
            {
                "confidence_gate": 0.70,
                "velocity_slope": 3.0,
                "comply_block": 0.80,
                "updated_at": datetime.now(UTC).isoformat(),
                "outcome_count": 50,
                "precision": 0.8,
            }
        )
        dt = DynamicThresholds(redis=redis)
        assert await dt.get_confidence_gate() == 0.70
        assert await dt.get_velocity_slope() == 3.0
        assert await dt.get_comply_block() == 0.80
        assert redis.get_calls == 1  # served from local cache after first hit

    async def test_fallback_wins_until_first_adaptive_update(self):
        """Caller-supplied fallback beats env defaults while outcome_count == 0."""
        dt = DynamicThresholds(redis=None)
        assert await dt.get_comply_block(fallback=0.33) == 0.33
        # Once Redis holds an adapted state, the adaptive value wins.
        redis = _FakeRedis()
        redis.store[_REDIS_KEY] = json.dumps(
            {
                "confidence_gate": 0.70,
                "velocity_slope": 3.0,
                "comply_block": 0.80,
                "updated_at": datetime.now(UTC).isoformat(),
                "outcome_count": 50,
                "precision": 0.8,
            }
        )
        dt2 = DynamicThresholds(redis=redis)
        assert await dt2.get_comply_block(fallback=0.33) == 0.80

    async def test_invalidate_cache_rereads_redis(self):
        redis = _FakeRedis()
        dt = DynamicThresholds(redis=redis)
        await dt.get_confidence_gate()
        dt.invalidate_cache()
        await dt.get_confidence_gate()
        assert redis.get_calls == 2

    def test_threshold_state_is_dataclass(self):
        st = ThresholdState(
            confidence_gate=0.85,
            velocity_slope=2.0,
            comply_block=0.70,
            updated_at=datetime.now(UTC),
            outcome_count=0,
            precision=0.0,
        )
        assert st.confidence_gate == 0.85


class TestSchedulerIntegration:
    async def test_weekly_update_job_registered(self):
        """job_threshold_update exists and degrades gracefully without infra."""
        from aegis.scheduler.autonomous import job_threshold_update

        # No postgres/redis in unit tests — must not raise.
        await job_threshold_update()


class TestBrain3ConsumerPropagation:
    """BRAIN-3: a threshold change from update_from_outcomes() must actually
    flow through to the three consumers (confidence, analytics, compliance).
    """

    async def test_read_path_reflects_update(self):
        """After update_from_outcomes(), the get_* readers return the new
        values — not the stale defaults."""
        redis = _FakeRedis()
        dt = DynamicThresholds(redis=redis)
        before_gate = await dt.get_confidence_gate()
        before_slope = await dt.get_velocity_slope()
        before_block = await dt.get_comply_block()

        # 90% winners → precision > target → relax all three.
        state = await dt.update_from_outcomes(_fake_pool(_outcome_rows(45, 5)), "t1")
        assert state is not None

        after_gate = await dt.get_confidence_gate()
        after_slope = await dt.get_velocity_slope()
        after_block = await dt.get_comply_block()

        # Confidence + velocity relax DOWN; compliance block relaxes UP.
        assert after_gate == pytest.approx(before_gate - 0.02)
        assert after_slope == pytest.approx(before_slope - 0.02)
        assert after_block == pytest.approx(before_block + 0.02)
        # Sanity: the readers genuinely changed.
        assert after_gate != before_gate

    async def test_confidence_consumer_injection(self):
        """The new gate can be injected into aegis.scrape.confidence and is
        then used as the score_batch default."""
        from aegis.scrape import confidence

        redis = _FakeRedis()
        dt = DynamicThresholds(redis=redis)
        state = await dt.update_from_outcomes(_fake_pool(_outcome_rows(45, 5)), "t1")
        assert state is not None

        try:
            new_gate = await dt.get_confidence_gate()
            confidence.set_confidence_threshold(new_gate)
            assert confidence.get_confidence_threshold() == pytest.approx(new_gate)
            # score_batch with threshold=None now uses the injected gate.
            result = confidence.score_batch([], threshold=None)
            assert result.threshold == pytest.approx(new_gate)
        finally:
            confidence.set_confidence_threshold(None)
        # Cleared → falls back to the static default.
        assert (
            confidence.get_confidence_threshold()
            == confidence.DEFAULT_CONFIDENCE_THRESHOLD
        )

    async def test_analytics_consumer_injection(self):
        """The new velocity slope can be injected into aegis.scrape.analytics."""
        from aegis.scrape import analytics

        redis = _FakeRedis()
        dt = DynamicThresholds(redis=redis)
        state = await dt.update_from_outcomes(_fake_pool(_outcome_rows(45, 5)), "t1")
        assert state is not None

        try:
            new_slope = await dt.get_velocity_slope()
            analytics.set_high_priority_slope(new_slope)
            assert analytics.get_high_priority_slope() == pytest.approx(new_slope)
        finally:
            analytics.set_high_priority_slope(None)
        assert analytics.get_high_priority_slope() == analytics.HIGH_PRIORITY_SLOPE
