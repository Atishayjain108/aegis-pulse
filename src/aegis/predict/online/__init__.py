"""Online (incremental) machine-learning models for the Phase 3 predict layer."""

from __future__ import annotations

from aegis.predict.online.river_models import (
    OnlineAnomalyScorer,
    OnlineVelocityClassifier,
    get_anomaly_scorer,
    get_velocity_classifier,
)

__all__ = [
    "OnlineAnomalyScorer",
    "OnlineVelocityClassifier",
    "get_anomaly_scorer",
    "get_velocity_classifier",
]
