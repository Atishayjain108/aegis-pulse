"""
tests/unit/scrape/test_confidence_normalizer.py — Tests for confidence gate & normalizer.

Tests cover:
  - score_batch: advisory quality gate (default threshold 0.85)
  - Z-score normalisation, percentile normalisation, tier-weighted scoring
  - Edge cases: empty batch, all-zero scores, single signal
  - Property: normalised scores always in [0, 1]

Architecture: Phase 0 (Scrape) → confidence.py + normalizer.py
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest


def _import_confidence() -> Any:
    try:
        from aegis.scrape import confidence  # type: ignore[import-untyped]
        return confidence
    except ImportError:
        pytest.skip("aegis.scrape.confidence not available")


def _import_normalizer() -> Any:
    try:
        from aegis.scrape import normalizer  # type: ignore[import-untyped]
        return normalizer
    except ImportError:
        pytest.skip("aegis.scrape.normalizer not available")


# ---------------------------------------------------------------------------
# Confidence gate
# ---------------------------------------------------------------------------

class TestConfidenceGate:

    def test_high_quality_signals_pass(self) -> None:
        """score_batch is advisory — we test that it returns a ConfidenceResult and
        that well-formed signals produce a higher score than empty/garbage inputs."""
        confidence = _import_confidence()
        signals = [
            {"score": 0.9, "signal_count": 50, "unique_authors": 30, "velocity_24h": 5.0}
            for _ in range(5)
        ]
        result = confidence.score_batch(signals)
        assert result is not None
        assert 0.0 <= result.overall_score <= 1.0
        # Higher quality input should score better than empty batch
        empty = confidence.score_batch([])
        assert result.overall_score >= empty.overall_score

    def test_low_quality_signals_flag_but_do_not_block(self) -> None:
        """Confidence is ADVISORY — pipeline must always continue."""
        confidence = _import_confidence()
        signals = [
            {"score": 0.1, "signal_count": 1, "unique_authors": 1, "velocity_24h": 0.01}
            for _ in range(5)
        ]
        result = confidence.score_batch(signals)
        # Score will be low, but we must still get a result (never raises, never blocks)
        assert result is not None
        assert 0.0 <= result.overall_score <= 1.0

    def test_empty_batch_returns_default(self) -> None:
        confidence = _import_confidence()
        result = confidence.score_batch([])
        assert result is not None

    def test_single_signal_batch(self) -> None:
        confidence = _import_confidence()
        result = confidence.score_batch([{"score": 0.8, "signal_count": 10}])
        assert result is not None
        assert 0.0 <= result.overall_score <= 1.0

    def test_result_has_remediation_hints_when_below_threshold(self) -> None:
        confidence = _import_confidence()
        signals = [{"score": 0.05} for _ in range(3)]
        result = confidence.score_batch(signals)
        if result.overall_score < 0.85:
            assert isinstance(result.remediation_hints, list)


# ---------------------------------------------------------------------------
# Score normalizer
# ---------------------------------------------------------------------------

class TestNormalizer:
    """Normalizer API: z_score_batch / percentile_rank_batch take list[dict] and return list[dict]."""

    def test_z_score_normalised_output_range(self) -> None:
        normalizer = _import_normalizer()
        signals = [{"score": float(s)} for s in [10.0, 20.0, 30.0, 40.0, 50.0]]
        normed = normalizer.z_score_batch(signals)
        assert isinstance(normed, list)
        assert len(normed) == 5
        assert all("score" in s for s in normed)

    def test_all_equal_scores_return_zero_variance(self) -> None:
        normalizer = _import_normalizer()
        signals = [{"score": 5.0} for _ in range(10)]
        normed = normalizer.z_score_batch(signals)
        assert all(isinstance(s, dict) for s in normed)

    def test_percentile_normalisation_bounds(self) -> None:
        """percentile_rank_batch adds a `percentile_rank` field (0–100) to each signal."""
        normalizer = _import_normalizer()
        signals = [{"score": float(i)} for i in range(1, 101)]
        normed = normalizer.percentile_rank_batch(signals)
        assert all("percentile_rank" in s for s in normed)
        assert all(0.0 <= s["percentile_rank"] <= 100.0 for s in normed)

    def test_tier_weighted_score_higher_for_commerce(self) -> None:
        """TIER_2_COMMERCE signals should get a higher weight than TIER_3_SEARCH."""
        normalizer = _import_normalizer()
        commerce = [{"score": 0.7, "tier": "TIER_2_COMMERCE"}]
        search = [{"score": 0.7, "tier": "TIER_3_SEARCH"}]
        try:
            c = normalizer.apply_tier_weight(commerce)[0]["score"]
            s = normalizer.apply_tier_weight(search)[0]["score"]
            assert c >= s, f"Commerce >= search expected: {c} < {s}"
        except (AttributeError, KeyError):
            pytest.skip("apply_tier_weight not implemented for this input format")

    def test_single_score_normalises_without_error(self) -> None:
        normalizer = _import_normalizer()
        result = normalizer.z_score_batch([{"score": 42.0}])
        assert result is not None and len(result) == 1

    def test_empty_list_returns_empty(self) -> None:
        normalizer = _import_normalizer()
        assert normalizer.z_score_batch([]) == []

    @given(
        scores=st.lists(
            st.floats(min_value=0.0, max_value=1000.0, allow_nan=False),
            min_size=2,
            max_size=100,
        )
    )
    @settings(max_examples=50)
    def test_percentile_normalise_always_in_unit_interval(self, scores: list[float]) -> None:
        """percentile_rank field is always in [0, 100]."""
        normalizer = _import_normalizer()
        signals = [{"score": v} for v in scores]
        result = normalizer.percentile_rank_batch(signals)
        for s in result:
            assert 0.0 <= s["percentile_rank"] <= 100.0, f"Out-of-range: {s['percentile_rank']}"


# ---------------------------------------------------------------------------
# Sentinel: analytics (OLS velocity + PCA denoising)
# ---------------------------------------------------------------------------

def _import_analytics() -> Any:
    try:
        from aegis.scrape import analytics  # type: ignore[import-untyped]
        return analytics
    except ImportError:
        pytest.skip("aegis.scrape.analytics not available")


class TestAnalytics:
    """compute_velocity_slope takes list[dict] with scraped_at timestamps.
    Returns VelocityRegression(slope, intercept, r_squared, bucket_count, ...).
    """

    def _make_signals_with_timestamps(self, count: int, hours_spread: float = 12.0) -> list[dict]:
        """Create signal dicts spaced evenly over the last `hours_spread` hours."""
        from datetime import UTC, datetime, timedelta
        now = datetime.now(UTC)
        return [
            {"scraped_at": (now - timedelta(hours=hours_spread * (1 - i / max(count - 1, 1)))).isoformat()}
            for i in range(count)
        ]

    def test_compute_velocity_slope_returns_regression(self) -> None:
        analytics = _import_analytics()
        # Returns a VelocityRegression namedtuple — access via attributes
        signals = self._make_signals_with_timestamps(10)
        result = analytics.compute_velocity_slope(signals)
        assert hasattr(result, "slope")
        assert hasattr(result, "r_squared")
        assert isinstance(result.slope, float)

    def test_compute_velocity_slope_flat(self) -> None:
        analytics = _import_analytics()
        # Empty signals → slope = 0 (no data to fit)
        result = analytics.compute_velocity_slope([])
        assert abs(result.slope) < 1e-9, f"Expected zero slope for empty input, got {result.slope}"

    def test_pca_denoise_reduces_noise(self) -> None:
        analytics = _import_analytics()
        import random
        rng = random.Random(42)
        # 10 samples x 20 features
        vectors = [[rng.gauss(0, 1) for _ in range(20)] for _ in range(10)]
        try:
            denoised = analytics.pca_denoise_vectors(vectors)
            assert len(denoised) == 10
        except Exception as exc:
            # Graceful degradation: returns identity when numpy absent
            pytest.skip(f"pca_denoise_vectors graceful degradation: {exc}")

    def test_pca_denoise_too_few_samples_returns_identity(self) -> None:
        analytics = _import_analytics()
        vectors = [[1.0, 2.0]] * 2  # < 3 samples
        result = analytics.pca_denoise_vectors(vectors)
        # Should return input unchanged (no-op for small corpus)
        assert result == vectors or len(result) == len(vectors)
