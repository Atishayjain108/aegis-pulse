"""
aegis.evolve.drift
==================

Continuous data drift and model performance monitoring.

Two checks run on a configurable interval:

  1. **Data drift** — Kolmogorov-Smirnov distance between the training
     baseline feature distribution and a recent window of prediction
     inputs.  Drift score > ``drift_threshold`` triggers early retraining.

  2. **Performance drop** — week-over-week precision comparison using
     settled outcomes.  Drop > ``performance_drop_threshold`` triggers
     auto-rollback to the previous champion.

Public API:
    DriftDetector.detect_drift(recent_features)          → DriftSnapshot
    DriftDetector.detect_performance_drop()              → DriftSnapshot
    DriftDetector.run_all_checks(recent_features)        → DriftSnapshot
    DriftDetector.persist_snapshot(snapshot)             → bool
    DriftDetector.fetch_latest_snapshot()                → DriftSnapshot | None
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np
import structlog

from aegis.evolve.config import EvolveSettings
from aegis.evolve.constants import (
    ERR_DRIFT_DETECTED,
    ERR_PERFORMANCE_DROP,
    ERR_ROLLBACK_TRIGGERED,
    EVOLVE_FEATURE_DIM,
    MIN_VALID_PRECISION,
)
from aegis.evolve.schemas import DriftSnapshot

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.evolve.drift")


class DriftDetector:
    """
    Monitor data and model drift.

    A ``db_pool`` is optional — when ``None``, persistence calls are
    silently skipped (useful for unit tests and offline analysis).
    """

    def __init__(
        self,
        db_pool: Pool | None = None,
        settings: EvolveSettings | None = None,
        redis_client: object | None = None,
    ) -> None:
        self._pool = db_pool
        self._cfg = settings or EvolveSettings()
        # PASS3-3B: optional Redis client used for the evolve event stream and
        # the Phase 4 killswitch when critical drift forces an execution halt.
        self._redis = redis_client

        # Baseline statistics — initialised lazily from the champion model's
        # stored training distribution (FIX-3, forensic audit).
        self._baseline_mean: np.ndarray | None = None
        self._baseline_std: np.ndarray | None = None
        # False until a real champion training distribution is loaded. While
        # False the drift score is still computed (against a neutral prior) but
        # it is decorative — callers/operators are warned not to trust it for
        # auto-rollback decisions until the first model is promoted.
        self._baseline_initialized: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def detect_drift(self, recent_features: np.ndarray) -> DriftSnapshot:
        """
        Compute data drift score for *recent_features*.

        Uses a KS-distance proxy: mean absolute deviation of
        per-feature means vs the training baseline.
        """
        if self._baseline_mean is None:
            await self._initialize_baseline()

        drift_score = self._compute_ks_distance(recent_features)
        is_drifted = drift_score > self._cfg.drift_threshold

        n = int(recent_features.shape[0])
        feature_stats = {
            "n_samples": n,
            "recent_mean": recent_features.mean(axis=0).tolist() if n > 0 else [],
            "baseline_mean": self._baseline_mean.tolist() if self._baseline_mean is not None else [],
        }

        if is_drifted:
            _log.warning(
                "evolve.drift_detected",
                drift_score=round(drift_score, 4),
                threshold=self._cfg.drift_threshold,
                n_samples=n,
                error_code=ERR_DRIFT_DETECTED,
            )

        return DriftSnapshot(
            drift_score=drift_score,
            is_drifted=is_drifted,
            feature_stats=feature_stats,
        )

    async def detect_performance_drop(self) -> DriftSnapshot:
        """
        Compare precision this week vs last week using settled outcomes.

        Returns a DriftSnapshot; ``should_rollback=True`` if drop exceeds
        the configured threshold.
        """
        if self._pool is None:
            return DriftSnapshot(drift_score=0.0, is_drifted=False)

        try:
            async with self._pool.acquire() as conn:
                recent_rows = await conn.fetch(
                    """
                    SELECT prediction_score, resolution_status
                    FROM prediction_outcomes
                    WHERE settlement_timestamp > NOW() - '7 days'::INTERVAL
                    """
                )
                old_rows = await conn.fetch(
                    """
                    SELECT prediction_score, resolution_status
                    FROM prediction_outcomes
                    WHERE settlement_timestamp BETWEEN
                          NOW() - '14 days'::INTERVAL AND NOW() - '7 days'::INTERVAL
                    """
                )

            recent_precision = self._compute_precision(list(recent_rows))
            old_precision = self._compute_precision(list(old_rows))

            drop_pct = max(
                0.0,
                (old_precision - recent_precision) / max(old_precision, MIN_VALID_PRECISION),
            )
            should_rollback = drop_pct > self._cfg.performance_drop_threshold

            if should_rollback:
                _log.error(
                    "evolve.performance_drop",
                    old_precision=round(old_precision, 4),
                    recent_precision=round(recent_precision, 4),
                    drop_pct=round(drop_pct, 4),
                    error_code=ERR_PERFORMANCE_DROP,
                )

            return DriftSnapshot(
                drift_score=drop_pct,
                is_drifted=should_rollback,
                precision_now=recent_precision,
                precision_prev=old_precision,
                precision_drop=drop_pct,
                should_rollback=should_rollback,
            )

        except Exception as exc:
            _log.error("evolve.performance_check_failed", error=str(exc))
            return DriftSnapshot(drift_score=0.0, is_drifted=False)

    async def run_all_checks(self, recent_features: np.ndarray) -> DriftSnapshot:
        """
        Run both drift and performance checks; merge into a single snapshot.

        The combined snapshot is flagged as drifted if either check fires.
        """
        data_snap = await self.detect_drift(recent_features)
        perf_snap = await self.detect_performance_drop()

        merged = DriftSnapshot(
            drift_score=max(data_snap.drift_score, perf_snap.drift_score),
            is_drifted=data_snap.is_drifted or perf_snap.is_drifted,
            feature_stats=data_snap.feature_stats,
            precision_now=perf_snap.precision_now,
            precision_prev=perf_snap.precision_prev,
            precision_drop=perf_snap.precision_drop,
            should_rollback=perf_snap.should_rollback,
        )

        # PASS3-3B: close the loop — critical drift triggers champion rollback
        # (and, at extreme levels, the Phase 4 killswitch). Best-effort.
        if merged.is_drifted:
            await self._attempt_auto_rollback(merged)

        return merged

    async def persist_snapshot(self, snapshot: DriftSnapshot) -> bool:
        """Write a drift snapshot to the DB.  Returns False on failure."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO drift_snapshots (
                        snapshot_id, drift_score, is_drifted, feature_stats,
                        precision_now, precision_prev, precision_drop,
                        should_rollback, captured_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    snapshot.snapshot_id,
                    snapshot.drift_score,
                    snapshot.is_drifted,
                    json.dumps(snapshot.feature_stats),
                    snapshot.precision_now,
                    snapshot.precision_prev,
                    snapshot.precision_drop,
                    snapshot.should_rollback,
                    snapshot.captured_at,
                )
            return True
        except Exception as exc:
            _log.error("evolve.drift_persist_failed", error=str(exc))
            return False

    async def fetch_latest_snapshot(self) -> DriftSnapshot | None:
        """Return the most recent persisted drift snapshot, or None."""
        if self._pool is None:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT drift_score, is_drifted, feature_stats,
                           precision_now, precision_prev, precision_drop,
                           should_rollback, captured_at
                    FROM drift_snapshots
                    ORDER BY captured_at DESC
                    LIMIT 1
                    """
                )
            if not row:
                return None
            return DriftSnapshot(
                drift_score=row["drift_score"],
                is_drifted=row["is_drifted"],
                feature_stats=json.loads(row["feature_stats"] or "{}"),
                precision_now=row["precision_now"],
                precision_prev=row["precision_prev"],
                precision_drop=row["precision_drop"],
                should_rollback=row["should_rollback"],
                captured_at=row["captured_at"],
            )
        except Exception as exc:
            _log.error("evolve.drift_fetch_failed", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _attempt_auto_rollback(self, snapshot: DriftSnapshot) -> bool:
        """
        PASS3-3B: auto-rollback to the previous champion on critical drift.

        Critical means either the performance check explicitly requested a
        rollback (``should_rollback``) or the merged drift score exceeds
        2× ``drift_threshold``.  At > 3× the threshold the Phase 4 killswitch
        is tripped, halting all outbound execution until a human re-arms it.

        Best-effort end to end: a drift check must never raise because the
        rollback machinery (DB, Redis, Phase 4) is unavailable.

        Returns True when a champion rollback was actually performed.
        """
        critical = (
            snapshot.should_rollback
            or snapshot.drift_score > 2 * self._cfg.drift_threshold
        )
        if not critical:
            _log.info(
                "evolve.drift_warning_not_critical",
                drift_score=round(snapshot.drift_score, 4),
                critical_threshold=round(2 * self._cfg.drift_threshold, 4),
            )
            return False

        _log.warning(
            "evolve.drift_rollback_triggered",
            drift_score=round(snapshot.drift_score, 4),
            threshold=self._cfg.drift_threshold,
            should_rollback=snapshot.should_rollback,
            error_code=ERR_ROLLBACK_TRIGGERED,
        )

        rolled_back = False
        try:
            from aegis.evolve.retrain import RetrainingPipeline

            pipeline = RetrainingPipeline(db_pool=self._pool, settings=self._cfg)
            rolled_back = await pipeline.rollback_champion()
            if rolled_back:
                _log.warning("evolve.drift_rollback_complete")
            else:
                _log.error("evolve.drift_rollback_no_previous_champion")
        except Exception as exc:
            _log.error("evolve.drift_rollback_failed", error=str(exc))

        try:
            from aegis.core.event_bus import STREAM_EVOLVE, publish_event

            await publish_event(
                STREAM_EVOLVE,
                {
                    "event": "auto_rollback",
                    "reason": "critical_drift",
                    "rolled_back": rolled_back,
                    "drift_score": float(snapshot.drift_score),
                    "threshold": float(self._cfg.drift_threshold),
                },
                redis_client=self._redis,
            )
        except Exception as exc:
            _log.debug("evolve.drift_event_publish_failed", error=str(exc))

        if snapshot.drift_score > 3 * self._cfg.drift_threshold:
            await self._trip_killswitch(snapshot)

        return rolled_back

    async def _trip_killswitch(self, snapshot: DriftSnapshot) -> None:
        """Trip the Phase 4 killswitch on extreme drift (> 3× threshold)."""
        if self._redis is None:
            _log.warning(
                "evolve.drift_killswitch_skipped",
                reason="no_redis_client",
                drift_score=round(snapshot.drift_score, 4),
            )
            return
        try:
            from aegis.execute.killswitch.switch import KillSwitch

            ks = KillSwitch(redis_client=self._redis)  # type: ignore[arg-type]
            await ks.trip(
                reason=f"Auto: critical model drift score={snapshot.drift_score:.3f}"
            )
            _log.warning(
                "evolve.drift_killswitch_tripped",
                drift_score=round(snapshot.drift_score, 4),
            )
        except Exception as exc:
            _log.error("evolve.drift_killswitch_failed", error=str(exc))

    async def _initialize_baseline(self) -> None:
        """
        FIX-3 (forensic audit): load the drift baseline from the champion
        model's *real* training feature distribution.

        At champion-promotion time ``RetrainingPipeline`` records the per-feature
        mean and std of the training set into ``model_candidates``
        (``training_feature_mean`` / ``training_feature_std``, migration
        ``0013_feature_snapshots.sql``). Drift is measured against THAT
        distribution — not against an arbitrary ``zeros``/``ones`` origin.

        When no champion exists yet (cold start) or the DB is unavailable, fall
        back to a neutral prior and set ``_baseline_initialized = False`` so the
        signal is treated as decorative until the first real promotion.
        """
        baseline = await self._load_champion_baseline()
        if baseline is not None:
            mean, std = baseline
            if mean.shape == (EVOLVE_FEATURE_DIM,) and std.shape == (EVOLVE_FEATURE_DIM,):
                self._baseline_mean = mean
                # Guard against zero-variance features (division by std later).
                self._baseline_std = np.where(std > 1e-8, std, 1.0)
                self._baseline_initialized = True
                _log.info(
                    "evolve.drift_baseline_loaded",
                    dim=EVOLVE_FEATURE_DIM,
                    source="champion_training_stats",
                )
                return

        # Cold start — no champion training distribution available yet.
        self._baseline_mean = np.zeros(EVOLVE_FEATURE_DIM)
        self._baseline_std = np.ones(EVOLVE_FEATURE_DIM)
        self._baseline_initialized = False
        _log.warning(
            "evolve.drift_baseline_uninitialized",
            dim=EVOLVE_FEATURE_DIM,
            message=(
                "No champion training distribution found. Drift detection is "
                "decorative until the first model promotion populates "
                "model_candidates.training_feature_mean/std."
            ),
        )

    async def _load_champion_baseline(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Fetch the champion model's stored training mean/std, or None."""
        if self._pool is None:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT training_feature_mean, training_feature_std
                    FROM model_candidates
                    WHERE is_champion = TRUE
                      AND training_feature_mean IS NOT NULL
                      AND training_feature_std IS NOT NULL
                    ORDER BY promoted_at DESC NULLS LAST
                    LIMIT 1
                    """
                )
            if not row:
                return None
            mean = np.asarray(json.loads(row["training_feature_mean"]), dtype=float)
            std = np.asarray(json.loads(row["training_feature_std"]), dtype=float)
            return mean, std
        except Exception as exc:
            # Column may not exist yet (pre-migration) or DB unreachable — treat
            # as cold start rather than raising. Drift checks must never crash.
            _log.debug("evolve.drift_baseline_load_failed", error=str(exc))
            return None

    def _compute_ks_distance(self, recent_features: np.ndarray) -> float:
        """
        Simple KS-distance proxy: mean absolute deviation of recent feature
        means from the training baseline.

        Returns a value in [0, 1].
        """
        if recent_features.shape[0] == 0 or self._baseline_mean is None:
            return 0.0

        recent_mean = recent_features.mean(axis=0)

        # Normalise by baseline std to make per-feature distances comparable
        std = self._baseline_std if self._baseline_std is not None else np.ones_like(recent_mean)
        normalised_distance = np.abs(recent_mean - self._baseline_mean) / np.maximum(std, 1e-8)
        return float(min(1.0, float(normalised_distance.mean())))

    @staticmethod
    def _compute_precision(rows: list) -> float:
        """Compute success rate from a list of outcome rows."""
        if not rows:
            return 0.5
        successful = sum(1 for r in rows if r["resolution_status"] == "successful")
        return successful / len(rows)
