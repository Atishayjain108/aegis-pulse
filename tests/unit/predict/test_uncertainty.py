"""Tests for the uncertainty layer: deep ensembles + conformal."""

from __future__ import annotations

import pytest

from aegis.predict.models import HeuristicTemporalPredictor
from aegis.predict.models.uncertainty import (
    ConformalCalibrator,
    DeepEnsemblePredictor,
    fit_conformal,
)
from aegis.predict.schemas import UncertaintyMethod


class TestDeepEnsemble:
    def test_requires_at_least_two_members(self):
        with pytest.raises(ValueError):
            DeepEnsemblePredictor(members=[HeuristicTemporalPredictor()])

    @pytest.mark.asyncio
    async def test_two_member_ensemble_runs(self, feature_window):
        ens = DeepEnsemblePredictor(
            members=[HeuristicTemporalPredictor(), HeuristicTemporalPredictor()]
        )
        b = await ens.predict(feature_window)
        assert len(b.predictions) > 0
        for p in b.predictions:
            assert p.velocity_p10 <= p.velocity_p50 <= p.velocity_p90

    @pytest.mark.asyncio
    async def test_uncertainty_method_when_no_conformal(self, feature_window):
        ens = DeepEnsemblePredictor(
            members=[HeuristicTemporalPredictor(), HeuristicTemporalPredictor()]
        )
        b = await ens.predict(feature_window)
        assert b.uncertainty_method == UncertaintyMethod.DEEP_ENSEMBLE

    @pytest.mark.asyncio
    async def test_uncertainty_method_with_conformal(self, feature_window):
        cal = ConformalCalibrator(quantiles={1: 1.2, 6: 1.4, 24: 1.6, 72: 1.8})
        ens = DeepEnsemblePredictor(
            members=[HeuristicTemporalPredictor(), HeuristicTemporalPredictor()],
            conformal=cal,
        )
        b = await ens.predict(feature_window)
        assert b.uncertainty_method == UncertaintyMethod.CONFORMAL


class TestConformalCalibrator:
    def test_adjust_returns_ordered_interval(self):
        cal = ConformalCalibrator(quantiles={24: 1.5})
        lo, hi = cal.adjust(horizon_h=24, mu=0.0, sigma=1.0)
        assert lo < hi

    def test_unknown_horizon_uses_default(self):
        cal = ConformalCalibrator(quantiles={24: 1.5})
        lo, hi = cal.adjust(horizon_h=99, mu=0.0, sigma=1.0)
        assert lo < hi

    def test_higher_sigma_widens_interval(self):
        cal = ConformalCalibrator(quantiles={24: 1.5})
        narrow = cal.adjust(horizon_h=24, mu=0.0, sigma=0.1)
        wide = cal.adjust(horizon_h=24, mu=0.0, sigma=1.0)
        assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


class TestFitConformal:
    def test_fit_with_residuals(self):
        residuals = {
            24: [
                (0.0, 1.0, 0.5),
                (0.0, 1.0, -0.5),
                (0.0, 1.0, 0.2),
                (0.0, 1.0, -0.1),
                (0.0, 1.0, 0.8),
            ],
        }
        cal = fit_conformal(residuals_by_horizon=residuals, alpha=0.10)
        assert 24 in cal.quantiles
        assert cal.quantiles[24] > 0

    def test_fit_with_empty_falls_back_to_default(self):
        cal = fit_conformal(residuals_by_horizon={1: []}, alpha=0.10)
        assert cal.quantiles[1] == pytest.approx(1.2816, abs=1e-4)
