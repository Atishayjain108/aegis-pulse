"""Schema validation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from aegis.predict import FEATURE_DIM
from aegis.predict.schemas import (
    BacktestResult,
    FeatureWindow,
    ModelKind,
    ModelManifest,
    Prediction,
    PredictionAction,
    PredictionBundle,
    PredictionRecord,
    TrendStage,
    UncertaintyMethod,
)


# --------------------------------------------------------------------------
# FeatureWindow
# --------------------------------------------------------------------------
class TestFeatureWindow:
    def test_valid_window_constructs(self, utc_now):
        fw = FeatureWindow(
            tenant_id="t1",
            trend_id="x",
            captured_at=utc_now,
            window_size=168,
            feature_dim=FEATURE_DIM,
            values=[0.0] * (168 * FEATURE_DIM),
        )
        assert fw.window_size == 168
        assert fw.feature_dim == FEATURE_DIM
        assert len(fw.values) == 168 * FEATURE_DIM

    def test_wrong_length_rejected(self, utc_now):
        with pytest.raises(ValidationError):
            FeatureWindow(
                tenant_id="t1",
                trend_id="x",
                captured_at=utc_now,
                window_size=168,
                feature_dim=FEATURE_DIM,
                values=[0.0] * 10,
            )

    def test_nan_rejected(self, utc_now):
        with pytest.raises(ValidationError):
            FeatureWindow(
                tenant_id="t1",
                trend_id="x",
                captured_at=utc_now,
                window_size=2,
                feature_dim=FEATURE_DIM,
                values=[float("nan")] + [0.0] * (2 * FEATURE_DIM - 1),
            )

    def test_inf_rejected(self, utc_now):
        with pytest.raises(ValidationError):
            FeatureWindow(
                tenant_id="t1",
                trend_id="x",
                captured_at=utc_now,
                window_size=2,
                feature_dim=FEATURE_DIM,
                values=[float("inf")] + [0.0] * (2 * FEATURE_DIM - 1),
            )

    def test_feature_names_drift_rejected(self, utc_now):
        with pytest.raises(ValidationError):
            FeatureWindow(
                tenant_id="t1",
                trend_id="x",
                captured_at=utc_now,
                window_size=2,
                feature_dim=FEATURE_DIM,
                feature_names=("wrong",) * FEATURE_DIM,
                values=[0.0] * (2 * FEATURE_DIM),
            )

    def test_naive_datetime_coerced_to_utc(self):
        naive = datetime(2026, 1, 1, 12, 0, 0)
        fw = FeatureWindow(
            tenant_id="t1",
            trend_id="x",
            captured_at=naive,
            window_size=1,
            feature_dim=FEATURE_DIM,
            values=[0.0] * FEATURE_DIM,
        )
        assert fw.captured_at.tzinfo is not None

    def test_as_2d_round_trips(self, utc_now):
        flat = [float(i) for i in range(2 * FEATURE_DIM)]
        fw = FeatureWindow(
            tenant_id="t1",
            trend_id="x",
            captured_at=utc_now,
            window_size=2,
            feature_dim=FEATURE_DIM,
            values=flat,
        )
        m = fw.as_2d()
        assert len(m) == 2
        assert all(len(row) == FEATURE_DIM for row in m)
        assert m[0][0] == 0.0
        assert m[1][-1] == float(2 * FEATURE_DIM - 1)


# --------------------------------------------------------------------------
# Prediction
# --------------------------------------------------------------------------
class TestPrediction:
    def _kwargs(self, **overrides):
        base = {
            "horizon_hours": 24,
            "stage": TrendStage.EMERGING,
            "velocity_log": 0.5,
            "velocity_mean": 1.6,
            "velocity_p10": 1.0,
            "velocity_p50": 1.6,
            "velocity_p90": 2.0,
            "p_breakout": 0.3,
            "p_peak": 0.2,
            "p_decline": 0.1,
            "confidence": 0.65,
            "action": PredictionAction.OBSERVE,
        }
        base.update(overrides)
        return base

    def test_constructs(self):
        p = Prediction(**self._kwargs())
        assert p.horizon_hours == 24
        assert p.stage == TrendStage.EMERGING

    def test_percentiles_must_be_monotonic(self):
        with pytest.raises(ValidationError):
            Prediction(**self._kwargs(velocity_p10=2.0, velocity_p50=1.6, velocity_p90=2.0))

    def test_conformal_inversion_rejected(self):
        with pytest.raises(ValidationError):
            Prediction(**self._kwargs(conformal_lower=1.0, conformal_upper=-1.0))

    def test_class_probs_cap(self):
        with pytest.raises(ValidationError):
            Prediction(**self._kwargs(p_breakout=0.6, p_peak=0.6, p_decline=0.6))

    def test_optional_neural_fields_default(self):
        p = Prediction(**self._kwargs())
        assert p.velocity_mean_log == 0.0
        assert p.velocity_log_sigma == 0.0
        assert p.metadata == {}
        assert p.model_kind == ModelKind.HEURISTIC

    def test_frozen(self):
        p = Prediction(**self._kwargs())
        with pytest.raises(ValidationError):
            p.confidence = 0.9  # type: ignore[misc]


# --------------------------------------------------------------------------
# PredictionBundle
# --------------------------------------------------------------------------
class TestPredictionBundle:
    def _bundle(self, horizons=(1, 6, 24), now=None) -> PredictionBundle:
        if now is None:
            now = datetime.now(UTC)
        preds = []
        for h in horizons:
            preds.append(
                Prediction(
                    horizon_hours=h,
                    stage=TrendStage.DORMANT,
                    velocity_log=0.0,
                    velocity_mean=0.0,
                    velocity_p10=0.0,
                    velocity_p50=0.0,
                    velocity_p90=0.0,
                    p_breakout=0.1,
                    p_peak=0.1,
                    p_decline=0.1,
                    confidence=0.5,
                    action=PredictionAction.OBSERVE,
                )
            )
        return PredictionBundle(
            trend_id="t",
            correlation_id="cid",
            predictions=preds,
            model_id="mid",
            model_kind=ModelKind.HEURISTIC,
            model_version="1.0.0",
            uncertainty_method=UncertaintyMethod.NONE,
            started_at=now - timedelta(milliseconds=10),
            finished_at=now,
            duration_ms=10.0,
            feature_window_hash="hash",
        )

    def test_constructs(self):
        b = self._bundle()
        assert len(b.predictions) == 3

    def test_at_least_one_prediction(self):
        with pytest.raises(ValidationError):
            self._bundle(horizons=())

    def test_unique_horizons(self):
        with pytest.raises(ValidationError):
            self._bundle(horizons=(1, 1, 6))

    def test_by_horizon(self):
        b = self._bundle(horizons=(1, 6, 24))
        p = b.by_horizon(6)
        assert p is not None
        assert p.horizon_hours == 6
        assert b.by_horizon(99) is None


# --------------------------------------------------------------------------
# PredictionRecord
# --------------------------------------------------------------------------
class TestPredictionRecord:
    def test_constructs(self):
        bundle = TestPredictionBundle()._bundle()
        rec = PredictionRecord(
            bundle=bundle,
            created_at=datetime.now(UTC),
            signature="UNSIGNED",
        )
        assert rec.signature == "UNSIGNED"
        assert rec.prediction_id  # auto-uuid

    def test_empty_signature_rejected(self):
        bundle = TestPredictionBundle()._bundle()
        with pytest.raises(ValidationError):
            PredictionRecord(
                bundle=bundle,
                created_at=datetime.now(UTC),
                signature="",
            )


# --------------------------------------------------------------------------
# BacktestResult
# --------------------------------------------------------------------------
class TestBacktestResult:
    def _kwargs(self, **overrides):
        now = datetime.now(UTC)
        base = {
            "model_id": "m",
            "fold_index": 0,
            "train_start": now - timedelta(days=30),
            "train_end": now - timedelta(days=8),
            "test_start": now - timedelta(days=7),
            "test_end": now,
            "n_train": 100,
            "n_test": 20,
            "accuracy": 0.75,
            "macro_f1": 0.6,
            "breakout_precision": 0.7,
            "breakout_recall": 0.5,
            "mae_log_velocity": 0.3,
            "pinball_p10": 0.1,
            "pinball_p90": 0.2,
            "ece": 0.05,
            "coverage_90": 0.88,
            "sharpness": 1.5,
        }
        base.update(overrides)
        return base

    def test_constructs(self):
        r = BacktestResult(**self._kwargs())
        assert r.macro_f1 == 0.6

    def test_lookahead_rejected(self):
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            BacktestResult(
                **self._kwargs(
                    train_end=now,  # later
                    test_start=now - timedelta(days=1),  # earlier
                )
            )


# --------------------------------------------------------------------------
# ModelManifest
# --------------------------------------------------------------------------
class TestModelManifest:
    def test_constructs(self):
        now = datetime.now(UTC)
        m = ModelManifest(
            model_id="m1-1.0.0-abc",
            name="m1",
            kind=ModelKind.TEMPORAL,
            version="1.0.0",
            weights_uri="file:///tmp/w.bin",
            sha256="a" * 64,
            trained_at=now,
            train_window_start=now - timedelta(days=30),
            train_window_end=now,
            n_train_samples=1000,
        )
        assert m.stage == "staging"
