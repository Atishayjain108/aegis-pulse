"""
Online machine learning models using River library.

Phase 3 predict layer. Complements the weekly retrain cycle with real-time
incremental learning. Every signal that flows through the system updates the
online model immediately — no retraining lag.

Models:
  - OnlineVelocityClassifier: classifies trend velocity as rising/stable/falling
    using Hoeffding Adaptive Tree (HAT) — handles concept drift natively
  - OnlineAnomalyScorer: real-time anomaly detection using Half-Space Trees

River runs in-process, zero latency, zero infrastructure.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, ClassVar

import structlog

_log = structlog.get_logger("aegis.predict.online")

try:
    from river import anomaly, preprocessing, tree

    _RIVER_AVAILABLE = True
except ImportError:
    _RIVER_AVAILABLE = False


class OnlineVelocityClassifier:
    """
    Incrementally updated trend velocity classifier.
    Uses Hoeffding Adaptive Tree — handles distribution drift natively.

    Input features: velocity_1h, velocity_6h, velocity_24h, signal_count,
                    unique_authors, sentiment, commercial_intent, novelty
    Output: probability of HIGH_VELOCITY (true=rising, false=stable/falling)

    Updates on every signal. Checkpoint saved every 1000 updates.
    """

    _CHECKPOINT: ClassVar[Path] = Path.home() / ".aegis" / "models" / "online_velocity.pkl"
    _FEATURES: ClassVar[list[str]] = [
        "velocity_1h", "velocity_6h", "velocity_24h",
        "signal_count", "unique_authors", "sentiment",
        "commercial_intent", "novelty",
    ]

    def __init__(self) -> None:
        self._model: Any = None
        self._scaler: Any = None
        self._update_count = 0
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not _RIVER_AVAILABLE:
            return
        try:
            if self._CHECKPOINT.exists():
                with self._CHECKPOINT.open("rb") as f:
                    saved = pickle.load(f)  # noqa: S301 — trusted local file
                self._model = saved["model"]
                self._scaler = saved["scaler"]
                _log.info("online_velocity.loaded_checkpoint")
            else:
                self._model = tree.HoeffdingAdaptiveTreeClassifier(
                    grace_period=100,
                    leaf_prediction="nb",
                    nb_threshold=0,
                )
                self._scaler = preprocessing.StandardScaler()
                _log.info("online_velocity.new_model")
            self._loaded = True
        except Exception as exc:
            _log.warning("online_velocity.load_failed", error=str(exc))

    def predict_proba(self, features: dict[str, float]) -> float:
        """Return P(HIGH_VELOCITY). Falls back to 0.5 when River unavailable."""
        self._ensure_loaded()
        if not _RIVER_AVAILABLE or self._model is None:
            return 0.5
        try:
            x = {k: features.get(k, 0.0) for k in self._FEATURES}
            x_scaled = self._scaler.transform_one(x)
            proba = self._model.predict_proba_one(x_scaled)
            return float(proba.get(True, 0.5))
        except Exception:
            return 0.5

    def learn_one(self, features: dict[str, float], is_high_velocity: bool) -> None:
        """Update model with one new labeled example."""
        self._ensure_loaded()
        if not _RIVER_AVAILABLE or self._model is None:
            return
        try:
            x = {k: features.get(k, 0.0) for k in self._FEATURES}
            x_scaled = self._scaler.learn_one(x).transform_one(x)
            self._model.learn_one(x_scaled, is_high_velocity)
            self._update_count += 1
            if self._update_count % 1000 == 0:
                self._save_checkpoint()
        except Exception as exc:
            _log.debug("online_velocity.learn_failed", error=str(exc))

    def _save_checkpoint(self) -> None:
        try:
            self._CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
            with self._CHECKPOINT.open("wb") as f:
                pickle.dump({"model": self._model, "scaler": self._scaler}, f)
            _log.debug("online_velocity.checkpoint_saved", updates=self._update_count)
        except Exception as exc:
            _log.warning("online_velocity.checkpoint_failed", error=str(exc))


class OnlineAnomalyScorer:
    """
    Real-time anomaly detection using Half-Space Trees.
    Scores each signal batch for statistical anomalies vs recent baseline.

    Anomaly score > 0.7 → potential market event worth immediate investigation.
    Score is calibrated: 0.5 is expected baseline, 1.0 is extreme outlier.
    """

    def __init__(self, n_trees: int = 25, height: int = 8, window_size: int = 250) -> None:
        self._model: Any = None
        self._window_size = window_size
        self._n_trees = n_trees
        self._height = height
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded or not _RIVER_AVAILABLE:
            return
        try:
            self._model = anomaly.HalfSpaceTrees(
                n_trees=self._n_trees,
                height=self._height,
                window_size=self._window_size,
                seed=42,
            )
            self._loaded = True
        except Exception as exc:
            _log.warning("online_anomaly.init_failed", error=str(exc))

    def score(self, features: dict[str, float]) -> float:
        """Return anomaly score 0-1. > 0.7 = worth investigating."""
        self._ensure_loaded()
        if not _RIVER_AVAILABLE or self._model is None:
            return 0.5
        try:
            score = self._model.score_one(features)
            self._model.learn_one(features)
            return float(min(1.0, max(0.0, score)))
        except Exception:
            return 0.5


# Module-level singletons (one per process)
_velocity_classifier = OnlineVelocityClassifier()
_anomaly_scorer = OnlineAnomalyScorer()


def get_velocity_classifier() -> OnlineVelocityClassifier:
    return _velocity_classifier


def get_anomaly_scorer() -> OnlineAnomalyScorer:
    return _anomaly_scorer
