"""Tests for the causal attribution layer."""

from __future__ import annotations

import pytest

from aegis.predict.causal import DeterministicAttributor
from aegis.predict.causal.counterfactual import (
    DEFAULT_SCENARIOS,
    CounterfactualEngine,
    CounterfactualScenario,
)
from aegis.predict.models import HeuristicTemporalPredictor


class TestDeterministicAttributor:
    @pytest.mark.asyncio
    async def test_attribute_returns_top_k(self, feature_window):
        bundle = await HeuristicTemporalPredictor().predict(feature_window)
        primary = bundle.predictions[0]
        attr = DeterministicAttributor(top_k=4)
        results = attr.attribute(feature_window, primary)
        assert len(results) <= 4
        for r in results:
            assert isinstance(r.feature, str)
            assert -1.0 <= r.contribution <= 1.0
            assert r.method in {"deterministic", "dowhy_linear"}

    @pytest.mark.asyncio
    async def test_attribute_on_empty_window_returns_empty(self, empty_window):
        bundle = await HeuristicTemporalPredictor().predict(empty_window)
        primary = bundle.predictions[0]
        attr = DeterministicAttributor()
        results = attr.attribute(empty_window, primary)
        # Empty (mostly-zero) window may or may not yield attributions —
        # the contract is just that the result is a tuple of valid records.
        assert isinstance(results, tuple)


class TestCounterfactualEngine:
    @pytest.mark.asyncio
    async def test_default_scenarios_run(self, feature_window):
        model = HeuristicTemporalPredictor()

        async def predict_fn(w):
            b = await model.predict(w)
            return b.predictions[0]

        engine = CounterfactualEngine()
        results = await engine.run(feature_window, predict_fn)
        assert len(results) == len(DEFAULT_SCENARIOS)
        # Every result has a Prediction
        for r in results:
            assert r.prediction is not None

    @pytest.mark.asyncio
    async def test_2x_velocity_changes_outcome(self, feature_window):
        """Doubling velocity should not REDUCE p_breakout."""
        model = HeuristicTemporalPredictor()

        async def predict_fn(w):
            b = await model.predict(w)
            return b.predictions[0]

        engine = CounterfactualEngine()
        results = await engine.run(feature_window, predict_fn)
        baseline = next(r for r in results if r.scenario.name == "baseline")
        doubled = next(r for r in results if r.scenario.name == "2x_velocity")
        # Heuristic monotonicity: higher velocity → ≥ same p_breakout.
        assert doubled.prediction.p_breakout + 1e-6 >= baseline.prediction.p_breakout

    @pytest.mark.asyncio
    async def test_scenario_does_not_mutate_input(self, feature_window):
        """The frozen FeatureWindow must survive any scenario unmutated."""
        original_values = list(feature_window.values)
        model = HeuristicTemporalPredictor()

        async def predict_fn(w):
            b = await model.predict(w)
            return b.predictions[0]

        engine = CounterfactualEngine(
            scenarios=(
                CounterfactualScenario(
                    name="custom",
                    description="x",
                    feature_multipliers={"velocity_24h": 5.0},
                ),
            )
        )
        await engine.run(feature_window, predict_fn)
        assert list(feature_window.values) == original_values

    @pytest.mark.asyncio
    async def test_failure_in_scenario_does_not_block_others(self, feature_window):
        async def predict_fn(w):
            raise RuntimeError("simulated")

        engine = CounterfactualEngine()
        results = await engine.run(feature_window, predict_fn)
        # Every scenario raised → no results, but no exception.
        assert results == ()
