"""
aegis.evolve.retrain
====================

Weekly model retraining pipeline.

Steps executed every Sunday 2 AM UTC:

  1. Fetch the last 30 days of settled trade outcomes (ground truth).
  2. Reject run if < min_outcomes_for_retrain samples exist.
  3. Pre-process outcomes → feature matrix X + binary labels y.
  4. Run HPO (Optuna) for each architecture; train candidate models.
  5. Compare best candidate AUC vs current champion AUC.
  6. Promote candidate to champion if AUC improvement > threshold.
  7. Persist retrain audit row.

Shadow deployment (future):  new model runs in parallel on a held-out
set before promotion.  Implemented as a hook point here.

Public API:
    RetrainingPipeline.run_weekly_retrain(triggered_by) → RetrainRun
    RetrainingPipeline.get_champion_auc()               → float
    RetrainingPipeline.rollback_champion()              → bool
    RetrainingPipeline.fetch_recent_runs(limit)         → list[RetrainRun]
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from aegis.evolve.config import EvolveSettings
from aegis.evolve.constants import (
    DEFAULT_CHAMPION_AUC,
    ERR_INSUFFICIENT_OUTCOMES,
    ERR_RETRAIN_GENERAL,
    ERR_SHADOW_REGISTER_FAILED,
    ERR_TRAINING_FAILED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NO_IMPROVEMENT,
    STATUS_RUNNING,
    STATUS_SHADOW_DEPLOYED,
    SUPPORTED_ARCHITECTURES,
    TRIGGER_MANUAL,
)
from aegis.evolve.errors import TrainingFailedError
from aegis.evolve.hpo import optimize_hyperparameters
from aegis.evolve.outcomes import OutcomeRecorder
from aegis.evolve.schemas import ModelCandidate, RetrainRun, TradeOutcome

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.evolve.retrain")


class RetrainingPipeline:
    """Orchestrate weekly model retraining."""

    def __init__(
        self,
        db_pool: Pool | None = None,
        minio_client: Any = None,
        settings: EvolveSettings | None = None,
    ) -> None:
        self._pool = db_pool
        self._minio = minio_client
        self._cfg = settings or EvolveSettings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_weekly_retrain(
        self,
        triggered_by: str = TRIGGER_MANUAL,
    ) -> RetrainRun:
        """
        Execute the full retraining pipeline.

        Returns a ``RetrainRun`` audit record regardless of outcome.
        """
        run_id = RetrainRun().run_id
        started_at = datetime.now(UTC)
        champion_before = await self._get_champion_id()

        _log.info(
            "evolve.retrain_started",
            run_id=run_id,
            triggered_by=triggered_by,
        )

        await self._upsert_retrain_audit(
            run_id=run_id,
            triggered_by=triggered_by,
            status=STATUS_RUNNING,
            started_at=started_at,
        )

        try:
            # 1. Fetch outcomes
            recorder = OutcomeRecorder(self._pool)  # type: ignore[arg-type]
            outcomes = await recorder.fetch_recent_outcomes(
                days_back=self._cfg.outcomes_lookback_days,
                limit=10_000,
            )

            if len(outcomes) < self._cfg.min_outcomes_for_retrain:
                _log.warning(
                    "evolve.insufficient_outcomes",
                    count=len(outcomes),
                    min_required=self._cfg.min_outcomes_for_retrain,
                    error_code=ERR_INSUFFICIENT_OUTCOMES,
                )
                run = RetrainRun(
                    run_id=run_id,
                    triggered_by=triggered_by,
                    outcomes_count=len(outcomes),
                    champion_before=champion_before,
                    status=STATUS_NO_IMPROVEMENT,
                    error_message=f"Only {len(outcomes)} outcomes (min {self._cfg.min_outcomes_for_retrain})",
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                )
                await self._upsert_retrain_audit(run_id=run_id, status=STATUS_NO_IMPROVEMENT,
                                                 finished_at=run.finished_at)
                return run

            # 2. Pre-process
            x_train, x_val, x_test, y_train, y_val, y_test = self._preprocess_outcomes(outcomes)

            # FIX-3 (forensic audit): capture the training feature distribution
            # so it can be stored on the champion and used as the *real* drift
            # baseline (replaces DriftDetector's synthetic zeros/ones).
            training_feature_mean = x_train.mean(axis=0).tolist() if x_train.shape[0] else []
            training_feature_std = x_train.std(axis=0).tolist() if x_train.shape[0] else []

            # 3. Train candidates
            candidates: list[ModelCandidate] = []
            for arch in SUPPORTED_ARCHITECTURES:
                candidate = await self._train_candidate(
                    arch, x_train, y_train, x_val, y_val, x_test, y_test
                )
                if candidate:
                    candidates.append(candidate)

            if not candidates:
                _log.error(
                    "evolve.all_candidates_failed",
                    error_code=ERR_TRAINING_FAILED,
                )
                run = RetrainRun(
                    run_id=run_id,
                    triggered_by=triggered_by,
                    outcomes_count=len(outcomes),
                    champion_before=champion_before,
                    status=STATUS_FAILED,
                    error_message="All candidate architectures failed to train",
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                )
                await self._upsert_retrain_audit(run_id=run_id, status=STATUS_FAILED,
                                                 finished_at=run.finished_at)
                return run

            # 4. Pick best candidate
            best_candidate = max(candidates, key=lambda c: c.test_auc)
            champion_auc = await self.get_champion_auc()
            improvement_pct = (
                (best_candidate.test_auc - champion_auc) / max(champion_auc, 0.01) * 100
            )

            # Pass 9C + 9E: production-grade monitoring. Evidently drift report +
            # MLflow experiment tracking. Both degrade gracefully to no-ops when the
            # optional libraries are absent, so the retrain path is unaffected.
            await self._run_evidently_report(outcomes)
            self._track_experiment(run_id, best_candidate, champion_auc)

            # 5. PASS3-3C: improved candidates enter a shadow evaluation window
            #    instead of being promoted immediately. fast_promote
            #    (AEGIS_EVOLVE_FAST_PROMOTE=true) keeps the old direct path.
            champion_after: str | None = champion_before
            shadow_deployed = False
            if best_candidate.test_auc > champion_auc + self._cfg.auc_improvement_threshold:
                if self._cfg.fast_promote:
                    champion_after = await self._promote_candidate(
                        best_candidate,
                        training_feature_mean=training_feature_mean,
                        training_feature_std=training_feature_std,
                    )
                    _log.info(
                        "evolve.model_promoted",
                        candidate_id=best_candidate.candidate_id,
                        new_auc=round(best_candidate.test_auc, 4),
                        prev_auc=round(champion_auc, 4),
                        improvement_pct=round(improvement_pct, 2),
                        fast_promote=True,
                    )
                else:
                    shadow_deployed = await self._register_shadow(best_candidate)
            else:
                _log.warning(
                    "evolve.no_improvement",
                    best_auc=round(best_candidate.test_auc, 4),
                    champion_auc=round(champion_auc, 4),
                    gap=round(best_candidate.test_auc - champion_auc, 4),
                    threshold=self._cfg.auc_improvement_threshold,
                )

            if champion_after != champion_before:
                status = STATUS_COMPLETED
            elif shadow_deployed:
                status = STATUS_SHADOW_DEPLOYED
            else:
                status = STATUS_NO_IMPROVEMENT
            run = RetrainRun(
                run_id=run_id,
                triggered_by=triggered_by,
                outcomes_count=len(outcomes),
                candidates=[c.model_dump(mode="json") for c in candidates],
                champion_before=champion_before,
                champion_after=champion_after,
                improvement_pct=(
                    improvement_pct
                    if status in (STATUS_COMPLETED, STATUS_SHADOW_DEPLOYED)
                    else None
                ),
                status=status,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
            await self._upsert_retrain_audit(
                run_id=run_id,
                status=status,
                candidates=run.candidates,
                champion_after=champion_after,
                improvement_pct=run.improvement_pct,
                finished_at=run.finished_at,
            )
            return run

        except Exception as exc:
            _log.error(
                "evolve.retrain_failed",
                error=str(exc),
                error_code=ERR_RETRAIN_GENERAL,
                run_id=run_id,
            )
            run = RetrainRun(
                run_id=run_id,
                triggered_by=triggered_by,
                champion_before=champion_before,
                status=STATUS_FAILED,
                error_message=str(exc),
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
            await self._upsert_retrain_audit(run_id=run_id, status=STATUS_FAILED,
                                             error_message=str(exc),
                                             finished_at=run.finished_at)
            return run

    async def _run_evidently_report(self, outcomes: list[TradeOutcome]) -> None:
        """Pass 9C: run an Evidently drift report (best-effort, non-fatal)."""
        try:
            import pandas as pd

            from aegis.evolve.evidently_monitor import EvidentlyMonitor

            n = len(outcomes)
            if n < 4:
                return
            mid = n // 2
            rows = [{"roi": float(getattr(o, "roi", 0.0))} for o in outcomes]
            reference_df = pd.DataFrame(rows[:mid])
            current_df = pd.DataFrame(rows[mid:])
            result = await EvidentlyMonitor().run_drift_report(reference_df, current_df)
            if result.get("drift_detected"):
                _log.info(
                    "evolve.evidently_drift_detected",
                    drifted_features=result.get("drifted_features"),
                    report_path=result.get("report_path"),
                )
        except Exception as exc:
            _log.debug("evolve.evidently_report_skipped", error=str(exc))

    def _track_experiment(
        self, run_id: str, candidate: ModelCandidate, champion_auc: float
    ) -> None:
        """Pass 9E: log the retrain run to MLflow (best-effort, non-fatal)."""
        try:
            from aegis.evolve.experiment_tracker import ExperimentTracker

            tracker = ExperimentTracker()
            with tracker.start_run(run_name=f"weekly_{run_id}"):
                tracker.log_params({"architecture": candidate.architecture})
                tracker.log_metrics({
                    "candidate_auc": float(candidate.test_auc),
                    "champion_auc": float(champion_auc),
                })
        except Exception as exc:
            _log.debug("evolve.experiment_tracking_skipped", error=str(exc))

    async def get_champion_auc(self) -> float:
        """Return current champion model test AUC, or DEFAULT_CHAMPION_AUC if none."""
        if self._pool is None:
            return DEFAULT_CHAMPION_AUC
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT test_auc FROM model_candidates WHERE is_champion = TRUE LIMIT 1"
                )
            if row:
                return float(row["test_auc"])
        except Exception as exc:
            _log.debug("evolve.champion_auc_fetch_failed", error=str(exc))
        return DEFAULT_CHAMPION_AUC

    async def rollback_champion(self) -> bool:
        """
        Rollback to the previous champion model.

        Promotes the most recent non-champion model back to champion.
        Returns True on success.
        """
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                prev = await conn.fetchrow(
                    """
                    SELECT candidate_id FROM model_candidates
                    WHERE is_champion = FALSE
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                )
                if not prev:
                    _log.warning("evolve.rollback_no_prev_model")
                    return False

                await conn.execute(
                    "UPDATE model_candidates SET is_champion = FALSE WHERE is_champion = TRUE"
                )
                await conn.execute(
                    "UPDATE model_candidates SET is_champion = TRUE, promoted_at = NOW() WHERE candidate_id = $1",
                    prev["candidate_id"],
                )

            _log.info("evolve.rollback_complete", candidate_id=prev["candidate_id"])
            return True
        except Exception as exc:
            _log.error("evolve.rollback_failed", error=str(exc))
            return False

    async def fetch_recent_runs(self, limit: int = 10) -> list[RetrainRun]:
        """Fetch the most recent retrain audit records."""
        if self._pool is None:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT run_id, triggered_by, outcomes_count, candidates,
                           champion_before, champion_after, improvement_pct,
                           status, error_message, started_at, finished_at
                    FROM retrain_audit
                    ORDER BY started_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
            result = []
            for row in rows:
                result.append(RetrainRun(
                    run_id=str(row["run_id"]),
                    triggered_by=row["triggered_by"],
                    outcomes_count=row["outcomes_count"],
                    candidates=json.loads(row["candidates"] or "[]"),
                    champion_before=row["champion_before"],
                    champion_after=row["champion_after"],
                    improvement_pct=row["improvement_pct"],
                    status=row["status"],
                    error_message=row["error_message"],
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                ))
            return result
        except Exception as exc:
            _log.error("evolve.fetch_runs_failed", error=str(exc))
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _train_candidate(
        self,
        architecture: str,
        x_train: np.ndarray,
        y_train: np.ndarray,
        x_val: np.ndarray,
        y_val: np.ndarray,
        x_test: np.ndarray,
        y_test: np.ndarray,
    ) -> ModelCandidate | None:
        """Train a single model candidate with HPO; return None on failure."""
        t0 = time.monotonic()
        try:
            # Hyperparameter search
            best_hparams = await optimize_hyperparameters(
                x_train, y_train, x_val, y_val,
                architecture=architecture,
                n_trials=self._cfg.hpo_n_trials,
            )

            # Train and evaluate
            metrics = self._train_and_evaluate(
                architecture, x_train, y_train, x_val, y_val, x_test, y_test, best_hparams
            )
            duration = time.monotonic() - t0

            # Persist artifact
            artifact_path = await self._save_model_artifact(architecture, best_hparams)

            candidate = ModelCandidate(
                candidate_id=f"{architecture}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}",
                architecture=architecture,
                train_auc=metrics.get("train_auc", 0.5),
                val_auc=metrics.get("val_auc", 0.5),
                test_auc=metrics.get("test_auc", 0.5),
                test_precision=metrics.get("test_precision", 0.5),
                test_recall=metrics.get("test_recall", 0.5),
                test_f1=metrics.get("test_f1", 0.5),
                training_duration_s=duration,
                artifact_path=artifact_path,
                hyperparameters=best_hparams,
            )

            _log.info(
                "evolve.candidate_trained",
                candidate_id=candidate.candidate_id,
                test_auc=round(candidate.test_auc, 4),
                duration_s=round(duration, 1),
            )
            return candidate

        except Exception as exc:
            _log.error(
                "evolve.candidate_failed",
                architecture=architecture,
                error=str(exc),
            )
            return None

    @staticmethod
    def _train_and_evaluate(
        architecture: str,
        x_train: np.ndarray,
        y_train: np.ndarray,
        x_val: np.ndarray,
        y_val: np.ndarray,
        x_test: np.ndarray,
        y_test: np.ndarray,
        hparams: dict[str, Any],
    ) -> dict[str, float]:
        """
        Train model and compute AUC/precision/recall/F1 on test set.

        Uses logistic regression as the training proxy (same as HPO).
        In production this would delegate to aegis.predict.models.
        """
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.metrics import (
                f1_score,
                precision_score,
                recall_score,
                roc_auc_score,
            )
            from sklearn.preprocessing import StandardScaler

            scaler = StandardScaler()
            x_tr = scaler.fit_transform(x_train)
            x_vl = scaler.transform(x_val)
            x_ts = scaler.transform(x_test)

            clf = LogisticRegression(max_iter=300, random_state=42)
            clf.fit(x_tr, y_train)

            def _auc(x_arr: np.ndarray, y: np.ndarray) -> float:
                proba = clf.predict_proba(x_arr)[:, 1]
                return float(roc_auc_score(y, proba)) if len(np.unique(y)) > 1 else 0.5

            y_pred = clf.predict(x_ts)
            return {
                "train_auc": _auc(x_tr, y_train),
                "val_auc": _auc(x_vl, y_val),
                "test_auc": _auc(x_ts, y_test),
                "test_precision": float(precision_score(y_test, y_pred, zero_division=0)),
                "test_recall": float(recall_score(y_test, y_pred, zero_division=0)),
                "test_f1": float(f1_score(y_test, y_pred, zero_division=0)),
            }

        except Exception as exc:
            # OMEGA reality-first (FIX-5): never fabricate metrics on training
            # failure. Synthetic AUC could promote a junk champion through the
            # gate. Raise instead — the caller (_train_candidate) drops the
            # candidate (returns None), so a failed training produces NO
            # candidate rather than a fake one.
            raise TrainingFailedError(
                f"training proxy failed for {architecture}: {exc}"
            ) from exc

    async def _save_model_artifact(self, architecture: str, hparams: dict[str, Any]) -> str:
        """Compute a content-addressable path; upload a placeholder to MinIO."""
        payload = json.dumps({"architecture": architecture, "hparams": hparams}, sort_keys=True)
        content_hash = hashlib.sha256(payload.encode()).hexdigest()
        key = f"models/{architecture}/{content_hash}.json"

        if self._minio is not None:
            try:
                import io
                data = payload.encode()
                self._minio.put_object(
                    bucket_name=self._cfg.model_bucket,
                    object_name=key,
                    data=io.BytesIO(data),
                    length=len(data),
                    content_type="application/json",
                )
            except Exception as exc:
                _log.warning("evolve.artifact_upload_failed", key=key, error=str(exc))

        return key

    async def _promote_candidate(
        self,
        candidate: ModelCandidate,
        training_feature_mean: list[float] | None = None,
        training_feature_std: list[float] | None = None,
    ) -> str | None:
        """Write candidate to model_candidates as the new champion.

        FIX-3 (forensic audit): the training feature distribution
        (``training_feature_mean`` / ``training_feature_std``) is persisted here
        so ``DriftDetector`` can compare live features against the real
        distribution the champion was trained on.
        """
        if self._pool is None:
            return candidate.candidate_id
        mean_json = json.dumps(training_feature_mean) if training_feature_mean else None
        std_json = json.dumps(training_feature_std) if training_feature_std else None
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE model_candidates SET is_champion = FALSE WHERE is_champion = TRUE"
                )
                await conn.execute(
                    """
                    INSERT INTO model_candidates
                        (candidate_id, architecture, train_auc, val_auc, test_auc,
                         test_precision, test_recall, test_f1,
                         training_duration_s, artifact_path, hyperparameters,
                         training_feature_mean, training_feature_std,
                         is_champion, promoted_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,
                            $12::jsonb,$13::jsonb,TRUE,NOW())
                    ON CONFLICT (candidate_id) DO UPDATE SET
                        is_champion = TRUE, promoted_at = NOW(),
                        training_feature_mean = COALESCE(
                            EXCLUDED.training_feature_mean, model_candidates.training_feature_mean),
                        training_feature_std = COALESCE(
                            EXCLUDED.training_feature_std, model_candidates.training_feature_std)
                    """,
                    candidate.candidate_id,
                    candidate.architecture,
                    candidate.train_auc,
                    candidate.val_auc,
                    candidate.test_auc,
                    candidate.test_precision,
                    candidate.test_recall,
                    candidate.test_f1,
                    candidate.training_duration_s,
                    candidate.artifact_path,
                    json.dumps(candidate.hyperparameters),
                    mean_json,
                    std_json,
                )
            return candidate.candidate_id
        except Exception as exc:
            _log.error("evolve.promotion_failed", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # PASS3-3C: shadow deployment
    # ------------------------------------------------------------------

    async def _register_shadow(self, candidate: ModelCandidate) -> bool:
        """
        Register *candidate* as a shadow model for parallel evaluation.

        The shadow scores predictions without affecting alerts; after
        ``shadow_period_hours`` (default 72) the 6-hourly
        ``evaluate_shadows()`` job decides promotion vs retirement.

        Returns True when the shadow row was written and the event published.
        """
        if self._pool is None:
            _log.debug(
                "evolve.shadow_register_skipped",
                reason="no_db_pool",
                candidate_id=candidate.candidate_id,
            )
            return False

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO model_candidates
                        (candidate_id, architecture, train_auc, val_auc, test_auc,
                         test_precision, test_recall, test_f1,
                         training_duration_s, artifact_path, hyperparameters,
                         is_champion, is_shadow, shadow_registered_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,FALSE,TRUE,NOW())
                    ON CONFLICT (candidate_id) DO UPDATE SET
                        is_shadow = TRUE, shadow_registered_at = NOW()
                    """,
                    candidate.candidate_id,
                    candidate.architecture,
                    candidate.train_auc,
                    candidate.val_auc,
                    candidate.test_auc,
                    candidate.test_precision,
                    candidate.test_recall,
                    candidate.test_f1,
                    candidate.training_duration_s,
                    candidate.artifact_path,
                    json.dumps(candidate.hyperparameters),
                )
        except Exception as exc:
            _log.error(
                "evolve.shadow_register_failed",
                candidate_id=candidate.candidate_id,
                error=str(exc),
                error_code=ERR_SHADOW_REGISTER_FAILED,
            )
            return False

        shadow_until = datetime.now(UTC) + timedelta(hours=self._cfg.shadow_period_hours)
        await self._publish_evolve_event(
            {
                "event": "shadow_deployment_started",
                "candidate_id": candidate.candidate_id,
                "candidate_auc": float(candidate.test_auc),
                "shadow_until": shadow_until.isoformat(),
            }
        )
        _log.info(
            "evolve.shadow_deployment_started",
            candidate_id=candidate.candidate_id,
            candidate_auc=round(candidate.test_auc, 4),
            shadow_until=shadow_until.isoformat(),
        )
        return True

    async def evaluate_shadows(self) -> list[dict[str, Any]]:
        """
        Promote or retire shadows whose evaluation window has expired.

        For each ``model_candidates`` row with ``is_shadow = TRUE`` older than
        ``shadow_period_hours``: promote it to champion when its test AUC still
        beats the current champion by ``auc_improvement_threshold``; otherwise
        retire it (clear the shadow flag, keep the row for audit).

        Returns one result dict per evaluated shadow. Never raises.
        """
        if self._pool is None:
            return []

        results: list[dict[str, Any]] = []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT candidate_id, test_auc
                    FROM model_candidates
                    WHERE is_shadow = TRUE
                      AND shadow_registered_at < NOW() - make_interval(hours => $1)
                    ORDER BY shadow_registered_at ASC
                    """,
                    self._cfg.shadow_period_hours,
                )
        except Exception as exc:
            _log.error("evolve.shadow_fetch_failed", error=str(exc))
            return results

        if not rows:
            return results

        champion_auc = await self.get_champion_auc()
        for row in rows:
            candidate_id = str(row["candidate_id"])
            shadow_auc = float(row["test_auc"])
            promoted = False
            if shadow_auc > champion_auc + self._cfg.auc_improvement_threshold:
                promoted = await self._promote_shadow(candidate_id)
            else:
                await self._retire_shadow(candidate_id)

            _log.info(
                "evolve.shadow_evaluated",
                candidate_id=candidate_id,
                shadow_auc=round(shadow_auc, 4),
                champion_auc=round(champion_auc, 4),
                promoted=promoted,
            )
            await self._publish_evolve_event(
                {
                    "event": "shadow_evaluated",
                    "candidate_id": candidate_id,
                    "shadow_auc": shadow_auc,
                    "champion_auc": champion_auc,
                    "promoted": promoted,
                }
            )
            results.append(
                {
                    "candidate_id": candidate_id,
                    "shadow_auc": shadow_auc,
                    "champion_auc": champion_auc,
                    "promoted": promoted,
                }
            )
        return results

    async def _promote_shadow(self, candidate_id: str) -> bool:
        """Promote a shadow row to champion; clears its shadow flag."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE model_candidates SET is_champion = FALSE WHERE is_champion = TRUE"
                )
                await conn.execute(
                    """
                    UPDATE model_candidates
                    SET is_champion = TRUE, is_shadow = FALSE, promoted_at = NOW()
                    WHERE candidate_id = $1
                    """,
                    candidate_id,
                )
            return True
        except Exception as exc:
            _log.error(
                "evolve.shadow_promotion_failed",
                candidate_id=candidate_id,
                error=str(exc),
            )
            return False

    async def _retire_shadow(self, candidate_id: str) -> None:
        """Clear the shadow flag without promotion (row kept for audit)."""
        if self._pool is None:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE model_candidates SET is_shadow = FALSE WHERE candidate_id = $1",
                    candidate_id,
                )
        except Exception as exc:
            _log.error(
                "evolve.shadow_retire_failed",
                candidate_id=candidate_id,
                error=str(exc),
            )

    @staticmethod
    async def _publish_evolve_event(payload: dict[str, Any]) -> None:
        """Best-effort publish to the evolve event stream."""
        try:
            from aegis.core.event_bus import STREAM_EVOLVE, publish_event

            await publish_event(STREAM_EVOLVE, payload)
        except Exception as exc:
            _log.debug("evolve.event_publish_failed", error=str(exc))

    async def _get_champion_id(self) -> str | None:
        if self._pool is None:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT candidate_id FROM model_candidates WHERE is_champion = TRUE LIMIT 1"
                )
            return str(row["candidate_id"]) if row else None
        except Exception:
            return None

    async def _upsert_retrain_audit(self, run_id: str, **kwargs: Any) -> None:
        if self._pool is None:
            return
        try:
            existing_keys = {
                "triggered_by", "status", "outcomes_count", "candidates",
                "champion_before", "champion_after", "improvement_pct",
                "error_message", "started_at", "finished_at",
            }
            data = {k: v for k, v in kwargs.items() if k in existing_keys}

            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO retrain_audit (run_id, triggered_by, status, started_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (run_id) DO UPDATE SET
                        status          = COALESCE(EXCLUDED.status, retrain_audit.status),
                        outcomes_count  = COALESCE($5, retrain_audit.outcomes_count),
                        candidates      = COALESCE($6::jsonb, retrain_audit.candidates),
                        champion_after  = COALESCE($7, retrain_audit.champion_after),
                        improvement_pct = COALESCE($8, retrain_audit.improvement_pct),
                        error_message   = COALESCE($9, retrain_audit.error_message),
                        finished_at     = COALESCE($10, retrain_audit.finished_at)
                    """,
                    run_id,
                    data.get("triggered_by", TRIGGER_MANUAL),
                    data.get("status", STATUS_RUNNING),
                    data.get("started_at", datetime.now(UTC)),
                    data.get("outcomes_count"),
                    json.dumps(data.get("candidates")) if data.get("candidates") else None,
                    data.get("champion_after"),
                    data.get("improvement_pct"),
                    data.get("error_message"),
                    data.get("finished_at"),
                )
        except Exception as exc:
            _log.debug("evolve.audit_upsert_failed", error=str(exc))

    # FIX-1 (forensic audit): canonical ordered list of PRE-TRADE feature keys
    # that may appear in ``TradeOutcome.market_conditions_at_exec``. These are the
    # signals known *before* the trade was entered. Any post-trade field
    # (actual_roi_pct, pnl_usd, units_sold, days_to_fulfillment, resolution_*)
    # is FORBIDDEN here — feeding outcomes as features is target leakage.
    _PRETRADE_SNAPSHOT_KEYS: tuple[str, ...] = (
        "signal_count",
        "unique_authors",
        "author_diversity",
        "velocity_1h",
        "velocity_6h",
        "velocity_24h",
        "platform_count",
        "sentiment",
        "commercial_intent",
        "novelty",
        "coordination_risk",
        "breakout_prob",
    )

    # Fields that are realized AFTER trade entry and must NEVER be used as
    # training features. Kept as an explicit blocklist for the leakage detector
    # and for documentation/audit purposes.
    _LEAKED_FIELDS: frozenset[str] = frozenset({
        "actual_roi_pct",
        "pnl_usd",
        "units_sold",
        "units_returned",
        "avg_sale_price",
        "total_cost",
        "shipping_cost",
        "platform_fee",
        "days_to_fulfillment",
        "resolution_status",
        "resolution_notes",
    })

    @staticmethod
    def _preprocess_outcomes(outcomes: list[TradeOutcome]) -> tuple[
        np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray
    ]:
        """
        Convert TradeOutcome list → (x_train, x_val, x_test, y_train, y_val, y_test).

        FIX-1 (forensic audit — target leakage removed):
        The feature vector is built EXCLUSIVELY from information available at
        trade-entry time:

            X[0] = prediction_score        (our confidence at entry)
            X[1] = prediction_confidence   (calibrated confidence at entry)
            X[2:] = pre-trade market snapshot from ``market_conditions_at_exec``
                    (velocity, signal_count, author_diversity, sentiment, …),
                    in the fixed order of ``_PRETRADE_SNAPSHOT_KEYS``, padded to
                    EVOLVE_FEATURE_DIM.

        The post-outcome fields ``actual_roi_pct``, ``pnl_usd``, ``units_sold``,
        and ``days_to_fulfillment`` are DELIBERATELY EXCLUDED — they are the very
        results being predicted, and using them as inputs guarantees an inflated,
        statistically meaningless AUC.

        Label: 1 if resolution_status == 'successful', else 0.
        Split: 70 / 15 / 15 (chronological — outcomes arrive newest-first from
        the recorder, so this is a strict time-based holdout, no shuffling).
        """
        from aegis.evolve.constants import EVOLVE_FEATURE_DIM

        n = len(outcomes)
        X = np.zeros((n, EVOLVE_FEATURE_DIM))
        y = np.zeros(n, dtype=int)

        snapshot_keys = RetrainingPipeline._PRETRADE_SNAPSHOT_KEYS
        # Reserve columns 0 and 1 for score/confidence; snapshot follows.
        max_snapshot = EVOLVE_FEATURE_DIM - 2

        for i, o in enumerate(outcomes):
            X[i, 0] = float(o.prediction_score)
            X[i, 1] = float(o.prediction_confidence)
            snapshot = o.market_conditions_at_exec or {}
            for j, key in enumerate(snapshot_keys[:max_snapshot]):
                raw = snapshot.get(key)
                try:
                    X[i, 2 + j] = float(raw) if raw is not None else 0.0
                except (TypeError, ValueError):
                    X[i, 2 + j] = 0.0
            y[i] = 1 if o.resolution_status == "successful" else 0

        n_train = max(1, int(0.70 * n))
        n_val = max(1, int(0.15 * n))

        X_train = X[:n_train]
        X_val = X[n_train: n_train + n_val]
        X_test = X[n_train + n_val:]
        y_train = y[:n_train]
        y_val = y[n_train: n_train + n_val]
        y_test = y[n_train + n_val:]

        return X_train, X_val, X_test, y_train, y_val, y_test
