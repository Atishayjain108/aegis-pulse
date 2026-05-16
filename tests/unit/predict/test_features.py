"""Feature-engineering tests."""

from __future__ import annotations

from datetime import UTC, datetime

from aegis.predict import FEATURE_DIM, FEATURE_NAMES
from aegis.predict.features.builder import build_window_from_rows
from aegis.predict.features.graph import build_creator_graph
from aegis.predict.features.velocity import compute_velocities


# --------------------------------------------------------------------------
# Velocity
# --------------------------------------------------------------------------
class TestVelocity:
    def test_compute_velocities_returns_velocity_window(self):
        # 24 hourly counts, increasing
        counts = [float(i) for i in range(24)]
        v = compute_velocities(counts)
        # VelocityWindow exposes v1, v6, v24 lists of length T.
        assert hasattr(v, "v1")
        assert hasattr(v, "v6")
        assert hasattr(v, "v24")
        assert len(v.v1) == len(counts)
        for arr in (v.v1, v.v6, v.v24):
            for x in arr:
                assert isinstance(x, float)

    def test_zero_counts_yields_zero_velocity(self):
        counts = [0.0] * 24
        v = compute_velocities(counts)
        assert all(x == 0.0 for x in v.v1)
        assert all(x == 0.0 for x in v.v6)
        assert all(x == 0.0 for x in v.v24)

    def test_increasing_counts_yields_positive_velocity(self):
        # log1p-difference of monotonically increasing counts is positive.
        counts = [float(i) for i in range(48)]
        v = compute_velocities(counts)
        # By position 24, v24 has a full lookback and should be positive.
        assert v.v24[24] > 0.0
        assert v.v6[12] > 0.0


# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------
class TestBuilder:
    def test_build_window_shape(self, synthetic_signals, utc_now):
        fw = build_window_from_rows(
            tenant_id="t1",
            trend_id="trend-1",
            rows=synthetic_signals,
            window_end=utc_now,
            window_size=168,
        )
        assert fw.window_size == 168
        assert fw.feature_dim == FEATURE_DIM
        assert len(fw.values) == 168 * FEATURE_DIM

    def test_empty_rows_yields_only_cyclic_features_set(self, utc_now):
        # An empty signal stream produces a window where only the
        # cyclic time features (indices 16..19) are non-zero —
        # those are derived from the bucket timestamp, not from
        # signal content.
        fw = build_window_from_rows(
            tenant_id="t1",
            trend_id="empty",
            rows=[],
            window_end=utc_now,
            window_size=24,
        )
        rows = fw.as_2d()
        for r in rows:
            for i, v in enumerate(r):
                if i in (16, 17, 18, 19):
                    continue  # cyclic features are allowed non-zero
                assert v == 0.0, f"non-cyclic feature {i} unexpectedly nonzero: {v}"

    def test_correlation_id_default(self, synthetic_signals, utc_now):
        fw = build_window_from_rows(
            tenant_id="t1",
            trend_id="t1",
            rows=synthetic_signals,
            window_end=utc_now,
        )
        assert fw.correlation_id == "t1"

    def test_window_size_bumps_truncate(self, synthetic_signals, utc_now):
        # 24-hour window, only 24 hours of data inside
        fw = build_window_from_rows(
            tenant_id="t1",
            trend_id="x",
            rows=synthetic_signals,
            window_end=utc_now,
            window_size=24,
        )
        assert fw.window_size == 24
        assert len(fw.values) == 24 * FEATURE_DIM

    def test_naive_window_end_coerced_to_utc(self, synthetic_signals):
        naive = datetime(2026, 5, 9, 12, 0, 0)
        fw = build_window_from_rows(
            tenant_id="t1",
            trend_id="x",
            rows=synthetic_signals,
            window_end=naive,
            window_size=24,
        )
        assert fw.captured_at.tzinfo is not None


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------
class TestGraph:
    def test_build_creator_graph(self, synthetic_signals):
        g = build_creator_graph(trend_id="trend-1", rows=synthetic_signals)
        assert g.n_authors > 0
        # Two platforms in synthetic_signals
        assert g.n_platforms >= 2
        assert 0.0 <= g.density <= 1.0
        assert 0.0 <= g.coordination_score <= 1.0

    def test_empty_rows(self):
        g = build_creator_graph(trend_id="empty", rows=[])
        assert g.n_authors == 0
        assert g.n_platforms == 0
        assert g.density == 0.0
        assert g.coordination_score == 0.0

    def test_anonymous_rows_dropped(self):
        rows = [
            {
                "id": "a",
                "platform": "twitter",
                "captured_at": datetime.now(UTC),
                "author_id": None,
                "external_id": "x",
            }
        ]
        g = build_creator_graph(trend_id="anon", rows=rows)
        assert g.n_authors == 0


# --------------------------------------------------------------------------
# FEATURE_NAMES contract
# --------------------------------------------------------------------------
class TestFeatureNames:
    def test_length_matches_feature_dim(self):
        assert len(FEATURE_NAMES) == FEATURE_DIM

    def test_unique(self):
        assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES)
