"""Tests for `aegis.harden.poisoning`."""

from __future__ import annotations

import numpy as np
import pytest

from aegis.harden.errors import PoisoningDetected
from aegis.harden.poisoning import (
    FeatureShiftDetector,
    GradientAnomalyDetector,
    LabelFlipDetector,
    require_clean,
    scan,
)

# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------


class TestLabelFlipDetector:
    def test_clean_dataset_not_flagged(self) -> None:
        rng = np.random.default_rng(0)
        n = 100
        labels = rng.integers(0, 2, size=n)
        # Reference equals labels — perfect agreement.
        sig = LabelFlipDetector().signal(labels, labels.copy())
        assert sig.flagged is False
        assert sig.severity == 0.0

    def test_high_flip_rate_flagged(self) -> None:
        rng = np.random.default_rng(0)
        labels = rng.integers(0, 2, size=200)
        poisoned = labels.copy()
        poisoned[:60] = 1 - poisoned[:60]  # 30% flip rate
        sig = LabelFlipDetector().signal(poisoned, labels)
        assert sig.flagged is True
        assert sig.severity >= 0.5

    def test_under_threshold_not_flagged(self) -> None:
        labels = np.zeros(200, dtype=int)
        ref = labels.copy()
        ref[:4] = 1  # 2% flip rate (under 5% default)
        sig = LabelFlipDetector().signal(labels, ref)
        assert sig.flagged is False

    def test_shape_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            LabelFlipDetector().signal(np.zeros(5), np.zeros(6))

    def test_empty_input(self) -> None:
        sig = LabelFlipDetector().signal(np.zeros(0), np.zeros(0))
        assert sig.flagged is False


class TestFeatureShiftDetector:
    def test_clean_passes(self) -> None:
        rng = np.random.default_rng(0)
        ref = rng.standard_normal((200, 8))
        new = rng.standard_normal((200, 8))
        sig = FeatureShiftDetector().signal(new, ref)
        assert sig.flagged is False

    def test_mean_shift_flagged(self) -> None:
        rng = np.random.default_rng(0)
        ref = rng.standard_normal((400, 8))
        poisoned = rng.standard_normal((200, 8))
        poisoned[:, 2] += 5.0  # large mean shift in column 2
        sig = FeatureShiftDetector().signal(poisoned, ref)
        assert sig.flagged is True
        assert sig.evidence["worst_column"] == 2.0

    def test_dim_mismatch(self) -> None:
        with pytest.raises(ValueError):
            FeatureShiftDetector().signal(np.zeros((10, 4)), np.zeros((10, 5)))

    def test_empty(self) -> None:
        sig = FeatureShiftDetector().signal(np.zeros((0, 4)), np.zeros((10, 4)))
        assert sig.flagged is False


class TestGradientAnomalyDetector:
    def test_clean_data_not_flagged(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.standard_normal((200, 8))
        sig = GradientAnomalyDetector().signal(x)
        assert sig.flagged is False

    def test_outliers_flagged(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.standard_normal((200, 8))
        # Inject 3 strong outliers
        x[0] += 50.0
        x[1] += 50.0
        x[2] -= 50.0
        sig = GradientAnomalyDetector().signal(x)
        assert sig.flagged is True

    def test_shape_check(self) -> None:
        with pytest.raises(ValueError):
            GradientAnomalyDetector().signal(np.zeros(5))


# ---------------------------------------------------------------------------
# Unified scan
# ---------------------------------------------------------------------------


class TestScan:
    def test_clean_batch_accepted(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.standard_normal((200, 8))
        x_ref = rng.standard_normal((200, 8))
        labels = (x[:, 0] > 0).astype(int)
        ref = labels.copy()
        report = scan(x=x, labels=labels, reference_labels=ref, x_ref=x_ref)
        assert report.decision == "accept"
        assert report.n_samples == 200

    def test_poisoned_labels_rejected(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.standard_normal((200, 8))
        ref = (x[:, 0] > 0).astype(int)
        labels = ref.copy()
        labels[:80] = 1 - labels[:80]  # 40% flip
        report = scan(x=x, labels=labels, reference_labels=ref)
        assert report.decision == "reject"

    def test_feature_shift_rejected(self) -> None:
        rng = np.random.default_rng(0)
        x_ref = rng.standard_normal((400, 8))
        x = rng.standard_normal((200, 8))
        x[:, 2] += 10.0  # major shift
        report = scan(x=x, x_ref=x_ref)
        assert report.decision == "reject"

    def test_small_batch_suppressed(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.standard_normal((10, 4))
        x_ref = rng.standard_normal((10, 4))
        x[:, 2] += 100.0  # huge shift but tiny batch
        report = scan(x=x, x_ref=x_ref)
        assert report.decision in ("accept", "warn")
        # Should NOT reject because batch is under floor.

    def test_signals_present(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.standard_normal((200, 4))
        report = scan(x=x)
        # Only the gradient anomaly detector runs (no labels, no x_ref).
        assert len(report.signals) == 1
        assert report.signals[0].detector == "gradient_anomaly"

    def test_2d_required(self) -> None:
        with pytest.raises(ValueError):
            scan(x=np.zeros(10))

    def test_batch_id_generated_when_absent(self) -> None:
        report = scan(x=np.zeros((50, 4)))
        assert report.batch_id
        assert len(report.batch_id) >= 16

    def test_batch_id_preserved(self) -> None:
        report = scan(x=np.zeros((50, 4)), batch_id="my-batch")
        assert report.batch_id == "my-batch"


class TestRequireClean:
    def test_accept_passes(self) -> None:
        report = scan(x=np.random.default_rng(0).standard_normal((200, 4)))
        require_clean(report)  # must not raise

    def test_reject_raises(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.standard_normal((200, 4))
        labels = (x[:, 0] > 0).astype(int)
        poisoned = labels.copy()
        poisoned[:80] = 1 - poisoned[:80]
        report = scan(x=x, labels=poisoned, reference_labels=labels)
        with pytest.raises(PoisoningDetected) as exc:
            require_clean(report)
        assert exc.value.code == "AEGIS-HARDEN-0040"
