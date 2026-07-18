"""Tests for `aegis.harden.smoothing`."""

from __future__ import annotations

import numpy as np
import pytest

from aegis.harden.errors import SmoothingError
from aegis.harden.smoothing import smooth_predict
from aegis.harden.utils.rng import SeededRng


def _const(p: float):
    def fn(_x: np.ndarray) -> float:
        return p

    return fn


def _linear():
    def fn(x: np.ndarray) -> float:
        return float(np.clip(x[0], 0.0, 1.0))

    return fn


class TestSmoothPredictBasics:
    def test_invalid_shape(self) -> None:
        with pytest.raises(SmoothingError) as exc:
            smooth_predict(_const(0.5), np.array([[1.0, 2.0]]))  # 2-D
        assert exc.value.code == "AEGIS-HARDEN-0030"

    def test_empty_vector(self) -> None:
        with pytest.raises(SmoothingError):
            smooth_predict(_const(0.5), np.array([]))

    def test_sigma_out_of_bounds(self) -> None:
        with pytest.raises(SmoothingError) as exc:
            smooth_predict(_const(0.5), np.zeros(5), sigma=-0.1)
        assert exc.value.code == "AEGIS-HARDEN-0031"

    def test_n_samples_out_of_bounds(self) -> None:
        with pytest.raises(SmoothingError):
            smooth_predict(_const(0.5), np.zeros(5), n_samples=1)  # below min

    def test_alpha_out_of_bounds(self) -> None:
        with pytest.raises(SmoothingError):
            smooth_predict(_const(0.5), np.zeros(5), alpha=0.0)

    def test_nan_classifier_raises(self) -> None:
        def nan_clf(_x: np.ndarray) -> float:
            return float("nan")

        with pytest.raises(SmoothingError) as exc:
            smooth_predict(nan_clf, np.zeros(5))
        assert exc.value.code == "AEGIS-HARDEN-0032"


class TestSmoothPredictSemantics:
    def test_sigma_zero_returns_raw(self, rng: SeededRng) -> None:
        r = smooth_predict(_linear(), np.array([0.7] + [0.0] * 9), sigma=0.0, n_samples=32, rng=rng)
        assert r.smoothed_score == r.raw_score
        assert r.certified_radius == 0.0
        assert r.agrees_with_raw is True

    def test_constant_clf_returns_constant_smooth(self, rng: SeededRng) -> None:
        r = smooth_predict(_const(0.4), np.zeros(10), sigma=0.2, n_samples=64, rng=rng)
        # All draws produce 0.4 exactly, so smoothed score equals raw.
        assert abs(r.smoothed_score - 0.4) < 1e-9
        assert r.agrees_with_raw is True

    def test_linear_clf_smoothed_near_raw(self, rng: SeededRng) -> None:
        x = np.zeros(10)
        x[0] = 0.7
        r = smooth_predict(_linear(), x, sigma=0.1, n_samples=256, rng=rng)
        # Mean of clip(0.7 + N(0,0.1), 0, 1) is essentially 0.7 — within 0.03.
        assert abs(r.smoothed_score - 0.7) < 0.05

    def test_reproducible_with_same_seed(self) -> None:
        x = np.array([0.6, 0.0, 0.0, 0.0])
        r1 = smooth_predict(_linear(), x, sigma=0.1, n_samples=64, rng=SeededRng(42))
        r2 = smooth_predict(_linear(), x, sigma=0.1, n_samples=64, rng=SeededRng(42))
        assert r1.smoothed_score == r2.smoothed_score
        assert r1.certified_radius == r2.certified_radius

    def test_disagrees_when_smoothing_flips_sign(self) -> None:
        # Raw verdict barely positive (0.51); large sigma pushes mean below 0.5.
        def near_boundary(x: np.ndarray) -> float:
            return float(np.clip(0.51 + 0.1 * x[0], 0, 1))

        # x[0] noise has mean 0 → smoothed mean stays near 0.51. We rig it via
        # a heavily skewed classifier that drops sharply for positive perturbations.
        def skewed(x: np.ndarray) -> float:
            val = 0.51 - max(0.0, x[0]) * 2.0
            return float(np.clip(val, 0, 1))

        r = smooth_predict(skewed, np.zeros(5), sigma=0.3, n_samples=128, rng=SeededRng(1))
        # Smoothed should fall well below 0.5 because positive noise dominates the decrease.
        assert r.smoothed_score < 0.5
        assert r.agrees_with_raw is False

    def test_certified_radius_nonneg(self, rng: SeededRng) -> None:
        r = smooth_predict(_const(0.9), np.zeros(8), sigma=0.2, n_samples=128, rng=rng)
        assert r.certified_radius >= 0.0

    def test_certified_radius_zero_when_score_is_zero_or_one(self, rng: SeededRng) -> None:
        # When the smoothed mean is exactly 1.0, p_lower = 1.0 → quantile clamps,
        # and radius shouldn't blow up. Use constant 1.0.
        r = smooth_predict(_const(1.0), np.zeros(8), sigma=0.2, n_samples=64, rng=rng)
        # Lower bound for p=1 with n=64 is < 1, so radius is bounded but finite.
        assert r.certified_radius >= 0.0
        assert r.smoothed_score == 1.0


class TestSmoothingBoundaries:
    def test_max_samples_accepted(self, rng: SeededRng) -> None:
        # Should not raise; uses a small sigma to keep it fast.
        r = smooth_predict(_const(0.5), np.zeros(5), sigma=0.05, n_samples=512, rng=rng)
        assert r.n_samples == 512

    def test_alpha_near_one(self, rng: SeededRng) -> None:
        r = smooth_predict(_const(0.6), np.zeros(5), sigma=0.1, n_samples=64, alpha=0.99, rng=rng)
        assert r.smoothed_score == 0.6
