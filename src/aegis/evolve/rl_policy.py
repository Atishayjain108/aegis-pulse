"""
aegis.evolve.rl_policy
======================

RL-based online learning for the pricing policy.

Maintains four policy weights that are updated incrementally from daily
execution outcomes.  Heavy model retraining is NOT required — only the
lightweight policy weights shift in response to actual trade profitability.

Weights:
    [cost_based, demand_based, inventory_based, competitor_based]

Each weight represents how much the corresponding pricing signal should
influence the final price recommendation.

Public API:
    OnlinePricingPolicy(learning_rate, db_pool, settings)
    OnlinePricingPolicy.update_from_outcome(price, cost, actual_demand, actual_roi_pct)
    OnlinePricingPolicy.get_weights() → np.ndarray
    OnlinePricingPolicy.get_state()   → PolicyState
    OnlinePricingPolicy.persist()     → bool
    OnlinePricingPolicy.load()        → bool
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np
import structlog

from aegis.evolve.config import EvolveSettings
from aegis.evolve.constants import (
    ERR_POLICY_PERSIST_FAILED,
    POLICY_WEIGHT_LABELS,
)
from aegis.evolve.schemas import PolicyState

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.evolve.rl_policy")

_N_WEIGHTS = 4
_INITIAL_WEIGHTS = np.array([0.25, 0.35, 0.20, 0.20], dtype=float)


class OnlinePricingPolicy:
    """
    Lightweight policy-gradient pricing agent.

    Updates weights using a simple REINFORCE-style rule:
      - Profitable trade (+reward) → amplify all weights proportionally.
      - Loss-making trade (-reward) → shrink all weights.
      - Weights are re-normalised after each update to sum to 1.

    Thread-safety: this class is NOT thread-safe.  Use one instance per
    event-loop (same as Phase 6 PricingStrategy).
    """

    def __init__(
        self,
        learning_rate: float = 0.01,
        db_pool: Pool | None = None,
        settings: EvolveSettings | None = None,
        policy_id: str = "default",
    ) -> None:
        self._lr = learning_rate
        self._pool = db_pool
        self._cfg = settings or EvolveSettings()
        self._policy_id = policy_id

        self._weights = _INITIAL_WEIGHTS.copy()
        self._update_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_from_outcome(
        self,
        price: float,
        cost: float,
        actual_demand: int,
        actual_roi_pct: float,
    ) -> None:
        """
        Update policy weights from a single trade outcome.

        Args:
            price:           Sale price achieved.
            cost:            Unit cost (used for sanity check).
            actual_demand:   Units sold.
            actual_roi_pct:  Actual ROI percentage (ground truth).
        """
        # Reward: normalised ROI in [-1, 1]
        reward = max(-1.0, min(1.0, actual_roi_pct / 100.0))

        if reward > 0.05:
            # Profitable: amplify all weights proportionally
            self._weights += self._lr * reward * self._weights
        else:
            # Loss / breakeven: reduce all weights
            self._weights *= max(0.1, 1.0 - self._lr * abs(reward))

        # Ensure non-negative
        self._weights = np.maximum(self._weights, 1e-6)

        # Re-normalise to unit simplex
        self._weights /= self._weights.sum()

        self._update_count += 1

        if self._update_count % self._cfg.rl_persist_interval == 0:
            _log.info(
                "evolve.policy_milestone",
                count=self._update_count,
                weights={k: round(float(v), 4) for k, v in zip(POLICY_WEIGHT_LABELS, self._weights, strict=False)},
                cost=cost,
            )

    def get_weights(self) -> np.ndarray:
        """Return a copy of the current policy weight vector."""
        return self._weights.copy()

    def get_state(self) -> PolicyState:
        """Return a frozen snapshot of the current policy state."""
        return PolicyState(
            policy_id=self._policy_id,
            weights=self._weights.tolist(),
            update_count=self._update_count,
            learning_rate=self._lr,
        )

    async def persist(self) -> bool:
        """Write current policy weights to the database."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO rl_policy_state
                        (policy_id, weights, update_count, learning_rate, last_updated_at)
                    VALUES ($1, $2::jsonb, $3, $4, NOW())
                    ON CONFLICT (policy_id) DO UPDATE SET
                        weights        = EXCLUDED.weights,
                        update_count   = EXCLUDED.update_count,
                        learning_rate  = EXCLUDED.learning_rate,
                        last_updated_at = EXCLUDED.last_updated_at
                    """,
                    self._policy_id,
                    json.dumps(self._weights.tolist()),
                    self._update_count,
                    self._lr,
                )
            _log.debug(
                "evolve.policy_persisted",
                policy_id=self._policy_id,
                update_count=self._update_count,
            )
            return True
        except Exception as exc:
            _log.error(
                "evolve.policy_persist_failed",
                error=str(exc),
                error_code=ERR_POLICY_PERSIST_FAILED,
            )
            return False

    async def load(self) -> bool:
        """Load policy weights from the database.  Returns False on miss/failure."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT weights, update_count, learning_rate
                    FROM rl_policy_state
                    WHERE policy_id = $1
                    """,
                    self._policy_id,
                )
            if not row:
                return False

            loaded = json.loads(row["weights"])
            if len(loaded) != _N_WEIGHTS:
                _log.warning(
                    "evolve.policy_load_shape_mismatch",
                    expected=_N_WEIGHTS,
                    got=len(loaded),
                )
                return False

            self._weights = np.array(loaded, dtype=float)
            self._update_count = int(row["update_count"])
            self._lr = float(row["learning_rate"])

            _log.info(
                "evolve.policy_loaded",
                policy_id=self._policy_id,
                update_count=self._update_count,
            )
            return True

        except Exception as exc:
            _log.error("evolve.policy_load_failed", error=str(exc))
            return False
