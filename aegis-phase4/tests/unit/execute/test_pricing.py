"""Unit tests for the Phase 6 dynamic pricing engine."""

from __future__ import annotations

import pytest

from aegis.execute.pricing import (
    PricingStrategy,
    _competitor_based_price,
    _cost_based_price,
    _demand_based_price,
    _inventory_based_price,
)

# ---------------------------------------------------------------------------
# PricingStrategy.compute_price
# ---------------------------------------------------------------------------


def test_price_above_floor():
    """Returned price must always be above the minimum-margin floor."""
    s = PricingStrategy("SKU-001", seed=42)
    price = s.compute_price(10.0, demand_units_per_h=1.0, inventory_level=20)
    min_floor = 10.0 / (1 - 0.15)
    assert price >= min_floor - 1e-6


def test_price_below_ceiling():
    """Returned price must be at most 3× COGS."""
    s = PricingStrategy("SKU-002", seed=0)
    price = s.compute_price(10.0, demand_units_per_h=100.0, inventory_level=200)
    assert price <= 30.0 + 1e-6


def test_price_raises_on_zero_cost():
    s = PricingStrategy("SKU-003")
    with pytest.raises(ValueError):
        s.compute_price(0.0, demand_units_per_h=1.0, inventory_level=10)


def test_price_raises_on_negative_cost():
    s = PricingStrategy("SKU-004")
    with pytest.raises(ValueError):
        s.compute_price(-5.0, demand_units_per_h=1.0, inventory_level=10)


def test_price_high_demand_higher_than_low_demand():
    s_high = PricingStrategy("SKU-H", seed=0)
    s_low = PricingStrategy("SKU-L", seed=0)
    cost = 10.0
    high = s_high.compute_price(cost, demand_units_per_h=50.0, inventory_level=20)
    low = s_low.compute_price(cost, demand_units_per_h=0.5, inventory_level=20)
    assert high >= low


def test_price_low_inventory_premium():
    s = PricingStrategy("SKU-INV", seed=0)
    cost = 10.0
    low_stock = s.compute_price(cost, demand_units_per_h=1.0, inventory_level=2)
    high_stock = s.compute_price(cost, demand_units_per_h=1.0, inventory_level=200)
    assert low_stock >= high_stock


def test_competitor_anchor_applied():
    """Competitor prices should anchor the weighted result toward market."""
    s = PricingStrategy("SKU-CMP", seed=0)
    competitor_prices = [15.0, 18.0, 20.0]
    price = s.compute_price(
        10.0, demand_units_per_h=1.0, inventory_level=20,
        competitor_prices=competitor_prices,
    )
    # Should land somewhere between floor and 3× COGS.
    assert 10.0 / 0.85 <= price <= 30.0 + 1e-6


def test_update_from_outcome_normalises_weights():
    s = PricingStrategy("SKU-RL", seed=0)
    s.update_from_outcome(price=15.0, actual_demand=5.0, pnl=3.0)
    total = sum(s._weights)
    assert total == pytest.approx(1.0, abs=1e-9)


def test_update_from_outcome_ignores_negative_pnl():
    s = PricingStrategy("SKU-NEG", seed=0)
    weights_before = list(s._weights)
    s.update_from_outcome(price=15.0, actual_demand=2.0, pnl=-5.0)
    assert s._weights == weights_before


# ---------------------------------------------------------------------------
# Pure signal functions
# ---------------------------------------------------------------------------


def test_cost_based_price_above_cost():
    p = _cost_based_price(10.0)
    assert p > 10.0


def test_demand_based_price_scales_with_demand():
    low = _demand_based_price(10.0, 0.1)
    high = _demand_based_price(10.0, 20.0)
    assert high > low


def test_demand_based_price_cap_at_2x():
    # Very high demand should cap at 2× COGS.
    price = _demand_based_price(10.0, 1000.0)
    assert price == pytest.approx(20.0)


def test_inventory_based_price_low_stock_premium():
    low = _inventory_based_price(10.0, 2)
    normal = _inventory_based_price(10.0, 50)
    assert low > normal


def test_inventory_based_price_overstock_discount():
    overstock = _inventory_based_price(10.0, 200)
    normal = _inventory_based_price(10.0, 50)
    assert overstock < normal


def test_competitor_based_price_no_competitors_defaults_2x():
    p = _competitor_based_price(10.0, [])
    assert p == pytest.approx(20.0)


def test_competitor_based_price_anchors_near_third_cheapest():
    prices = [12.0, 14.0, 16.0]
    p = _competitor_based_price(10.0, prices)
    # Should be ≤ cheapest * 1.10
    assert p <= 12.0 * 1.10 + 1e-6


# ---------------------------------------------------------------------------
# PASS3-3D — Phase 9 RL policy weight loading
# ---------------------------------------------------------------------------


class TestPolicyWeightLoading:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        """Class-wide cache must never leak into (or out of) these tests."""
        PricingStrategy._reset_policy_cache()
        yield
        PricingStrategy._reset_policy_cache()

    @pytest.mark.asyncio
    async def test_fallback_to_defaults_when_no_policy(self) -> None:
        # No db_pool → OnlinePricingPolicy.load() returns False → defaults.
        weights = await PricingStrategy._get_policy_weights(db_pool=None)
        assert weights == list(PricingStrategy._DEFAULT_WEIGHTS)
        # Failure is not cached — next call retries.
        assert PricingStrategy._policy_weights is None

    @pytest.mark.asyncio
    async def test_loaded_weights_cached_for_ttl(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import patch as _patch

        import numpy as np

        fake_weights = np.array([0.4, 0.3, 0.2, 0.1])
        with (
            _patch(
                "aegis.evolve.rl_policy.OnlinePricingPolicy.load",
                new_callable=_AsyncMock,
                return_value=True,
            ) as load,
            _patch(
                "aegis.evolve.rl_policy.OnlinePricingPolicy.get_weights",
                return_value=fake_weights,
            ),
        ):
            first = await PricingStrategy._get_policy_weights()
            second = await PricingStrategy._get_policy_weights()

        assert first == pytest.approx([0.4, 0.3, 0.2, 0.1])
        assert second == first
        load.assert_awaited_once()  # second call served from class cache

    @pytest.mark.asyncio
    async def test_new_instance_seeds_from_cache(self) -> None:
        from datetime import UTC, datetime

        PricingStrategy._policy_weights = [0.4, 0.3, 0.2, 0.1]
        PricingStrategy._policy_loaded_at = datetime.now(UTC)

        strat = PricingStrategy("SKU-CACHED", seed=1)
        assert strat._weights == pytest.approx([0.4, 0.3, 0.2, 0.1])

    def test_stale_cache_ignored(self) -> None:
        from datetime import UTC, datetime, timedelta

        PricingStrategy._policy_weights = [0.4, 0.3, 0.2, 0.1]
        PricingStrategy._policy_loaded_at = datetime.now(UTC) - timedelta(hours=7)

        assert PricingStrategy._cached_policy_weights() is None
        strat = PricingStrategy("SKU-STALE", seed=1)
        assert strat._weights == list(PricingStrategy._DEFAULT_WEIGHTS)

    @pytest.mark.asyncio
    async def test_load_exception_falls_back_to_defaults(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import patch as _patch

        with _patch(
            "aegis.evolve.rl_policy.OnlinePricingPolicy.load",
            new_callable=_AsyncMock,
            side_effect=RuntimeError("policy table missing"),
        ):
            weights = await PricingStrategy._get_policy_weights()
        assert weights == list(PricingStrategy._DEFAULT_WEIGHTS)
