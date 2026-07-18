"""
Phase B calibration core tests.

The two non-negotiable guards:
  * a CONSTANT-confidence stream must return insufficient_variance (the guard
    that would have auto-caught Phase A's 0.547 base-rate illusion),
  * a constant predictor's Brier skill must be exactly 0 (no fake skill).
"""

from __future__ import annotations

import random

from aegis.trust.calibration import (
    brier_score,
    brier_skill_score,
    calibration_report,
    ece,
    isotonic_apply,
    isotonic_fit,
    mce,
    reliability_bins,
)


def _calibrated_stream(n: int, seed: int = 0) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    ps, ys = [], []
    for _ in range(n):
        p = rng.random()
        ps.append(p)
        ys.append(1.0 if rng.random() < p else 0.0)
    return ps, ys


class TestDegenerateGuards:
    def test_constant_confidence_is_insufficient_variance(self) -> None:
        # Exactly Phase A's data shape: 247 rows, constant 0.5, 135 correct.
        ps = [0.5] * 247
        ys = [1.0 if i < 135 else 0.0 for i in range(247)]
        rep = calibration_report(ps, ys)
        assert rep.status == "insufficient_variance"
        assert rep.ece is None  # never fabricates a number
        assert rep.base_rate is not None  # base rate is still reported

    def test_too_few_outcomes_is_insufficient_data(self) -> None:
        rep = calibration_report([0.1, 0.9, 0.5], [0, 1, 0])
        assert rep.status == "insufficient_data"

    def test_base_rate_predictor_has_zero_brier_skill(self) -> None:
        # Predicting exactly the base rate is, by definition, zero skill.
        ys = [1.0 if i < 108 else 0.0 for i in range(200)]
        base_rate = sum(ys) / len(ys)
        ps = [base_rate] * 200
        assert abs(brier_skill_score(ps, ys)) < 1e-9


class TestCalibrationDetection:
    def test_well_calibrated_stream_low_ece_positive_skill(self) -> None:
        ps, ys = _calibrated_stream(4000)
        rep = calibration_report(ps, ys)
        assert rep.status == "ok"
        assert rep.ece < 0.05
        assert rep.brier_skill_score > 0.1

    def test_overconfident_stream_high_ece(self) -> None:
        rng = random.Random(1)
        # spread confidences 0.8-1.0 but only ~50% realize -> overconfident
        ps = [round(rng.uniform(0.8, 1.0), 3) for _ in range(500)]
        ys = [1.0 if rng.random() < 0.5 else 0.0 for _ in range(500)]
        rep = calibration_report(ps, ys)
        assert rep.status == "ok"
        assert rep.ece > 0.25
        assert rep.brier_skill_score < 0  # worse than base rate

    def test_isotonic_is_monotone_and_reduces_ece(self) -> None:
        rng = random.Random(2)
        ps = [round(rng.uniform(0.8, 1.0), 3) for _ in range(500)]
        ys = [1.0 if rng.random() < 0.5 else 0.0 for _ in range(500)]
        knots = isotonic_fit(ps, ys)
        assert all(knots[i][1] <= knots[i + 1][1] + 1e-9 for i in range(len(knots) - 1))
        cal = [isotonic_apply(knots, p) for p in ps]
        assert ece(cal, ys) < ece(ps, ys)


class TestPrimitives:
    def test_brier_perfect_is_zero(self) -> None:
        assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == 0.0

    def test_reliability_bins_omit_empty(self) -> None:
        bins = reliability_bins([0.05, 0.95], [0, 1], n_bins=10)
        assert len(bins) == 2  # only the two non-empty bins

    def test_mce_is_max_gap(self) -> None:
        # bin 0.0-0.1: predicts ~0.05 observes 1.0 -> gap ~0.95
        ps = [0.05] * 5 + [0.95] * 5
        ys = [1.0] * 5 + [1.0] * 5
        assert mce(ps, ys) > 0.9

    def test_validation_rejects_out_of_range(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="out of"):
            brier_score([1.5], [1])
        with pytest.raises(ValueError, match="binary"):
            brier_score([0.5], [2])
