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


# ---------------------------------------------------------------------------
# FIX-2 (forensic audit): real contextual-bandit pricing policy.
#
# The forensic audit proved that ``OnlinePricingPolicy`` below is NOT learning:
# it scales all four weights by the same scalar and renormalises, so the
# relative weights barely move and no reward is ever attributed to a specific
# action. ``LinUCBPricingPolicy`` replaces that theater with a genuine LinUCB
# contextual bandit (Li et al., 2010, "A Contextual-Bandit Approach to
# Personalized News Article Recommendation").
#
# Each *arm* is a discrete pricing strategy (e.g. "15% markup + free shipping").
# The *context* is the per-decision feature vector (product category one-hot,
# trend phase, competitor price ratio, inventory level). The *reward* is the
# realized gross margin from the SettlementManager. LinUCB maintains a separate
# (A, b) pair per arm, attributes reward only to the arm actually played, and
# provably balances exploration vs exploitation via the upper-confidence bound.
# ---------------------------------------------------------------------------


class LinUCBPricingPolicy:
    """Disjoint LinUCB contextual bandit for pricing-strategy selection.

    Parameters
    ----------
    n_arms:
        Number of discrete pricing strategies to choose between.
    context_dim:
        Dimensionality of the context (feature) vector supplied at decision
        and update time.
    alpha:
        Exploration coefficient. Higher → more exploration. The classic LinUCB
        default is ``1 + sqrt(ln(2/delta)/2)``; 1.0 is a sound general value.
    policy_id:
        Identifier used when persisting/loading state from the database.

    Notes
    -----
    Per arm ``a`` LinUCB keeps:
        A_a = D_a^T D_a + I   (context_dim × context_dim ridge matrix)
        b_a = D_a^T c_a       (context_dim reward-weighted context sum)
    The expected reward for arm ``a`` in context ``x`` is ``theta_a · x`` with
    ``theta_a = A_a^{-1} b_a``, and the UCB padding is
    ``alpha · sqrt(x^T A_a^{-1} x)``. This class is NOT thread-safe; use one
    instance per event loop.
    """

    def __init__(
        self,
        n_arms: int = 4,
        context_dim: int = 5,
        alpha: float = 1.0,
        db_pool: Pool | None = None,
        settings: EvolveSettings | None = None,
        policy_id: str = "linucb_default",
    ) -> None:
        if n_arms < 1:
            raise ValueError("n_arms must be >= 1")
        if context_dim < 1:
            raise ValueError("context_dim must be >= 1")

        self._n_arms = int(n_arms)
        self._context_dim = int(context_dim)
        self._alpha = float(alpha)
        self._pool = db_pool
        self._cfg = settings or EvolveSettings()
        self._policy_id = policy_id

        # Ridge-regularised design matrices and reward vectors, one per arm.
        self._a_mats = [np.identity(self._context_dim, dtype=float) for _ in range(self._n_arms)]
        self._b_vecs = [np.zeros(self._context_dim, dtype=float) for _ in range(self._n_arms)]
        self._update_count = 0

    # ------------------------------------------------------------------
    # Decision + learning
    # ------------------------------------------------------------------

    def _as_context(self, context: object) -> np.ndarray:
        x = np.asarray(context, dtype=float).reshape(-1)
        if x.shape[0] != self._context_dim:
            raise ValueError(
                f"context has dim {x.shape[0]}, expected {self._context_dim}"
            )
        return x

    def _arm_estimates(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (expected_reward, ucb_padding) arrays over all arms."""
        means = np.empty(self._n_arms, dtype=float)
        padding = np.empty(self._n_arms, dtype=float)
        for a in range(self._n_arms):
            a_inv = np.linalg.inv(self._a_mats[a])
            theta = a_inv @ self._b_vecs[a]
            means[a] = float(theta @ x)
            padding[a] = self._alpha * float(np.sqrt(max(0.0, x @ a_inv @ x)))
        return means, padding

    def select_arm(self, context: object) -> int:
        """Return the index of the arm with the highest UCB score for *context*."""
        x = self._as_context(context)
        means, padding = self._arm_estimates(x)
        ucb = means + padding
        return int(np.argmax(ucb))

    def get_arm_weights(self, context: object) -> np.ndarray:
        """Return a normalised preference distribution over arms for *context*.

        Derived from each arm's estimated expected reward (``theta_a · x``)
        passed through a softmax. Useful for inspection/telemetry — it reveals
        which pricing strategy the policy currently favours in a given context.
        Unlike the old REINFORCE weights, these genuinely shift as reward is
        attributed to specific arms.
        """
        x = self._as_context(context)
        means, _ = self._arm_estimates(x)
        # Numerically stable softmax.
        shifted = means - means.max()
        exp = np.exp(shifted)
        return exp / exp.sum()

    def update(self, arm: int, reward: float, context: object) -> None:
        """Attribute *reward* for the played *arm* in *context* to that arm only.

        ``reward`` should be the realized gross margin (any real-valued scale;
        normalising to roughly [-1, 1] keeps the UCB padding well-conditioned).
        """
        if not 0 <= arm < self._n_arms:
            raise ValueError(f"arm {arm} out of range [0, {self._n_arms})")
        x = self._as_context(context)
        self._a_mats[arm] += np.outer(x, x)
        self._b_vecs[arm] += float(reward) * x
        self._update_count += 1

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def update_count(self) -> int:
        return self._update_count

    def get_state(self) -> PolicyState:
        """Return a PolicyState snapshot (weights = uniform-context preference)."""
        uniform = np.full(self._context_dim, 1.0 / self._context_dim)
        weights = self.get_arm_weights(uniform).tolist()
        return PolicyState(
            policy_id=self._policy_id,
            weights=weights,
            update_count=self._update_count,
            learning_rate=self._alpha,
        )

    # ------------------------------------------------------------------
    # Persistence (per-arm A/b matrices → TimescaleDB, migration 0014)
    # ------------------------------------------------------------------

    async def persist(self) -> bool:
        """Write all per-arm (A, b) matrices to ``lin_ucb_state``."""
        if self._pool is None:
            return False
        try:
            payload = {
                "n_arms": self._n_arms,
                "context_dim": self._context_dim,
                "alpha": self._alpha,
                "A": [m.tolist() for m in self._a_mats],
                "b": [v.tolist() for v in self._b_vecs],
            }
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO lin_ucb_state
                        (policy_id, state, update_count, last_updated_at)
                    VALUES ($1, $2::jsonb, $3, NOW())
                    ON CONFLICT (policy_id) DO UPDATE SET
                        state           = EXCLUDED.state,
                        update_count    = EXCLUDED.update_count,
                        last_updated_at = EXCLUDED.last_updated_at
                    """,
                    self._policy_id,
                    json.dumps(payload),
                    self._update_count,
                )
            return True
        except Exception as exc:
            _log.error(
                "evolve.linucb_persist_failed",
                error=str(exc),
                error_code=ERR_POLICY_PERSIST_FAILED,
            )
            return False

    async def load(self) -> bool:
        """Load per-arm (A, b) matrices from ``lin_ucb_state``."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT state, update_count FROM lin_ucb_state WHERE policy_id = $1",
                    self._policy_id,
                )
            if not row:
                return False
            state = json.loads(row["state"])
            n_arms = int(state["n_arms"])
            context_dim = int(state["context_dim"])
            a_mats = [np.asarray(m, dtype=float) for m in state["A"]]
            b_vecs = [np.asarray(v, dtype=float) for v in state["b"]]
            if (
                len(a_mats) != n_arms
                or len(b_vecs) != n_arms
                or any(m.shape != (context_dim, context_dim) for m in a_mats)
            ):
                _log.warning("evolve.linucb_load_shape_mismatch", policy_id=self._policy_id)
                return False
            self._n_arms = n_arms
            self._context_dim = context_dim
            self._alpha = float(state.get("alpha", self._alpha))
            self._a_mats = a_mats
            self._b_vecs = b_vecs
            self._update_count = int(row["update_count"])
            return True
        except Exception as exc:
            _log.error("evolve.linucb_load_failed", error=str(exc))
            return False


class OnlinePricingPolicy:
    """
    DEPRECATED (forensic audit FIX-2): retained only for backward compatibility.

    This "REINFORCE" rule does NOT learn: it multiplies every weight by the same
    scalar then renormalises to the simplex, so the relative weights are
    essentially unchanged and no reward is ever attributed to a specific pricing
    signal. Use :class:`LinUCBPricingPolicy` instead — a real contextual bandit
    that attributes realized margin to the specific pricing arm that produced it.

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
