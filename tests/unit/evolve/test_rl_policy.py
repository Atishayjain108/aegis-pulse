"""Unit tests for OnlinePricingPolicy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from aegis.evolve.rl_policy import _N_WEIGHTS, OnlinePricingPolicy


class TestOnlinePricingPolicyCore:
    def test_initial_weights_sum_to_one(self) -> None:
        policy = OnlinePricingPolicy()
        assert policy.get_weights().sum() == pytest.approx(1.0, abs=1e-6)

    def test_initial_weights_count(self) -> None:
        policy = OnlinePricingPolicy()
        assert len(policy.get_weights()) == _N_WEIGHTS

    def test_update_profitable_increases_weights(self) -> None:
        policy = OnlinePricingPolicy(learning_rate=0.1)
        policy.update_from_outcome(price=50.0, cost=25.0, actual_demand=10, actual_roi_pct=100.0)
        # Weights should still sum to 1 after profitable update
        assert policy.get_weights().sum() == pytest.approx(1.0, abs=1e-6)

    def test_update_loss_decreases_weights(self) -> None:
        policy = OnlinePricingPolicy(learning_rate=0.1)
        policy.update_from_outcome(price=20.0, cost=30.0, actual_demand=5, actual_roi_pct=-50.0)
        # Sum still normalises to 1
        assert policy.get_weights().sum() == pytest.approx(1.0, abs=1e-6)

    def test_weights_always_positive(self) -> None:
        policy = OnlinePricingPolicy(learning_rate=0.5)
        for _ in range(20):
            policy.update_from_outcome(
                price=10.0, cost=20.0, actual_demand=0, actual_roi_pct=-100.0
            )
        assert (policy.get_weights() > 0).all()

    def test_update_count_increments(self) -> None:
        policy = OnlinePricingPolicy()
        assert policy._update_count == 0
        policy.update_from_outcome(price=50.0, cost=25.0, actual_demand=10, actual_roi_pct=50.0)
        assert policy._update_count == 1

    def test_multiple_updates_accumulate(self) -> None:
        policy = OnlinePricingPolicy(learning_rate=0.05)
        for _ in range(50):
            policy.update_from_outcome(price=50.0, cost=25.0, actual_demand=10, actual_roi_pct=80.0)
        assert policy._update_count == 50
        assert policy.get_weights().sum() == pytest.approx(1.0, abs=1e-6)

    def test_get_weights_returns_copy(self) -> None:
        policy = OnlinePricingPolicy()
        w1 = policy.get_weights()
        w1[0] = 9999.0
        w2 = policy.get_weights()
        assert w2[0] != 9999.0

    def test_get_state_snapshot(self) -> None:
        policy = OnlinePricingPolicy(learning_rate=0.02, policy_id="test-p")
        policy.update_from_outcome(price=40.0, cost=20.0, actual_demand=5, actual_roi_pct=30.0)
        state = policy.get_state()
        assert state.policy_id == "test-p"
        assert state.update_count == 1
        assert state.learning_rate == pytest.approx(0.02)
        assert len(state.weights) == _N_WEIGHTS
        assert sum(state.weights) == pytest.approx(1.0, abs=1e-6)

    def test_breakeven_trade_normalises(self) -> None:
        policy = OnlinePricingPolicy()
        policy.update_from_outcome(price=30.0, cost=28.0, actual_demand=2, actual_roi_pct=0.0)
        assert policy.get_weights().sum() == pytest.approx(1.0, abs=1e-6)

    def test_extreme_roi_clamped(self) -> None:
        policy = OnlinePricingPolicy()
        # 10000% ROI should still work without overflow
        policy.update_from_outcome(price=50.0, cost=0.01, actual_demand=100, actual_roi_pct=10000.0)
        assert policy.get_weights().sum() == pytest.approx(1.0, abs=1e-6)

    def test_deterministic_with_same_seed_sequence(self) -> None:
        """Same sequence of updates → same final weights."""
        def run_updates() -> np.ndarray:
            policy = OnlinePricingPolicy(learning_rate=0.05)
            for roi in [10.0, -5.0, 30.0, 0.0, 50.0]:
                policy.update_from_outcome(price=50.0, cost=25.0, actual_demand=5, actual_roi_pct=roi)
            return policy.get_weights()

        w1 = run_updates()
        w2 = run_updates()
        np.testing.assert_allclose(w1, w2)


class TestOnlinePricingPolicyPersistence:
    def _make_pool(self) -> tuple:
        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        return pool, conn

    @pytest.mark.asyncio
    async def test_persist_success(self) -> None:
        pool, conn = self._make_pool()
        policy = OnlinePricingPolicy(db_pool=pool)
        ok = await policy.persist()
        assert ok is True
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_no_pool(self) -> None:
        policy = OnlinePricingPolicy()
        ok = await policy.persist()
        assert ok is False

    @pytest.mark.asyncio
    async def test_persist_db_failure(self) -> None:
        pool, conn = self._make_pool()
        conn.execute = AsyncMock(side_effect=RuntimeError("write error"))
        policy = OnlinePricingPolicy(db_pool=pool)
        ok = await policy.persist()
        assert ok is False

    @pytest.mark.asyncio
    async def test_load_no_pool(self) -> None:
        policy = OnlinePricingPolicy()
        ok = await policy.load()
        assert ok is False

    @pytest.mark.asyncio
    async def test_load_no_row(self) -> None:
        pool, conn = self._make_pool()
        conn.fetchrow = AsyncMock(return_value=None)
        policy = OnlinePricingPolicy(db_pool=pool)
        ok = await policy.load()
        assert ok is False

    @pytest.mark.asyncio
    async def test_load_restores_state(self) -> None:
        import json
        weights = [0.30, 0.40, 0.15, 0.15]
        row = MagicMock()
        row.__getitem__ = lambda self, k: {
            "weights": json.dumps(weights),
            "update_count": 42,
            "learning_rate": 0.02,
        }[k]
        pool, conn = self._make_pool()
        conn.fetchrow = AsyncMock(return_value=row)
        policy = OnlinePricingPolicy(db_pool=pool)
        ok = await policy.load()
        assert ok is True
        np.testing.assert_allclose(policy.get_weights(), weights)
        assert policy._update_count == 42
        assert policy._lr == pytest.approx(0.02)

    @pytest.mark.asyncio
    async def test_load_shape_mismatch_returns_false(self) -> None:
        import json
        row = MagicMock()
        row.__getitem__ = lambda self, k: {
            "weights": json.dumps([0.5, 0.5]),  # wrong size
            "update_count": 0,
            "learning_rate": 0.01,
        }[k]
        pool, conn = self._make_pool()
        conn.fetchrow = AsyncMock(return_value=row)
        policy = OnlinePricingPolicy(db_pool=pool)
        ok = await policy.load()
        assert ok is False

    @pytest.mark.asyncio
    async def test_load_db_failure(self) -> None:
        pool, conn = self._make_pool()
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("network error"))
        policy = OnlinePricingPolicy(db_pool=pool)
        ok = await policy.load()
        assert ok is False
