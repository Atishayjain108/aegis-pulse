"""
MLflow experiment tracker for AEGIS model training.

Phase 9 evolve layer. Wraps MLflow to provide:
  - Automatic experiment creation per retrain run
  - Parameter logging (all Optuna HPO params)
  - Metric logging (AUC, precision, recall at each trial)
  - Artifact logging (model pickle, feature importance, Evidently report)
  - Model registry integration (staging → production promotion)

MLflow UI: http://localhost:5000 (run: mlflow ui --port 5000)
MLflow runs: ~/.aegis/mlruns/

Falls back to structured logging when MLflow unavailable.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

_log = structlog.get_logger("aegis.evolve.experiment_tracker")

try:
    # Broad except: an installed-but-incompatible MLflow can emit a
    # DeprecationWarning (fatal under pytest's filterwarnings=error) or a
    # pydantic error at import. Degrade to structured-logging fallback instead.
    import mlflow
    import mlflow.sklearn

    _MLFLOW_AVAILABLE = True
except Exception:
    _MLFLOW_AVAILABLE = False

_TRACKING_URI = str(Path.home() / ".aegis" / "mlruns")
_EXPERIMENT_NAME = "aegis-pulse-arbitrage"


class ExperimentTracker:
    """
    MLflow wrapper for AEGIS retrain experiments.

    Usage:
        tracker = ExperimentTracker()
        with tracker.start_run(run_name="weekly_retrain_2026-01-01") as run:
            tracker.log_params({"n_estimators": 100, "max_depth": 5})
            tracker.log_metric("auc", 0.872)
            tracker.log_metric("precision", 0.891)
            tracker.log_artifact(model_path)
    """

    def __init__(self) -> None:
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized or not _MLFLOW_AVAILABLE:
            return
        try:
            mlflow.set_tracking_uri(_TRACKING_URI)
            mlflow.set_experiment(_EXPERIMENT_NAME)
            self._initialized = True
            _log.info("experiment_tracker.initialized", tracking_uri=_TRACKING_URI)
        except Exception as exc:
            _log.warning("experiment_tracker.init_failed", error=str(exc))

    @contextmanager
    def start_run(self, run_name: str) -> Generator[Any, None, None]:
        """Context manager for an MLflow run. No-op if MLflow unavailable."""
        self._ensure_initialized()
        if not _MLFLOW_AVAILABLE:
            yield None
            return
        try:
            with mlflow.start_run(run_name=run_name) as run:
                mlflow.log_param("run_started_at", datetime.now(UTC).isoformat())
                yield run
        except Exception as exc:
            _log.warning("experiment_tracker.run_failed", error=str(exc))
            yield None

    def log_params(self, params: dict) -> None:
        if not _MLFLOW_AVAILABLE:
            _log.debug("experiment_tracker.params", **params)
            return
        with contextlib.suppress(Exception):
            mlflow.log_params(params)

    def log_metric(self, key: str, value: float, step: int | None = None) -> None:
        if not _MLFLOW_AVAILABLE:
            _log.debug("experiment_tracker.metric", key=key, value=value)
            return
        with contextlib.suppress(Exception):
            mlflow.log_metric(key, value, step=step)

    def log_metrics(self, metrics: dict[str, float], step: int | None = None) -> None:
        for k, v in metrics.items():
            self.log_metric(k, v, step=step)

    def log_artifact(self, path: str) -> None:
        if not _MLFLOW_AVAILABLE:
            return
        with contextlib.suppress(Exception):
            mlflow.log_artifact(path)

    def register_model(self, model: Any, model_name: str = "aegis-arbitrage-model") -> str | None:
        """Register model in MLflow registry. Returns model URI."""
        if not _MLFLOW_AVAILABLE:
            return None
        try:
            model_info = mlflow.sklearn.log_model(
                model, artifact_path="model",
                registered_model_name=model_name,
            )
            return model_info.model_uri
        except Exception as exc:
            _log.warning("experiment_tracker.register_failed", error=str(exc))
            return None
