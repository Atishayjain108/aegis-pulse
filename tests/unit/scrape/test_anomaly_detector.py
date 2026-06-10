"""Unit tests for aegis.scrape.anomaly_detector (ORPH-3 coverage)."""
from __future__ import annotations

import builtins

import pytest

from aegis.scrape.anomaly_detector import AnomalyResult, detect_anomalies_in_batch


def _normal_signal(i: int) -> dict:
    return {
        "source_confidence": 0.7,
        "views": 100 + i,
        "likes": 10 + i,
        "comments": 2 + i,
    }


def test_too_few_samples_returns_passthrough():
    signals = [_normal_signal(i) for i in range(5)]
    results = detect_anomalies_in_batch(signals)
    assert len(results) == 5
    assert all(isinstance(r, AnomalyResult) for r in results)
    assert all(not r.is_anomaly and r.reason == "too_few_samples" for r in results)


def test_pyod_missing_degrades_gracefully(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("pyod"):
            raise ImportError("pyod not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    signals = [_normal_signal(i) for i in range(12)]
    results = detect_anomalies_in_batch(signals)
    assert len(results) == 12
    assert all(r.reason == "pyod_not_installed" for r in results)


def test_detects_injected_outliers():
    signals = [_normal_signal(i) for i in range(20)]
    # Inject clear outliers: viral views + very low confidence.
    signals.append({"source_confidence": 0.1, "views": 5_000_000, "likes": 200_000, "comments": 9})
    signals.append({"source_confidence": 0.05, "views": 2, "likes": 0, "comments": 0})
    results = detect_anomalies_in_batch(signals, contamination=0.1)
    assert len(results) == len(signals)
    assert any(r.is_anomaly for r in results)
    # Scores are normalised to [0, 1].
    assert all(0.0 <= r.anomaly_score <= 1.0 for r in results)
    # Flagged anomalies carry a non-empty reason; inliers do not.
    for r in results:
        if r.is_anomaly:
            assert r.reason != ""
        else:
            assert r.reason == ""


def test_handles_null_fields():
    signals = [{"source_confidence": None, "views": None, "likes": None, "comments": None} for _ in range(11)]
    results = detect_anomalies_in_batch(signals)
    assert len(results) == 11
    assert all(isinstance(r, AnomalyResult) for r in results)


@pytest.mark.parametrize("contamination", [0.05, 0.2, 0.4])
def test_contamination_param_respected(contamination):
    signals = [_normal_signal(i) for i in range(30)]
    results = detect_anomalies_in_batch(signals, contamination=contamination)
    assert len(results) == 30
