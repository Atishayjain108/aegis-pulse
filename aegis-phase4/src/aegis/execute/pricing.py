"""Dynamic pricing engine — RL-optimized, A/B tested, cost-aware.

Produces a recommended sale price for a product given:
  - cost of goods (COGS)
  - Phase 1 demand velocity (units/hour)
  - current inventory level
  - competitor price list

Optimization targets (multi-objective weighted sum):
  1. Cost-based floor: never sell below MIN_MARGIN_PCT gross margin.
  2. Demand signal: high-velocity trends support a premium.
  3. Inventory pressure: low stock → price up; overstock → price down.
  4. Competitive position: stay in top-3 cheapest without undercutting to zero.

A/B testing: PRICING_AB_TEST_RATE fraction of calls receive a ±5% perturbation
to gather elasticity signal. Outcomes should be fed back via `update_from_outcome`
to let the policy weights adapt over time.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import ClassVar, Final

import structlog

from aegis.execute.constants import (
    PRICING_AB_TEST_DELTAS,
    PRICING_AB_TEST_RATE,
    PRICING_MAX_MARKUP_FACTOR,
    PRICING_MIN_MARGIN_PCT,
)

# NOTE: `PRICING_AB_TEST_RATE` / `PRICING_AB_TEST_DELTAS` are re-exported simply
# by importing them above (they become module attributes). The previous
# `X: Final = X` self-assignment was a no-op flagged by ruff PLW0127 — removed.

_log = structlog.get_logger(__name__)

_EPSILON: Final[float] = 1e-9  # guards against division by zero


class PricingStrategy:
    """Multi-objective pricing policy with lightweight online learning.

    One instance per product SKU; not thread-safe (holds mutable state for
    online weight updates — wrap with a lock if shared across coroutines).
    """

    # Weights: [cost-based, demand-based, inventory-based, competitor-based].
    _DEFAULT_WEIGHTS: Final[tuple[float, float, float, float]] = (0.25, 0.35, 0.20, 0.20)

    # PASS3-3D: class-wide cache of Phase 9 RL policy weights (6-hour TTL).
    # Refreshed via the async `_get_policy_weights()`; new instances seed
    # their per-SKU weights from this cache when it is fresh.
    _POLICY_TTL_S: ClassVar[float] = 6.0 * 3600
    _policy_weights: ClassVar[list[float] | None] = None
    _policy_loaded_at: ClassVar[datetime | None] = None

    __slots__ = ("sku", "_weights", "_price_history", "_demand_history", "_rng")

    def __init__(self, sku: str, *, seed: int | None = None) -> None:
        self.sku = sku
        cached = type(self)._cached_policy_weights()
        self._weights = list(cached) if cached is not None else list(self._DEFAULT_WEIGHTS)
        self._price_history: list[float] = []
        self._demand_history: list[float] = []
        self._rng = random.Random(seed)  # isolated RNG for reproducibility

    # ------------------------------------------------------------------
    # PASS3-3D: Phase 9 policy weight loading
    # ------------------------------------------------------------------

    @classmethod
    def _cached_policy_weights(cls) -> list[float] | None:
        """Return the class-wide policy weights if still within TTL."""
        if (
            cls._policy_weights is not None
            and cls._policy_loaded_at is not None
            and (datetime.now(UTC) - cls._policy_loaded_at).total_seconds()
            < cls._POLICY_TTL_S
        ):
            return list(cls._policy_weights)
        return None

    @classmethod
    async def _get_policy_weights(cls, db_pool: object | None = None) -> list[float]:
        """Load RL pricing weights from the Phase 9 OnlinePricingPolicy.

        4 weights: [cost_based, demand_based, inventory_based, competitor_based].
        Cached class-wide for 6 hours — not per-instance. Falls back to
        `_DEFAULT_WEIGHTS` when Phase 9 is unavailable or no persisted policy
        exists yet (the failure is never cached, so the next call retries).
        """
        cached = cls._cached_policy_weights()
        if cached is not None:
            return cached

        try:
            from aegis.evolve.rl_policy import OnlinePricingPolicy

            policy = OnlinePricingPolicy(db_pool=db_pool)  # type: ignore[arg-type]
            loaded = await policy.load()
            weights = [float(w) for w in policy.get_weights()]
            if loaded and len(weights) == len(cls._DEFAULT_WEIGHTS):
                cls._policy_weights = weights
                cls._policy_loaded_at = datetime.now(UTC)
                _log.debug(
                    "execute.pricing.policy_weights_refreshed",
                    weights=[round(w, 4) for w in weights],
                )
                return list(weights)
        except Exception as exc:
            _log.warning("execute.pricing.policy_weights_load_failed", error=str(exc))

        return list(cls._DEFAULT_WEIGHTS)

    @classmethod
    def _reset_policy_cache(cls) -> None:
        """Drop the cached policy weights (test/maintenance hook)."""
        cls._policy_weights = None
        cls._policy_loaded_at = None

    # ------------------------------------------------------------------
    # Core computation
    # ------------------------------------------------------------------

    def compute_price(
        self,
        unit_cost: float,
        demand_units_per_h: float,
        inventory_level: int,
        competitor_prices: list[float] | None = None,
    ) -> float:
        """Return an optimal sale price in USD.

        All inputs must be non-negative. `unit_cost` must be > 0.
        Returns a price that is always ≥ the minimum margin floor.
        """
        if unit_cost <= 0:
            raise ValueError(f"unit_cost must be positive, got {unit_cost}")

        candidates = (
            _cost_based_price(unit_cost),
            _demand_based_price(unit_cost, demand_units_per_h),
            _inventory_based_price(unit_cost, inventory_level),
            _competitor_based_price(unit_cost, competitor_prices or []),
        )

        weighted = sum(w * c for w, c in zip(self._weights, candidates, strict=False))

        # Enforce bounds.
        floor = unit_cost / (1 - PRICING_MIN_MARGIN_PCT + _EPSILON)
        ceiling = unit_cost * PRICING_MAX_MARKUP_FACTOR
        price = max(floor, min(ceiling, weighted))

        # A/B perturbation for elasticity learning.
        price = self._maybe_ab_test(price)

        self._price_history.append(price)
        self._demand_history.append(demand_units_per_h)

        _log.info(
            "execute.pricing.computed",
            sku=self.sku,
            unit_cost=round(unit_cost, 2),
            price=round(price, 2),
            margin_pct=round((price - unit_cost) / price * 100, 1),
            demand_h=demand_units_per_h,
            inventory=inventory_level,
        )
        return round(price, 4)

    def update_from_outcome(self, price: float, actual_demand: float, pnl: float) -> None:
        """Lightweight policy gradient: nudge weights toward profitable signals."""
        if pnl <= 0:
            return
        # Identify which candidate was closest to the chosen price.
        # Amplify its weight by a small learning rate.
        lr = 0.05
        self._weights = [max(0.01, w + lr / len(self._weights)) for w in self._weights]
        total = sum(self._weights)
        self._weights = [w / total for w in self._weights]

        _log.debug(
            "execute.pricing.learned",
            sku=self.sku,
            pnl=round(pnl, 2),
            weights=self._weights,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _maybe_ab_test(self, price: float) -> float:
        if self._rng.random() < PRICING_AB_TEST_RATE:
            delta = self._rng.choice(list(PRICING_AB_TEST_DELTAS))
            test_price = price * delta
            _log.info(
                "execute.pricing.ab_test",
                sku=self.sku,
                control=round(price, 2),
                test=round(test_price, 2),
                delta=delta,
            )
            return test_price
        return price


# ---------------------------------------------------------------------------
# Pure price-signal functions (no side effects, easy to unit test)
# ---------------------------------------------------------------------------


def _cost_based_price(unit_cost: float) -> float:
    """Floor: minimum acceptable margin."""
    return unit_cost / (1 - PRICING_MIN_MARGIN_PCT + _EPSILON)


def _demand_based_price(unit_cost: float, demand_units_per_h: float) -> float:
    """Premium scales with demand velocity; cap at 2× COGS."""
    multiplier = min(2.0, 1.0 + demand_units_per_h / 10.0)
    return unit_cost * multiplier


def _inventory_based_price(unit_cost: float, inventory_level: int) -> float:
    """Low stock → premium; overstock → discount."""
    if inventory_level < 5:
        multiplier = 1.5
    elif inventory_level > 100:
        multiplier = 0.9
    else:
        multiplier = 1.0
    return unit_cost * multiplier


def _competitor_based_price(unit_cost: float, competitor_prices: list[float]) -> float:
    """Stay in 3rd cheapest position with a 5% buffer above cheapest."""
    if not competitor_prices:
        return unit_cost * 2.0
    sorted_prices = sorted(competitor_prices)
    third_cheapest = sorted_prices[min(2, len(sorted_prices) - 1)]
    return min(third_cheapest * 1.05, sorted_prices[0] * 1.10)


__all__: Final = ["PricingStrategy"]
