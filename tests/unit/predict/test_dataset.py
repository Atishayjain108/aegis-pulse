"""Tests for `aegis.predict.training.dataset`."""

from __future__ import annotations

from aegis.predict import FEATURE_DIM
from aegis.predict.training.dataset import (
    LabelledSample,
    _label_for_window,
    iter_batches,
    synthetic_dataset,
)


class TestSyntheticDataset:
    def test_count_matches_request(self):
        ds = synthetic_dataset(n_samples=12, feature_dim=FEATURE_DIM)
        assert len(ds) == 12

    def test_sample_shape(self):
        ds = synthetic_dataset(n_samples=3, feature_dim=FEATURE_DIM, window_size=24)
        s = ds[0]
        assert isinstance(s, LabelledSample)
        assert s.window.window_size == 24
        assert s.window.feature_dim == FEATURE_DIM
        assert len(s.window.values) == 24 * FEATURE_DIM

    def test_target_keys(self):
        ds = synthetic_dataset(n_samples=4, feature_dim=FEATURE_DIM)
        for i in range(len(ds)):
            tgt = ds[i].target
            assert "stage" in tgt
            assert "velocity" in tgt
            assert "breakout" in tgt

    def test_seed_determinism(self):
        a = synthetic_dataset(n_samples=4, feature_dim=FEATURE_DIM, seed=42)
        b = synthetic_dataset(n_samples=4, feature_dim=FEATURE_DIM, seed=42)
        for i in range(len(a)):
            assert list(a[i].window.values) == list(b[i].window.values)


class TestLabelForWindow:
    def test_empty_velocity_yields_dormant(self):
        lbl = _label_for_window([], horizon=24)
        assert lbl["stage"].value == "dormant"
        assert lbl["breakout"] == 0

    def test_high_velocity_yields_breakout_or_emerging(self):
        lbl = _label_for_window([5.0] * 30, horizon=24)
        assert lbl["breakout"] == 1
        assert lbl["stage"].value in {"breakout", "emerging"}

    def test_negative_velocity_yields_decline(self):
        lbl = _label_for_window([-1.0] * 30, horizon=24)
        assert lbl["stage"].value in {"declining", "saturated"}


class TestIterBatches:
    def test_batches_cover_dataset(self):
        ds = synthetic_dataset(n_samples=10, feature_dim=FEATURE_DIM)
        seen = 0
        for batch in iter_batches(ds, batch_size=3, shuffle=False):
            seen += len(batch)
            assert len(batch) <= 3
        assert seen == 10

    def test_shuffle_preserves_count(self):
        ds = synthetic_dataset(n_samples=10, feature_dim=FEATURE_DIM)
        seen = sum(len(b) for b in iter_batches(ds, 4, shuffle=True, seed=1))
        assert seen == 10
