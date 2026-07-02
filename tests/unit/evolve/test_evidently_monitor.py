"""Pass 9C — Evidently monitor tests (graceful fallback when Evidently absent)."""

from __future__ import annotations

from aegis.evolve import evidently_monitor
from aegis.evolve.evidently_monitor import EvidentlyMonitor


async def test_returns_fallback_when_unavailable(monkeypatch):
    monkeypatch.setattr(evidently_monitor, "_EVIDENTLY_AVAILABLE", False)
    monitor = EvidentlyMonitor()
    result = await monitor.run_drift_report(reference_df=None, current_df=None)
    assert result["available"] is False
    assert result["fallback"] == "native_ks_distance"


def test_extract_drift_detected_handles_empty():
    monitor = EvidentlyMonitor()
    assert monitor._extract_drift_detected({}) is False
    assert monitor._extract_drift_detected({"metrics": []}) is False


def test_extract_drifted_features_handles_empty():
    monitor = EvidentlyMonitor()
    assert monitor._extract_drifted_features({}) == []


def test_extract_drift_detected_parses_metric():
    monitor = EvidentlyMonitor()
    result = {
        "metrics": [
            {"metric": "DatasetDriftMetric", "result": {"dataset_drift": True}},
        ]
    }
    assert monitor._extract_drift_detected(result) is True


def test_extract_drifted_features_parses_columns():
    monitor = EvidentlyMonitor()
    result = {
        "metrics": [
            {
                "result": {
                    "drift_by_columns": {
                        "roi": {"drift_detected": True},
                        "score": {"drift_detected": False},
                    }
                }
            }
        ]
    }
    assert monitor._extract_drifted_features(result) == ["roi"]
