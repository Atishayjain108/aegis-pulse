"""Cover aegis.scrape.anomaly_detector (Isolation Forest signal anomalies)."""

from __future__ import annotations

from typing import Any

from aegis.scrape.anomaly_detector import AnomalyResult, detect_anomalies_in_batch


def test_too_few_samples_returns_safe_defaults() -> None:
    sigs: list[dict[str, Any]] = [{"source_confidence": 0.5} for _ in range(3)]
    out = detect_anomalies_in_batch(sigs)
    assert len(out) == 3
    assert all(isinstance(r, AnomalyResult) for r in out)
    assert all(not r.is_anomaly and r.reason == "too_few_samples" for r in out)


def test_full_batch_returns_one_result_per_signal() -> None:
    # 20 normal signals + 1 obvious outlier (very high engagement, low confidence)
    sigs: list[dict[str, Any]] = [
        {"source_confidence": 0.8, "views": 100, "likes": 10, "comments": 2}
        for _ in range(20)
    ]
    sigs.append({"source_confidence": 0.1, "views": 5_000_000, "likes": 400_000, "comments": 9999})
    out = detect_anomalies_in_batch(sigs, contamination=0.1)
    assert len(out) == len(sigs)
    assert all(0.0 <= r.anomaly_score <= 1.0 for r in out)
    # reason is one of the known strings (pyod present) or the not-installed fallback
    valid = {
        "", "very_low_confidence", "viral_outlier_views", "viral_outlier_likes",
        "isolation_forest_outlier", "pyod_not_installed",
    }
    assert all(r.reason in valid for r in out)
