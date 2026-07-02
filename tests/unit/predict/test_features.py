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


# --------------------------------------------------------------------------
# PASS2-2E: schema 3.1.0 — four new features + legacy padding
# --------------------------------------------------------------------------
class TestPass2NewFeatures:
    _END = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    def _row(self, *, platform="reddit", title="some title here", author=None,
             region=None, minutes_ago=10):
        from datetime import timedelta

        row = {
            "platform": platform,
            "title": title,
            "captured_at": self._END - timedelta(minutes=minutes_ago),
        }
        if author is not None:
            row["author_id"] = author
        if region is not None:
            row["region"] = region
        return row

    def _window(self, rows, size=24):
        return build_window_from_rows(
            trend_id="t-2e",
            tenant_id="default",
            rows=rows,
            window_end=self._END,
            window_size=size,
        )

    def _last_bucket(self, fw, name):
        idx = list(FEATURE_NAMES).index(name)
        offset = (fw.window_size - 1) * fw.feature_dim
        return fw.values[offset + idx]

    def test_feature_names_length_matches_dim_24(self):
        assert len(FEATURE_NAMES) == FEATURE_DIM == 24

    def test_new_features_zero_on_empty_signals(self):
        fw = self._window([])
        for name in (
            "cross_platform_coherence",
            "temporal_autocorr_lag1",
            "author_diversity_ratio",
            "geo_spread_entropy",
        ):
            assert self._last_bucket(fw, name) == 0.0

    def test_new_features_within_documented_ranges(self):
        rows = [
            self._row(platform=p, title=f"breaking story {i}", author=f"a{i}",
                      region=r, minutes_ago=5 + i)
            for i, (p, r) in enumerate(
                [("reddit", "US"), ("hacker_news", "US"), ("flipkart", "IN"),
                 ("google_news", "EU"), ("reddit", "IN")]
            )
        ]
        fw = self._window(rows)
        assert 0.0 <= self._last_bucket(fw, "cross_platform_coherence") <= 1.0
        assert -1.0 <= self._last_bucket(fw, "temporal_autocorr_lag1") <= 1.0
        assert 0.0 <= self._last_bucket(fw, "author_diversity_ratio") <= 1.0
        assert 0.0 <= self._last_bucket(fw, "geo_spread_entropy") <= 1.0

    def test_cross_platform_coherence_high_for_identical_text(self):
        title = "nvidia announces record q3 revenue"
        rows = [
            self._row(platform=p, title=title, minutes_ago=5)
            for p in ("reddit", "hacker_news", "google_news")
        ]
        fw = self._window(rows)
        assert self._last_bucket(fw, "cross_platform_coherence") >= 0.99

    def test_author_diversity_zero_when_single_author(self):
        rows = [
            self._row(title=f"post number {i}", author="same_author", minutes_ago=5)
            for i in range(6)
        ]
        fw = self._window(rows)
        assert self._last_bucket(fw, "author_diversity_ratio") == 0.0

    def test_geo_spread_entropy_one_for_equal_distribution(self):
        rows = [
            self._row(title=f"story {i}", region=r, minutes_ago=5)
            for i, r in enumerate(["US", "US", "IN", "IN", "EU", "EU"])
        ]
        fw = self._window(rows)
        assert abs(self._last_bucket(fw, "geo_spread_entropy") - 1.0) < 1e-9

    def test_pad_legacy_window_preserves_first_20_values(self):
        from aegis.predict import LEGACY_FEATURE_NAMES_V3
        from aegis.predict.features.builder import pad_legacy_window
        from aegis.predict.schemas import FeatureWindow

        legacy_values = [float(i % 7) / 10 for i in range(8 * 20)]
        legacy = FeatureWindow(
            trend_id="legacy-1",
            window_size=8,
            feature_dim=20,
            feature_names=LEGACY_FEATURE_NAMES_V3,
            values=legacy_values,
            captured_at=self._END,
        )
        padded = pad_legacy_window(legacy)
        assert padded.feature_dim == 24
        assert len(padded.values) == 8 * 24
        for t in range(8):
            assert padded.values[t * 24 : t * 24 + 20] == legacy_values[t * 20 : (t + 1) * 20]
            assert padded.values[t * 24 + 20 : (t + 1) * 24] == [0.0] * 4

    async def test_inference_runner_accepts_legacy_20_dim_window(self):
        from aegis.predict import LEGACY_FEATURE_NAMES_V3
        from aegis.predict.inference.runner import InferenceRunner
        from aegis.predict.schemas import FeatureWindow

        legacy = FeatureWindow(
            trend_id="legacy-2",
            window_size=24,
            feature_dim=20,
            feature_names=LEGACY_FEATURE_NAMES_V3,
            values=[0.1] * (24 * 20),
            captured_at=self._END,
        )
        result = await InferenceRunner().run(
            tenant_id="default", trend_id="legacy-2", window=legacy
        )
        assert result.bundle is not None
        assert result.bundle.trend_id == "legacy-2"
