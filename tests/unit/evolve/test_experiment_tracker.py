"""Pass 9E — MLflow experiment tracker tests (graceful fallback when MLflow absent)."""

from __future__ import annotations

from aegis.evolve import experiment_tracker
from aegis.evolve.experiment_tracker import ExperimentTracker


def test_start_run_yields_none_without_mlflow(monkeypatch):
    monkeypatch.setattr(experiment_tracker, "_MLFLOW_AVAILABLE", False)
    tracker = ExperimentTracker()
    with tracker.start_run("test-run") as run:
        assert run is None


def test_log_methods_no_op_without_mlflow(monkeypatch):
    monkeypatch.setattr(experiment_tracker, "_MLFLOW_AVAILABLE", False)
    tracker = ExperimentTracker()
    # None of these should raise.
    tracker.log_params({"a": 1, "b": 2})
    tracker.log_metric("auc", 0.9)
    tracker.log_metrics({"precision": 0.8, "recall": 0.7})
    tracker.log_artifact("does-not-matter.pkl")


def test_register_model_returns_none_without_mlflow(monkeypatch):
    monkeypatch.setattr(experiment_tracker, "_MLFLOW_AVAILABLE", False)
    tracker = ExperimentTracker()
    assert tracker.register_model(object()) is None


def test_log_metrics_delegates_to_log_metric(monkeypatch):
    monkeypatch.setattr(experiment_tracker, "_MLFLOW_AVAILABLE", False)
    tracker = ExperimentTracker()
    seen = {}
    tracker.log_metric = lambda k, v, step=None: seen.__setitem__(k, v)  # type: ignore[method-assign]
    tracker.log_metrics({"x": 1.0, "y": 2.0})
    assert seen == {"x": 1.0, "y": 2.0}


def test_full_run_lifecycle_no_mlflow(monkeypatch):
    monkeypatch.setattr(experiment_tracker, "_MLFLOW_AVAILABLE", False)
    tracker = ExperimentTracker()
    with tracker.start_run("weekly_abc"):
        tracker.log_params({"architecture": "heuristic"})
        tracker.log_metrics({"candidate_auc": 0.7, "champion_auc": 0.6})
    # Reaching here without exception is the assertion.
    assert True
