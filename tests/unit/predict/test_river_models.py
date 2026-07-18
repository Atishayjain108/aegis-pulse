"""Pass 9A — River online ML model tests (graceful with or without River)."""

from __future__ import annotations

import pickle

from aegis.predict.online import river_models
from aegis.predict.online.river_models import (
    OnlineAnomalyScorer,
    OnlineVelocityClassifier,
    get_anomaly_scorer,
    get_velocity_classifier,
)

_FEATS = {
    "velocity_1h": 1.0, "velocity_6h": 2.0, "velocity_24h": 3.0,
    "signal_count": 50.0, "unique_authors": 10.0, "sentiment": 0.5,
    "commercial_intent": 0.3, "novelty": 0.7,
}


def test_predict_proba_in_range_without_river(monkeypatch):
    monkeypatch.setattr(river_models, "_RIVER_AVAILABLE", False)
    clf = OnlineVelocityClassifier()
    p = clf.predict_proba(_FEATS)
    assert isinstance(p, float)
    assert 0.0 <= p <= 1.0
    # No-River path is the deterministic 0.5 floor.
    assert p == 0.5


def test_predict_proba_in_range_with_river():
    clf = OnlineVelocityClassifier()
    p = clf.predict_proba(_FEATS)
    assert isinstance(p, float)
    assert 0.0 <= p <= 1.0


def test_learn_one_does_not_raise_repeatedly():
    clf = OnlineVelocityClassifier()
    for i in range(20):
        clf.learn_one(_FEATS, is_high_velocity=(i % 2 == 0))
    # And still produces a valid probability afterwards.
    assert 0.0 <= clf.predict_proba(_FEATS) <= 1.0


def test_anomaly_score_first_call():
    scorer = OnlineAnomalyScorer()
    score = scorer.score(_FEATS)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_anomaly_score_without_river(monkeypatch):
    monkeypatch.setattr(river_models, "_RIVER_AVAILABLE", False)
    scorer = OnlineAnomalyScorer()
    assert scorer.score(_FEATS) == 0.5


def test_anomaly_score_extreme_outlier():
    scorer = OnlineAnomalyScorer(window_size=20)
    baseline = {"x": 0.5, "y": 0.5}
    for _ in range(30):
        scorer.score(baseline)
    outlier = {"x": 999.0, "y": 999.0}
    score = scorer.score(outlier)
    # Whether or not River is installed, the score stays calibrated in [0, 1].
    assert 0.0 <= score <= 1.0


def test_checkpoint_round_trips(tmp_path, monkeypatch):
    if not river_models._RIVER_AVAILABLE:
        # Without River there is no model to checkpoint; assert the no-op path.
        clf = OnlineVelocityClassifier()
        clf._save_checkpoint()
        return
    ckpt = tmp_path / "online_velocity.pkl"
    monkeypatch.setattr(OnlineVelocityClassifier, "_CHECKPOINT", ckpt)
    clf = OnlineVelocityClassifier()
    clf.learn_one(_FEATS, is_high_velocity=True)
    clf._save_checkpoint()
    assert ckpt.exists()
    with ckpt.open("rb") as f:
        saved = pickle.load(f)  # noqa: S301 — trusted local test fixture
    assert "model" in saved
    assert "scaler" in saved


def test_module_singletons_are_stable():
    assert get_velocity_classifier() is get_velocity_classifier()
    assert get_anomaly_scorer() is get_anomaly_scorer()
