"""Signal anomaly detector using Isolation Forest (pyod).

Detects outlier signals based on confidence, engagement metrics, and
platform distribution. Uses real DB data only — no synthetic inputs.

Usage:
    from aegis.scrape.anomaly_detector import detect_anomalies_in_batch, AnomalyResult

    results = detect_anomalies_in_batch(signals, contamination=0.1)
    for sig, result in zip(signals, results):
        if result.is_anomaly:
            log.warning("anomaly detected", score=result.anomaly_score, reason=result.reason)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnomalyResult:
    is_anomaly: bool
    anomaly_score: float  # higher = more anomalous
    reason: str


def detect_anomalies_in_batch(
    signals: list[dict[str, Any]],
    *,
    contamination: float = 0.1,
) -> list[AnomalyResult]:
    """Detect anomalies in a batch of signal dicts.

    Args:
        signals: list of signal dicts with keys: source_confidence, views,
                 likes, comments (all nullable).
        contamination: fraction of expected anomalies (0.0–0.5).

    Returns:
        One AnomalyResult per signal, in the same order.
    """
    if len(signals) < 10:
        return [AnomalyResult(is_anomaly=False, anomaly_score=0.0, reason="too_few_samples")] * len(signals)

    try:
        import numpy as np
        from pyod.models.iforest import IForest
    except ImportError:
        return [AnomalyResult(is_anomaly=False, anomaly_score=0.0, reason="pyod_not_installed")] * len(signals)

    features = np.array([
        [
            float(s.get("source_confidence") or 0.5),
            float(s.get("views") or 0),
            float(s.get("likes") or 0),
            float(s.get("comments") or 0),
        ]
        for s in signals
    ], dtype=np.float64)

    # Log-scale engagement to reduce skew from viral outliers
    features[:, 1:] = np.log1p(features[:, 1:])

    clf = IForest(contamination=contamination, random_state=42, n_estimators=100)
    clf.fit(features)

    labels = clf.labels_          # 0 = inlier, 1 = anomaly
    scores = clf.decision_scores_  # higher = more anomalous

    # Normalise scores to [0, 1]
    s_min, s_max = scores.min(), scores.max()
    norm_scores = (scores - s_min) / (s_max - s_min + 1e-9)

    results = []
    for i, sig in enumerate(signals):
        is_anom = bool(labels[i] == 1)
        reason = ""
        if is_anom:
            conf = float(sig.get("source_confidence") or 0.5)
            if conf < 0.3:
                reason = "very_low_confidence"
            elif float(sig.get("views") or 0) > 1e6:
                reason = "viral_outlier_views"
            elif float(sig.get("likes") or 0) > 1e5:
                reason = "viral_outlier_likes"
            else:
                reason = "isolation_forest_outlier"
        results.append(AnomalyResult(
            is_anomaly=is_anom,
            anomaly_score=float(norm_scores[i]),
            reason=reason,
        ))
    return results


__all__ = ["AnomalyResult", "detect_anomalies_in_batch"]
