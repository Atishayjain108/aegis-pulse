"""
Abstract base class for every predictor in the apex.

A `Predictor`:
    * Takes a `FeatureWindow` (and optionally a `CreatorGraph`).
    * Returns a `PredictionBundle` covering the configured horizons.
    * Reports its `model_id`, `kind`, and `version` via properties.
    * Never raises in normal operation — wraps its own internals
      with `resilient_call()` and degrades to a documented fallback.

Subclasses implement `_predict_inner(...)`. The base class handles
timing, hashing, schema validation, and exception trapping.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import abc
import time
from datetime import UTC, datetime

import structlog

from ..constants import INFERENCE_HARD_TIMEOUT_S
from ..features.builder import feature_window_hash
from ..features.graph import CreatorGraph
from ..schemas import (
    FeatureWindow,
    ModelKind,
    PredictionBundle,
    UncertaintyMethod,
)

_log = structlog.get_logger("aegis.predict.models.base")


class Predictor(abc.ABC):
    """Common interface for every model in the apex."""

    @property
    @abc.abstractmethod
    def model_id(self) -> str:
        """Globally unique id of this loaded model (e.g. 'patchtst-3.0.0-sha:ab12...')."""

    @property
    @abc.abstractmethod
    def kind(self) -> ModelKind:
        """Coarse type — used by the registry and the UI."""

    @property
    def version(self) -> str:
        return "0.0.0"

    @property
    def uncertainty_method(self) -> UncertaintyMethod:
        return UncertaintyMethod.NONE

    @property
    def is_heuristic_only(self) -> bool:
        return self.kind == ModelKind.HEURISTIC

    # ------------------------------------------------------------------
    # Public entry point — never raises.
    # ------------------------------------------------------------------
    async def predict(
        self,
        window: FeatureWindow,
        *,
        graph: CreatorGraph | None = None,
        horizons: tuple[int, ...] = (1, 6, 24, 72),
        seed: int = 0,
    ) -> PredictionBundle:
        """Run the model. Always returns a valid bundle."""
        started = datetime.now(UTC)
        t0 = time.perf_counter()
        try:
            preds = await self._predict_inner(
                window=window,
                graph=graph,
                horizons=horizons,
                seed=seed,
            )
        except Exception as exc:
            _log.exception(
                "predictor.failed_falling_back",
                model_id=self.model_id,
                trend_id=window.trend_id,
                error=str(exc),
            )
            # Lazy import: avoid circular at module load.
            from .heuristic import heuristic_predict

            preds = heuristic_predict(window=window, graph=graph, horizons=horizons)
            kind = ModelKind.HEURISTIC
            method = UncertaintyMethod.NONE
            mid = "heuristic-fallback-3.0.0"
            is_h = True
        else:
            kind = self.kind
            method = self.uncertainty_method
            mid = self.model_id
            is_h = self.is_heuristic_only

        finished = datetime.now(UTC)
        return PredictionBundle(
            trend_id=window.trend_id,
            correlation_id=window.correlation_id,
            tenant_id=window.tenant_id,
            predictions=preds,
            model_id=mid,
            model_kind=kind,
            model_version=self.version,
            uncertainty_method=method,
            seed=seed,
            is_heuristic_only=is_h,
            started_at=started,
            finished_at=finished,
            duration_ms=round((time.perf_counter() - t0) * 1000.0, 3),
            feature_window_hash=feature_window_hash(window),
        )

    @abc.abstractmethod
    async def _predict_inner(
        self,
        *,
        window: FeatureWindow,
        graph: CreatorGraph | None,
        horizons: tuple[int, ...],
        seed: int,
    ) -> list:
        """Subclass hook — return list[Prediction] (one per horizon)."""

    # ------------------------------------------------------------------
    # Helper used by every subclass for tail-of-window summaries.
    # ------------------------------------------------------------------
    @staticmethod
    def latest_bucket(window: FeatureWindow) -> dict[str, float]:
        """Return the last bucket as a name→value dict."""
        d = window.feature_dim
        last_offset = (window.window_size - 1) * d
        last = window.values[last_offset : last_offset + d]
        return dict(zip(window.feature_names, last, strict=False))


__all__ = ["INFERENCE_HARD_TIMEOUT_S", "Predictor"]
