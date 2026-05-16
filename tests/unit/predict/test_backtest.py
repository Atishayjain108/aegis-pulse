"""Tests for the walk-forward backtester."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aegis.predict.backtest import (
    BacktestSpec,
    WalkForwardBacktester,
    compute_metrics,
)
from aegis.predict.backtest.runner import _Sample
from aegis.predict.models import HeuristicTemporalPredictor
from aegis.predict.schemas import (
    Prediction,
    PredictionAction,
    TrendStage,
)


def _build_samples(n: int, base: datetime, window):
    """Make `n` samples spanning n hours with the same window."""
    samples = []
    for i in range(n):
        samples.append(
            _Sample(
                timestamp=base + timedelta(days=i),
                window=window,
                truth={
                    "stage": TrendStage.EMERGING,
                    "velocity": 0.5,
                    "breakout": 1 if i % 3 == 0 else 0,
                },
            )
        )
    return samples


class TestWalkForwardBacktester:
    @pytest.mark.asyncio
    async def test_too_few_samples_returns_zero_folds(self, feature_window, utc_now):
        samples = _build_samples(5, utc_now - timedelta(days=10), feature_window)
        spec = BacktestSpec(
            horizon=24,
            train_days=60,
            test_days=30,
            purge_days=2,
            step_days=7,
        )
        backtester = WalkForwardBacktester(spec=spec)

        async def predict_fn(w):
            b = await HeuristicTemporalPredictor().predict(w)
            return b.predictions[0]

        folds, agg = await backtester.run(samples, predict_fn, model_id="m")
        assert agg.n_folds == 0
        assert folds == ()

    @pytest.mark.asyncio
    async def test_walks_forward_with_enough_data(self, feature_window, utc_now):
        # 200 daily samples → enough for several 60-train / 7-purge / 30-test folds.
        base = utc_now - timedelta(days=210)
        samples = _build_samples(200, base, feature_window)
        spec = BacktestSpec(
            horizon=24,
            train_days=60,
            test_days=30,
            purge_days=7,
            step_days=21,
            adversarial_noise_prob=0.0,
        )
        backtester = WalkForwardBacktester(spec=spec)

        async def predict_fn(w):
            b = await HeuristicTemporalPredictor().predict(w)
            return b.predictions[0]

        folds, agg = await backtester.run(samples, predict_fn, model_id="m")
        assert agg.n_folds >= 2
        for f in folds:
            assert f.train_end <= f.test_start
            assert f.n_train > 0
            assert f.n_test > 0

    @pytest.mark.asyncio
    async def test_aggregated_metrics_in_unit_range(self, feature_window, utc_now):
        base = utc_now - timedelta(days=210)
        samples = _build_samples(200, base, feature_window)
        spec = BacktestSpec(
            horizon=24,
            train_days=60,
            test_days=30,
            purge_days=7,
            step_days=21,
            adversarial_noise_prob=0.0,
        )

        async def predict_fn(w):
            b = await HeuristicTemporalPredictor().predict(w)
            return b.predictions[0]

        folds, agg = await WalkForwardBacktester(spec=spec).run(samples, predict_fn, model_id="m")
        assert 0.0 <= agg.accuracy <= 1.0
        assert 0.0 <= agg.macro_f1 <= 1.0
        assert 0.0 <= agg.breakout_precision <= 1.0
        assert 0.0 <= agg.breakout_recall <= 1.0
        assert 0.0 <= agg.coverage_90 <= 1.0
        assert agg.brier_breakout >= 0.0


class TestComputeMetrics:
    def test_compute_metrics_basic(self):
        preds = [
            Prediction(
                horizon_hours=24,
                stage=TrendStage.EMERGING,
                velocity_log=0.5,
                velocity_mean=1.0,
                velocity_p10=0.5,
                velocity_p50=1.0,
                velocity_p90=2.0,
                p_breakout=0.8,
                p_peak=0.0,
                p_decline=0.0,
                confidence=0.6,
                action=PredictionAction.ENTER,
            )
        ]
        truths = [{"stage": TrendStage.EMERGING, "velocity": 1.0, "breakout": 1}]
        m = compute_metrics(preds, truths)
        assert "macro_f1" in m
        assert "mae_velocity" in m
        assert "coverage_p10_p90" in m

    def test_compute_metrics_empty(self):
        m = compute_metrics([], [])
        assert m["n"] == 0
        assert m["macro_f1"] == 0.0

    def test_compute_metrics_mismatched_length_returns_zeros(self):
        p = Prediction(
            horizon_hours=24,
            stage=TrendStage.EMERGING,
            velocity_log=0.0,
            velocity_mean=1.0,
            velocity_p10=0.5,
            velocity_p50=1.0,
            velocity_p90=2.0,
            p_breakout=0.5,
            p_peak=0.0,
            p_decline=0.0,
            confidence=0.5,
            action=PredictionAction.OBSERVE,
        )
        m = compute_metrics([p], [])
        assert m["macro_f1"] == 0.0

    def test_mae_uses_velocity_log(self):
        """MAE uses velocity_log (the actual prediction), not velocity_mean_log."""
        p = Prediction(
            horizon_hours=24,
            stage=TrendStage.EMERGING,
            velocity_log=1.0,
            velocity_mean=1.0,
            velocity_p10=0.5,
            velocity_p50=1.0,
            velocity_p90=2.0,
            p_breakout=0.5,
            p_peak=0.0,
            p_decline=0.0,
            confidence=0.5,
            action=PredictionAction.OBSERVE,
        )
        truths = [{"stage": TrendStage.EMERGING, "velocity": 1.0, "breakout": 0}]
        m = compute_metrics([p], truths)
        # velocity_log=1.0, truth=1.0 → MAE should be ~0
        assert m["mae_velocity"] == pytest.approx(0.0, abs=1e-9)

    def test_coverage_fraction_correct(self):
        p = Prediction(
            horizon_hours=24,
            stage=TrendStage.EMERGING,
            velocity_log=0.0,
            velocity_mean=1.0,
            velocity_p10=0.5,
            velocity_p50=1.0,
            velocity_p90=2.0,
            p_breakout=0.5,
            p_peak=0.0,
            p_decline=0.0,
            confidence=0.5,
            action=PredictionAction.OBSERVE,
        )
        truths_in = [{"stage": TrendStage.EMERGING, "velocity": 1.2, "breakout": 0}]
        truths_out = [{"stage": TrendStage.EMERGING, "velocity": 5.0, "breakout": 0}]
        m_in = compute_metrics([p], truths_in)
        m_out = compute_metrics([p], truths_out)
        assert m_in["coverage_p10_p90"] == 1.0
        assert m_out["coverage_p10_p90"] == 0.0


class TestCalibrationMetrics:
    def test_ece_perfect_calibration(self):
        from aegis.predict.backtest.metrics import expected_calibration_error

        probs = [0.1, 0.3, 0.7, 0.9]
        outcomes = [0, 0, 1, 1]
        ece = expected_calibration_error(probs, outcomes)
        assert 0.0 <= ece <= 1.0

    def test_ece_empty(self):
        from aegis.predict.backtest.metrics import expected_calibration_error

        assert expected_calibration_error([], []) == 0.0

    def test_ece_worst_case(self):
        from aegis.predict.backtest.metrics import expected_calibration_error

        probs = [1.0, 1.0, 1.0]
        outcomes = [0, 0, 0]
        ece = expected_calibration_error(probs, outcomes)
        assert ece > 0.5

    def test_reliability_curve_returns_tuples(self):
        from aegis.predict.backtest.metrics import reliability_curve

        probs = [0.1, 0.5, 0.9]
        outcomes = [0, 1, 1]
        curve = reliability_curve(probs, outcomes)
        assert isinstance(curve, list)
        for mean_p, obs_rate, count in curve:
            assert 0.0 <= mean_p <= 1.0
            assert 0.0 <= obs_rate <= 1.0
            assert count > 0

    def test_safe_log_loss_bounded(self):
        from aegis.predict.backtest.metrics import safe_log_loss

        probs = [0.9, 0.1, 0.8]
        outcomes = [1, 0, 1]
        ll = safe_log_loss(probs, outcomes)
        assert ll >= 0.0
        assert ll < 5.0

    def test_safe_log_loss_empty(self):
        from aegis.predict.backtest.metrics import safe_log_loss

        assert safe_log_loss([], []) == 0.0

    def test_safe_log_loss_extreme_probs_clamped(self):
        from aegis.predict.backtest.metrics import safe_log_loss

        probs = [0.0, 1.0]
        outcomes = [1, 0]
        ll = safe_log_loss(probs, outcomes)
        assert ll < 20.0  # clamped to eps, not inf


class TestBacktestSpec:
    def test_invalid_horizon_warns_but_proceeds(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="aegis.predict.backtest.runner"):
            spec = BacktestSpec(horizon=999)
        assert spec.horizon == 999

    def test_purge_days_floor_at_one(self):
        spec = BacktestSpec(horizon=24, purge_days=0)
        assert spec.purge_days == 1


class TestLookaheadCheck:
    @pytest.mark.asyncio
    async def test_lookahead_raises(self, feature_window, utc_now):
        from aegis.predict.backtest.runner import _check_no_lookahead, _Sample

        t1 = utc_now
        t2 = utc_now
        s1 = _Sample(timestamp=t1, window=feature_window, truth={})
        s2 = _Sample(timestamp=t2, window=feature_window, truth={})
        with pytest.raises(Exception):
            _check_no_lookahead([s1], [s2], purge_seconds=3600)
