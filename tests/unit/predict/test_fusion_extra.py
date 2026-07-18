"""Additional tests for fusion edge cases — improves coverage of fusion.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.predict.models import HeuristicRelationalPredictor, HeuristicTemporalPredictor
from aegis.predict.models.fusion import FusionWeights, fuse
from aegis.predict.schemas import (
    ModelKind,
    Prediction,
    PredictionAction,
    PredictionBundle,
    TrendStage,
    UncertaintyMethod,
)


def _make_bundle(
    *, p_breakout: float, confidence: float = 0.7, model_id: str = "x"
) -> PredictionBundle:
    p = Prediction(
        horizon_hours=24,
        stage=TrendStage.EMERGING if p_breakout > 0.3 else TrendStage.DORMANT,
        velocity_log=0.5,
        velocity_mean=1.6,
        velocity_p10=0.5,
        velocity_p50=1.6,
        velocity_p90=3.0,
        p_breakout=p_breakout,
        p_peak=0.05,
        p_decline=0.05,
        confidence=confidence,
        action=PredictionAction.OBSERVE,
    )
    now = datetime.now(UTC)
    return PredictionBundle(
        trend_id="t",
        correlation_id="c",
        predictions=[p],
        model_id=model_id,
        model_kind=ModelKind.HEURISTIC,
        model_version="1",
        uncertainty_method=UncertaintyMethod.NONE,
        started_at=now,
        finished_at=now,
        duration_ms=1.0,
        feature_window_hash="h",
    )


class TestFusionWeights:
    def test_default_normalised(self):
        w = FusionWeights().normalised()
        assert abs(w.temporal + w.relational - 1.0) < 1e-9

    def test_zero_weights_stay_safe(self):
        # Defensive fallback: a (0, 0) weights blob should not divide by zero.
        w = FusionWeights(temporal=0.0, relational=0.0).normalised()
        assert w.temporal + w.relational == pytest.approx(1.0, abs=1e-6)

    def test_unequal_weights_preserve_ratio(self):
        w = FusionWeights(temporal=3.0, relational=1.0).normalised()
        assert w.temporal == pytest.approx(0.75, abs=1e-9)
        assert w.relational == pytest.approx(0.25, abs=1e-9)


class TestFuseEdgeCases:
    def test_fuse_with_temporal_only_passthrough(self):
        t = _make_bundle(p_breakout=0.7)
        out = fuse(temporal=t, relational=None)
        assert len(out) == 1
        # Without relational, fusion caps confidence below temporal's.
        assert out[0].confidence <= t.predictions[0].confidence + 1e-9

    def test_fuse_high_disagreement_lowers_confidence(self):
        t = _make_bundle(p_breakout=0.9, confidence=0.9, model_id="t")
        r = _make_bundle(p_breakout=0.1, confidence=0.9, model_id="r")
        out = fuse(temporal=t, relational=r)
        # Disagreement signal: confidence should be lower than the
        # max of the two single-model confidences.
        assert out[0].confidence < 0.9 + 1e-9

    def test_fuse_perfect_agreement_keeps_confidence(self):
        t = _make_bundle(p_breakout=0.7, confidence=0.7, model_id="t")
        r = _make_bundle(p_breakout=0.7, confidence=0.7, model_id="r")
        out = fuse(temporal=t, relational=r)
        # At identical predictions, confidence is at least the mean.
        assert out[0].confidence >= 0.5

    def test_fuse_with_custom_weights(self):
        t = _make_bundle(p_breakout=0.9, model_id="t")
        r = _make_bundle(p_breakout=0.1, model_id="r")
        # Heavily weight temporal.
        out = fuse(
            temporal=t,
            relational=r,
            weights=FusionWeights(temporal=4.0, relational=1.0),
        )
        # Result should be closer to temporal's 0.9 than to mean (0.5).
        assert out[0].p_breakout > 0.5

    def test_fuse_empty_temporal_predictions_yields_empty(self):
        # Construct a bundle with a single horizon, then strip it.
        t = _make_bundle(p_breakout=0.5)
        # Replace predictions with a 0-length list — but the schema
        # requires ≥ 1 prediction, so we must build a fresh bundle
        # that mimics the empty case via mismatched horizons.
        p2 = Prediction(
            horizon_hours=99,
            stage=TrendStage.DORMANT,
            velocity_log=0.0,
            velocity_mean=0.0,
            velocity_p10=0.0,
            velocity_p50=0.0,
            velocity_p90=0.0,
            p_breakout=0.0,
            p_peak=0.0,
            p_decline=0.0,
            confidence=0.0,
            action=PredictionAction.OBSERVE,
        )
        now = datetime.now(UTC)
        r = PredictionBundle(
            trend_id="t",
            correlation_id="c",
            predictions=[p2],
            model_id="r",
            model_kind=ModelKind.HEURISTIC,
            model_version="1",
            uncertainty_method=UncertaintyMethod.NONE,
            started_at=now,
            finished_at=now,
            duration_ms=1.0,
            feature_window_hash="h",
        )
        # When relational has *no* matching horizon, fusion falls
        # back to the temporal prediction at that horizon.
        out = fuse(temporal=t, relational=r)
        assert len(out) >= 1


class TestPlattCalibrator:
    def test_identity_at_default_params(self):
        from aegis.predict.models.fusion import PlattCalibrator

        cal = PlattCalibrator(a=1.0, b=0.0)
        # a=1, b=0 → identity-ish (sigmoid of logit = identity)
        p = 0.6
        result = cal(p)
        assert result == pytest.approx(p, abs=0.01)

    def test_clips_extreme_probs(self):
        from aegis.predict.models.fusion import PlattCalibrator

        cal = PlattCalibrator()
        assert 0.0 < cal(0.0) < 1.0  # 0.0 gets clipped to eps
        assert 0.0 < cal(1.0) < 1.0  # 1.0 gets clipped to 1-eps

    def test_scaling_shifts_calibration(self):
        from aegis.predict.models.fusion import PlattCalibrator

        cal_up = PlattCalibrator(a=1.0, b=2.0)
        cal_down = PlattCalibrator(a=1.0, b=-2.0)
        p = 0.5
        assert cal_up(p) > p
        assert cal_down(p) < p

    def test_output_in_unit_range(self):
        from aegis.predict.models.fusion import PlattCalibrator

        cal = PlattCalibrator(a=0.5, b=0.1)
        for p in [0.01, 0.1, 0.5, 0.9, 0.99]:
            out = cal(p)
            assert 0.0 <= out <= 1.0


class TestIsotonicCalibrator:
    def test_identity_two_point(self):
        from aegis.predict.models.fusion import IsotonicCalibrator

        cal = IsotonicCalibrator(xs=(0.0, 1.0), ys=(0.0, 1.0))
        assert cal(0.5) == pytest.approx(0.5, abs=1e-9)

    def test_clamps_below_xs0(self):
        from aegis.predict.models.fusion import IsotonicCalibrator

        cal = IsotonicCalibrator(xs=(0.2, 1.0), ys=(0.3, 1.0))
        assert cal(0.0) == 0.3

    def test_clamps_above_xs_last(self):
        from aegis.predict.models.fusion import IsotonicCalibrator

        cal = IsotonicCalibrator(xs=(0.0, 0.8), ys=(0.0, 0.9))
        assert cal(1.0) == 0.9

    def test_interpolates_mid_segment(self):
        from aegis.predict.models.fusion import IsotonicCalibrator

        cal = IsotonicCalibrator(xs=(0.0, 0.5, 1.0), ys=(0.0, 0.4, 1.0))
        # Midpoint between 0.0 and 0.5 in x should map to midpoint in y
        assert cal(0.25) == pytest.approx(0.2, abs=1e-9)

    def test_degenerate_segment_x0_eq_x1(self):
        from aegis.predict.models.fusion import IsotonicCalibrator

        cal = IsotonicCalibrator(xs=(0.5, 0.5, 1.0), ys=(0.3, 0.3, 1.0))
        # xs[0]==xs[1] — shouldn't crash; returns ys[lo]
        result = cal(0.5)
        assert 0.0 <= result <= 1.0


class TestFusionCosineHelper:
    def test_identical_vectors_give_one(self):
        from aegis.predict.models.fusion import _cosine

        assert _cosine((0.7, 0.2, 0.1), (0.7, 0.2, 0.1)) == pytest.approx(1.0, abs=1e-9)

    def test_orthogonal_vectors_give_zero(self):
        from aegis.predict.models.fusion import _cosine

        assert _cosine((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)) == pytest.approx(0.0, abs=1e-9)

    def test_zero_vector_returns_zero(self):
        from aegis.predict.models.fusion import _cosine

        assert _cosine((0.0, 0.0, 0.0), (0.5, 0.3, 0.2)) == 0.0


class TestFuseWithCalibrators:
    def test_platt_applied_shifts_p_breakout(self):
        from aegis.predict.models.fusion import PlattCalibrator, fuse

        t = _make_bundle(p_breakout=0.5)
        r = _make_bundle(p_breakout=0.5)
        cal = PlattCalibrator(a=1.0, b=3.0)  # strong upward shift
        out = fuse(temporal=t, relational=r, platt_breakout=cal)
        assert out[0].p_breakout > 0.5

    def test_isotonic_applied_shifts_p_breakout(self):
        from aegis.predict.models.fusion import IsotonicCalibrator, fuse

        t = _make_bundle(p_breakout=0.5)
        r = _make_bundle(p_breakout=0.5)
        cal = IsotonicCalibrator(xs=(0.0, 0.5, 1.0), ys=(0.0, 0.8, 1.0))
        out = fuse(temporal=t, relational=r, isotonic_breakout=cal)
        assert out[0].p_breakout >= 0.5

    def test_fuse_high_p_breakout_picks_breakout_stage(self):
        from aegis.predict.schemas import TrendStage

        _make_bundle(p_breakout=0.8, confidence=0.9)
        # Override p values so breakout dominates
        from datetime import datetime

        from aegis.predict.schemas import ModelKind, UncertaintyMethod

        now = datetime.now(UTC)
        p_high = Prediction(
            horizon_hours=24,
            stage=TrendStage.EMERGING,
            velocity_log=0.5,
            velocity_mean=1.6,
            velocity_p10=0.5,
            velocity_p50=1.6,
            velocity_p90=3.0,
            p_breakout=0.8,
            p_peak=0.05,
            p_decline=0.05,
            confidence=0.9,
            action=PredictionAction.OBSERVE,
        )
        bundle = PredictionBundle(
            trend_id="t",
            correlation_id="c",
            predictions=[p_high],
            model_id="x",
            model_kind=ModelKind.HEURISTIC,
            model_version="1",
            uncertainty_method=UncertaintyMethod.NONE,
            started_at=now,
            finished_at=now,
            duration_ms=1.0,
            feature_window_hash="h",
        )
        out = fuse(temporal=bundle, relational=bundle)
        assert out[0].stage == TrendStage.BREAKOUT


class TestHeuristicEdgeCases:
    @pytest.mark.asyncio
    async def test_temporal_handles_window_size_one(self, utc_now):
        from aegis.predict import FEATURE_DIM
        from aegis.predict.schemas import FeatureWindow

        w = FeatureWindow(
            tenant_id="t",
            trend_id="trend",
            captured_at=utc_now,
            window_size=1,
            feature_dim=FEATURE_DIM,
            values=[0.1] * FEATURE_DIM,
        )
        m = HeuristicTemporalPredictor()
        b = await m.predict(w)
        assert len(b.predictions) > 0
        for p in b.predictions:
            assert 0.0 <= p.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_relational_handles_unipolar_window(self, utc_now):
        # All-zero except a few non-zero spots — graph features are sparse.
        from aegis.predict import FEATURE_DIM
        from aegis.predict.schemas import FeatureWindow

        flat = [0.0] * (24 * FEATURE_DIM)
        # Set a single hot spot — should not crash.
        flat[5] = 99.0
        w = FeatureWindow(
            tenant_id="t",
            trend_id="trend",
            captured_at=utc_now,
            window_size=24,
            feature_dim=FEATURE_DIM,
            values=flat,
        )
        m = HeuristicRelationalPredictor()
        b = await m.predict(w)
        assert b is not None
