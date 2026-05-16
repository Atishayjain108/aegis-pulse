"""Tests for the heuristic + fusion predictors."""

from __future__ import annotations

import pytest

from aegis.predict import DEFAULT_HORIZONS
from aegis.predict.models import (
    HeuristicRelationalPredictor,
    HeuristicTemporalPredictor,
    load_model,
)
from aegis.predict.models.fusion import FusionWeights, fuse
from aegis.predict.schemas import (
    PredictionAction,
    TrendStage,
)


class TestHeuristicTemporal:
    @pytest.mark.asyncio
    async def test_predict_returns_bundle(self, feature_window):
        m = HeuristicTemporalPredictor()
        b = await m.predict(feature_window)
        assert b.is_heuristic_only is True
        # Default predictor produces all configured horizons.
        horizons = sorted(p.horizon_hours for p in b.predictions)
        assert tuple(horizons) == tuple(sorted(DEFAULT_HORIZONS))

    @pytest.mark.asyncio
    async def test_predict_on_empty_window(self, empty_window):
        m = HeuristicTemporalPredictor()
        b = await m.predict(empty_window)
        # Empty window → DORMANT or low-velocity stage.
        for p in b.predictions:
            assert p.p_breakout < 0.5
            # Velocity p10 ≤ p50 ≤ p90 invariant always holds.
            assert p.velocity_p10 <= p.velocity_p50 <= p.velocity_p90

    @pytest.mark.asyncio
    async def test_action_is_valid_enum(self, feature_window):
        m = HeuristicTemporalPredictor()
        b = await m.predict(feature_window)
        for p in b.predictions:
            assert p.action in PredictionAction
            assert p.stage in TrendStage


class TestHeuristicRelational:
    @pytest.mark.asyncio
    async def test_predict_returns_bundle(self, feature_window):
        m = HeuristicRelationalPredictor()
        b = await m.predict(feature_window)
        assert b.is_heuristic_only is True
        assert len(b.predictions) > 0

    @pytest.mark.asyncio
    async def test_relational_does_not_throw_on_zero_graph(self, empty_window):
        m = HeuristicRelationalPredictor()
        b = await m.predict(empty_window)
        # Graph metrics on empty input collapse to neutral values; no exception.
        assert all(0.0 <= p.confidence <= 1.0 for p in b.predictions)


class TestFusion:
    @pytest.mark.asyncio
    async def test_fuse_returns_predictions(self, feature_window):
        t_bundle = await HeuristicTemporalPredictor().predict(feature_window)
        r_bundle = await HeuristicRelationalPredictor().predict(feature_window)
        fused = fuse(temporal=t_bundle, relational=r_bundle)
        assert len(fused) == len(t_bundle.predictions)
        for p in fused:
            assert 0.0 <= p.p_breakout <= 1.0
            assert p.p_breakout + p.p_peak + p.p_decline <= 1.0001

    @pytest.mark.asyncio
    async def test_fuse_without_relational_caps_confidence(self, feature_window):
        t_bundle = await HeuristicTemporalPredictor().predict(feature_window)
        fused = fuse(temporal=t_bundle, relational=None)
        # Without relational data we cap confidence at the heuristic ceiling.
        for f, t in zip(fused, t_bundle.predictions, strict=False):
            assert f.confidence <= t.confidence + 1e-9

    @pytest.mark.asyncio
    async def test_fusion_weights_normalised(self):
        w = FusionWeights(temporal=2.0, relational=1.0).normalised()
        assert abs(w.temporal + w.relational - 1.0) < 1e-9
        assert w.temporal > w.relational


class TestFactory:
    def test_load_heuristic(self):
        m = load_model("heuristic_temporal")
        assert m is not None
        assert m.kind.value == "heuristic"

    def test_load_unknown_raises(self):
        with pytest.raises(Exception):
            load_model("does_not_exist_v999")

    def test_load_optional_neural_falls_back(self):
        # patchtst falls back to heuristic if torch is missing.
        m = load_model("patchtst")
        assert m is not None


class TestPredictorBaseClass:
    @pytest.mark.asyncio
    async def test_fallback_to_heuristic_on_inner_exception(self, feature_window):
        """Predictor.predict() must not raise — it falls back to heuristic."""
        from aegis.predict.models.base import Predictor
        from aegis.predict.schemas import ModelKind

        class BrokenPredictor(Predictor):
            @property
            def model_id(self) -> str:
                return "broken-3.0.0"

            @property
            def kind(self) -> ModelKind:
                return ModelKind.TEMPORAL

            async def _predict_inner(self, *, window, graph, horizons, seed):
                raise RuntimeError("simulated GPU crash")

        m = BrokenPredictor()
        bundle = await m.predict(feature_window)
        # Must still produce a valid bundle (heuristic fallback).
        assert bundle is not None
        assert len(bundle.predictions) > 0
        # Fallback model_id should be the sentinel string.
        assert "heuristic-fallback" in bundle.model_id

    @pytest.mark.asyncio
    async def test_latest_bucket_returns_named_features(self, feature_window):
        from aegis.predict.models.base import Predictor
        from aegis.predict.schemas import ModelKind

        class NullPredictor(Predictor):
            @property
            def model_id(self) -> str:
                return "null-3.0.0"

            @property
            def kind(self) -> ModelKind:
                return ModelKind.HEURISTIC

            async def _predict_inner(self, *, window, graph, horizons, seed):
                return []

        bucket = NullPredictor.latest_bucket(feature_window)
        assert "signal_count" in bucket
        assert "velocity_1h" in bucket
        assert len(bucket) == feature_window.feature_dim

    @pytest.mark.asyncio
    async def test_predict_with_custom_horizons(self, feature_window):
        m = HeuristicTemporalPredictor()
        b = await m.predict(feature_window, horizons=(1, 6))
        horizons = [p.horizon_hours for p in b.predictions]
        assert 1 in horizons
        assert 6 in horizons
        assert 24 not in horizons
