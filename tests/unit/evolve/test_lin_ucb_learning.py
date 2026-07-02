"""FIX-2 (forensic audit): proof that LinUCBPricingPolicy actually learns.

These tests are the executable evidence that the pricing policy attributes
reward to the specific arm that produced it and shifts its preference toward
the rewarding arm — the thing the old REINFORCE OnlinePricingPolicy provably
did NOT do.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from aegis.evolve.rl_policy import LinUCBPricingPolicy


class TestLinUCBLearning:
    def test_lin_ucb_actually_learns(self) -> None:
        """LinUCB increases preference for the consistently-rewarding arm."""
        policy = LinUCBPricingPolicy(n_arms=4, context_dim=5)
        context = [0.5] * 5
        initial_weights = policy.get_arm_weights(context=context).copy()

        # 60 rounds: arm 2 always pays 0.8, every other arm pays 0.2.
        for _ in range(60):
            arm = policy.select_arm(context=context)
            reward = 0.8 if arm == 2 else 0.2
            policy.update(arm=arm, reward=reward, context=context)

        final_weights = policy.get_arm_weights(context=context)

        # The rewarding arm's preference must have increased.
        assert final_weights[2] > initial_weights[2], (
            "LinUCB should raise the probability of the rewarding arm"
        )

        # And it should now be selected the majority of the time.
        selections = [policy.select_arm(context=context) for _ in range(100)]
        arm_2_rate = selections.count(2) / 100
        assert arm_2_rate > 0.5, (
            f"LinUCB should prefer the rewarding arm; arm 2 chosen "
            f"only {arm_2_rate:.0%} of the time"
        )

    def test_reward_attributed_only_to_played_arm(self) -> None:
        """Updating arm 1 must not change the (A, b) state of any other arm."""
        policy = LinUCBPricingPolicy(n_arms=3, context_dim=4)
        before = [m.copy() for m in policy._a_mats]
        policy.update(arm=1, reward=1.0, context=[1.0, 0.0, 0.0, 0.0])
        # Arm 1 changed.
        assert not np.allclose(policy._a_mats[1], before[1])
        # Arms 0 and 2 are untouched.
        assert np.allclose(policy._a_mats[0], before[0])
        assert np.allclose(policy._a_mats[2], before[2])

    def test_get_arm_weights_is_distribution(self) -> None:
        policy = LinUCBPricingPolicy(n_arms=4, context_dim=3)
        w = policy.get_arm_weights(context=[0.1, 0.2, 0.3])
        assert w.shape == (4,)
        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        assert (w >= 0).all()

    def test_context_dim_validation(self) -> None:
        policy = LinUCBPricingPolicy(n_arms=2, context_dim=3)
        with pytest.raises(ValueError, match="expected 3"):
            policy.select_arm(context=[0.1, 0.2])

    def test_invalid_arm_rejected(self) -> None:
        policy = LinUCBPricingPolicy(n_arms=2, context_dim=2)
        with pytest.raises(ValueError, match="out of range"):
            policy.update(arm=5, reward=1.0, context=[0.0, 0.0])

    def test_constructor_validation(self) -> None:
        with pytest.raises(ValueError):
            LinUCBPricingPolicy(n_arms=0, context_dim=3)
        with pytest.raises(ValueError):
            LinUCBPricingPolicy(n_arms=2, context_dim=0)

    def test_update_count_increments(self) -> None:
        policy = LinUCBPricingPolicy(n_arms=2, context_dim=2)
        assert policy.update_count == 0
        policy.update(arm=0, reward=0.5, context=[1.0, 1.0])
        assert policy.update_count == 1

    def test_get_state_snapshot(self) -> None:
        policy = LinUCBPricingPolicy(n_arms=4, context_dim=4, policy_id="p-x")
        policy.update(arm=0, reward=1.0, context=[1.0, 0.0, 0.0, 0.0])
        state = policy.get_state()
        assert state.policy_id == "p-x"
        assert state.update_count == 1
        assert len(state.weights) == 4
        assert sum(state.weights) == pytest.approx(1.0, abs=1e-9)


class TestLinUCBPersistence:
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
    async def test_persist_no_pool(self) -> None:
        policy = LinUCBPricingPolicy()
        assert await policy.persist() is False

    @pytest.mark.asyncio
    async def test_persist_success(self) -> None:
        pool, conn = self._make_pool()
        policy = LinUCBPricingPolicy(db_pool=pool)
        policy.update(arm=0, reward=1.0, context=[0.0] * 5)
        assert await policy.persist() is True
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_no_pool(self) -> None:
        policy = LinUCBPricingPolicy()
        assert await policy.load() is False

    @pytest.mark.asyncio
    async def test_load_round_trip(self) -> None:
        import json

        # Train one policy, capture its serialised state.
        src = LinUCBPricingPolicy(n_arms=3, context_dim=4)
        for i in range(10):
            src.update(arm=i % 3, reward=float(i % 2), context=[0.1, 0.2, 0.3, 0.4])
        payload = {
            "n_arms": src._n_arms,
            "context_dim": src._context_dim,
            "alpha": src._alpha,
            "A": [m.tolist() for m in src._a_mats],
            "b": [v.tolist() for v in src._b_vecs],
        }
        row = {"state": json.dumps(payload), "update_count": src.update_count}

        pool, conn = self._make_pool()
        conn.fetchrow = AsyncMock(return_value=row)
        dst = LinUCBPricingPolicy(n_arms=3, context_dim=4, db_pool=pool)
        assert await dst.load() is True
        assert dst.update_count == src.update_count
        for a in range(3):
            np.testing.assert_allclose(dst._a_mats[a], src._a_mats[a])

    @pytest.mark.asyncio
    async def test_load_shape_mismatch(self) -> None:
        import json

        bad = {"n_arms": 2, "context_dim": 2, "alpha": 1.0,
               "A": [[[1.0, 0.0], [0.0, 1.0]]], "b": [[0.0, 0.0]]}  # only 1 arm
        row = {"state": json.dumps(bad), "update_count": 0}
        pool, conn = self._make_pool()
        conn.fetchrow = AsyncMock(return_value=row)
        policy = LinUCBPricingPolicy(n_arms=2, context_dim=2, db_pool=pool)
        assert await policy.load() is False
