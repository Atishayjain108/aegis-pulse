"""Pass 10 — 24-dim feature schema + legacy 20-dim padding migration.

Schema 3.1.0 appends 4 features (cross_platform_coherence,
temporal_autocorr_lag1, author_diversity_ratio, geo_spread_entropy) at the
tail so the first 20 indices stay binary-compatible with 3.0.0 windows.
`pad_legacy_window` zero-fills the new tail.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.predict import (
    FEATURE_DIM,
    FEATURE_NAMES,
    LEGACY_FEATURE_DIM_V3,
    LEGACY_FEATURE_NAMES_V3,
)
from aegis.predict.features.builder import pad_legacy_window
from aegis.predict.schemas import FeatureWindow


def test_feature_dim_is_24() -> None:
    assert FEATURE_DIM == 24
    assert len(FEATURE_NAMES) == 24


def test_schema_version_is_3_1_0() -> None:
    """The 24-dim layout is schema 3.1.0 (4 features appended to 3.0.0)."""
    from aegis.predict import FEATURE_SCHEMA_VERSION

    assert FEATURE_SCHEMA_VERSION == "3.1.0"
    assert len(FEATURE_NAMES) - LEGACY_FEATURE_DIM_V3 == 4


def test_four_new_features_at_tail() -> None:
    """Invariant: the 4 new features are appended, not interleaved."""
    assert FEATURE_NAMES[20:] == (
        "cross_platform_coherence",
        "temporal_autocorr_lag1",
        "author_diversity_ratio",
        "geo_spread_entropy",
    )


def test_legacy_dim_preserved() -> None:
    """First 20 indices stay binary-compatible with schema 3.0.0."""
    assert LEGACY_FEATURE_DIM_V3 == 20
    assert FEATURE_NAMES[:20] == LEGACY_FEATURE_NAMES_V3


def _legacy_window(window_size: int = 4) -> FeatureWindow:
    values = [float(i) for i in range(window_size * LEGACY_FEATURE_DIM_V3)]
    return FeatureWindow(
        trend_id="t-legacy",
        window_size=window_size,
        feature_dim=LEGACY_FEATURE_DIM_V3,
        feature_names=LEGACY_FEATURE_NAMES_V3,
        values=values,
        captured_at=datetime.now(UTC),
    )


def test_pad_legacy_window_upgrades_dim() -> None:
    """Happy path: 20-dim window padded to 24-dim, length grows correctly."""
    fw = pad_legacy_window(_legacy_window())
    assert fw.feature_dim == FEATURE_DIM
    assert len(fw.values) == fw.window_size * FEATURE_DIM


def test_pad_preserves_original_values() -> None:
    """The original 20 values per timestep survive unchanged; tail is zero."""
    legacy = _legacy_window(window_size=2)
    fw = pad_legacy_window(legacy)
    rows = fw.as_2d()
    assert rows[0][:20] == [float(i) for i in range(20)]
    assert rows[0][20:] == [0.0, 0.0, 0.0, 0.0]


def test_pad_noop_for_current_dim() -> None:
    """Edge case: a window already at FEATURE_DIM returns unchanged."""
    cur = FeatureWindow(
        trend_id="t-cur",
        window_size=2,
        feature_dim=FEATURE_DIM,
        feature_names=FEATURE_NAMES,
        values=[0.0] * (2 * FEATURE_DIM),
        captured_at=datetime.now(UTC),
    )
    assert pad_legacy_window(cur) is cur


def test_pad_rejects_wrong_dim() -> None:
    """Failure path: a window that is neither 20 nor 24 dim raises.

    Built via model_construct to bypass the FeatureWindow schema's own
    feature_names validator and exercise pad_legacy_window's guard directly.
    """
    bad = FeatureWindow.model_construct(
        trend_id="t-bad",
        window_size=2,
        feature_dim=10,
        feature_names=tuple(f"f{i}" for i in range(10)),
        values=[0.0] * 20,
        captured_at=datetime.now(UTC),
    )
    with pytest.raises(ValueError):
        pad_legacy_window(bad)
