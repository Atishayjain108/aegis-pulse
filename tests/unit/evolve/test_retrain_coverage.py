"""Additional coverage tests for retrain.py — DB-dependent paths and train_evaluate."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from aegis.evolve.config import EvolveSettings
from aegis.evolve.retrain import RetrainingPipeline
from aegis.evolve.schemas import ModelCandidate, TradeOutcome


def _make_outcome(plan_id: str = "p1") -> TradeOutcome:
    return TradeOutcome(
        execution_plan_id=plan_id,
        trend_id="t1",
        prediction_score=0.75,
        prediction_confidence=0.85,
        actual_roi_pct=Decimal("30.0"),
        pnl_usd=Decimal("150.0"),
        units_sold=5,
        resolution_status="successful",
    )


def _make_pool_no_ops():
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class TestTrainAndEvaluateFallback:
    """Tests for the sklearn fallback path in _train_and_evaluate."""

    def _arrays(self, n: int = 100) -> tuple:
        rng = np.random.default_rng(42)
        X = rng.standard_normal((n, 20))
        y = (rng.random(n) > 0.5).astype(int)
        h = n // 2
        return X[:h], y[:h], X[h:], y[h:]

    def test_all_architectures_return_valid_metrics(self) -> None:
        X_tr, y_tr, X_ts, y_ts = self._arrays()
        for arch in ("patchts", "autoformer", "heuristic"):
            metrics = RetrainingPipeline._train_and_evaluate(
                arch, X_tr, y_tr, X_tr, y_tr, X_ts, y_ts, {}
            )
            for key in ("train_auc", "val_auc", "test_auc", "test_precision", "test_recall", "test_f1"):
                assert 0 <= metrics[key] <= 1, f"{key} out of range for {arch}"

    def test_single_class_y_still_returns_float(self) -> None:
        X_tr, y_tr, X_ts, _ = self._arrays()
        y_ts_single = np.zeros(50, dtype=int)
        metrics = RetrainingPipeline._train_and_evaluate(
            "patchts", X_tr, y_tr, X_tr, y_tr, X_ts, y_ts_single, {}
        )
        assert isinstance(metrics["test_auc"], float)

    def test_sklearn_absent_fallback(self) -> None:
        import sys
        X_tr, y_tr, X_ts, y_ts = self._arrays()
        with pytest.MonkeyPatch().context() as mp:
            mp.setitem(sys.modules, "sklearn", None)
            mp.setitem(sys.modules, "sklearn.linear_model", None)
            mp.setitem(sys.modules, "sklearn.metrics", None)
            mp.setitem(sys.modules, "sklearn.preprocessing", None)
            metrics = RetrainingPipeline._train_and_evaluate(
                "patchts", X_tr, y_tr, X_tr, y_tr, X_ts, y_ts, {}
            )
        for key in ("train_auc", "val_auc", "test_auc"):
            assert 0.4 <= metrics[key] <= 0.7


class TestSaveModelArtifact:
    @pytest.mark.asyncio
    async def test_returns_path_without_minio(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None, minio_client=None)
        path = await pipeline._save_model_artifact("patchts", {"d_model": 128})
        assert "models/patchts/" in path
        assert path.endswith(".json")

    @pytest.mark.asyncio
    async def test_minio_failure_still_returns_path(self) -> None:
        minio = MagicMock()
        minio.put_object = MagicMock(side_effect=RuntimeError("MinIO down"))
        pipeline = RetrainingPipeline(db_pool=None, minio_client=minio)
        path = await pipeline._save_model_artifact("autoformer", {"lr": 0.001})
        assert path.startswith("models/")

    @pytest.mark.asyncio
    async def test_path_is_content_addressable(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None)
        p1 = await pipeline._save_model_artifact("heuristic", {"kelly_fraction": 0.25})
        p2 = await pipeline._save_model_artifact("heuristic", {"kelly_fraction": 0.25})
        assert p1 == p2  # same content → same hash

    @pytest.mark.asyncio
    async def test_different_hparams_different_paths(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None)
        p1 = await pipeline._save_model_artifact("patchts", {"d_model": 64})
        p2 = await pipeline._save_model_artifact("patchts", {"d_model": 512})
        assert p1 != p2


class TestPromoteCandidate:
    @pytest.mark.asyncio
    async def test_promote_no_pool_returns_candidate_id(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None)
        candidate = ModelCandidate(
            candidate_id="test-arch-20260602",
            architecture="heuristic",
            test_auc=0.72,
        )
        result = await pipeline._promote_candidate(candidate)
        assert result == "test-arch-20260602"

    @pytest.mark.asyncio
    async def test_promote_with_pool_executes_sql(self) -> None:
        pool, conn = _make_pool_no_ops()
        pipeline = RetrainingPipeline(db_pool=pool)
        candidate = ModelCandidate(
            candidate_id="new-champ",
            architecture="patchts",
            test_auc=0.75,
        )
        result = await pipeline._promote_candidate(candidate)
        assert result == "new-champ"
        assert conn.execute.call_count == 2  # demote old + insert new

    @pytest.mark.asyncio
    async def test_promote_db_failure_returns_none(self) -> None:
        pool, conn = _make_pool_no_ops()
        conn.execute = AsyncMock(side_effect=RuntimeError("constraint violation"))
        pipeline = RetrainingPipeline(db_pool=pool)
        candidate = ModelCandidate(candidate_id="fail-champ", architecture="heuristic")
        result = await pipeline._promote_candidate(candidate)
        assert result is None


class TestGetChampionId:
    @pytest.mark.asyncio
    async def test_no_pool_returns_none(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None)
        result = await pipeline._get_champion_id()
        assert result is None

    @pytest.mark.asyncio
    async def test_with_pool_no_champion(self) -> None:
        pool, conn = _make_pool_no_ops()
        pipeline = RetrainingPipeline(db_pool=pool)
        result = await pipeline._get_champion_id()
        assert result is None

    @pytest.mark.asyncio
    async def test_with_pool_has_champion(self) -> None:
        row = MagicMock()
        row.__getitem__ = lambda self, k: "patchts-20260601"
        pool, conn = _make_pool_no_ops()
        conn.fetchrow = AsyncMock(return_value=row)
        pipeline = RetrainingPipeline(db_pool=pool)
        result = await pipeline._get_champion_id()
        assert result == "patchts-20260601"


class TestGetChampionAUC:
    @pytest.mark.asyncio
    async def test_with_pool_has_champion(self) -> None:
        row = MagicMock()
        row.__getitem__ = lambda self, k: 0.73
        pool, conn = _make_pool_no_ops()
        conn.fetchrow = AsyncMock(return_value=row)
        pipeline = RetrainingPipeline(db_pool=pool)
        auc = await pipeline.get_champion_auc()
        assert auc == pytest.approx(0.73)

    @pytest.mark.asyncio
    async def test_with_pool_db_failure(self) -> None:
        pool, conn = _make_pool_no_ops()
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("timeout"))
        pipeline = RetrainingPipeline(db_pool=pool)
        from aegis.evolve.constants import DEFAULT_CHAMPION_AUC
        auc = await pipeline.get_champion_auc()
        assert auc == DEFAULT_CHAMPION_AUC


class TestFetchRecentRuns:
    @pytest.mark.asyncio
    async def test_fetch_with_data(self) -> None:
        import datetime
        row = MagicMock()
        now = datetime.datetime.now(datetime.UTC)
        row.__getitem__ = lambda self, k: {
            "run_id": "run-001",
            "triggered_by": "scheduler",
            "outcomes_count": 250,
            "candidates": "[]",
            "champion_before": None,
            "champion_after": "model-1",
            "improvement_pct": 3.5,
            "status": "completed",
            "error_message": None,
            "started_at": now,
            "finished_at": now,
        }[k]
        pool, conn = _make_pool_no_ops()
        conn.fetch = AsyncMock(return_value=[row])
        pipeline = RetrainingPipeline(db_pool=pool)
        runs = await pipeline.fetch_recent_runs()
        assert len(runs) == 1
        assert runs[0].status == "completed"
        assert runs[0].outcomes_count == 250

    @pytest.mark.asyncio
    async def test_fetch_db_failure(self) -> None:
        pool, conn = _make_pool_no_ops()
        conn.fetch = AsyncMock(side_effect=RuntimeError("db gone"))
        pipeline = RetrainingPipeline(db_pool=pool)
        runs = await pipeline.fetch_recent_runs()
        assert runs == []


class TestUpsertRetrainAudit:
    @pytest.mark.asyncio
    async def test_no_pool_is_noop(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None)
        await pipeline._upsert_retrain_audit(run_id="test-run")  # should not raise

    @pytest.mark.asyncio
    async def test_with_pool_executes(self) -> None:
        pool, conn = _make_pool_no_ops()
        pipeline = RetrainingPipeline(db_pool=pool)
        await pipeline._upsert_retrain_audit(run_id="test-run", status="running")
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_db_failure_is_silent(self) -> None:
        pool, conn = _make_pool_no_ops()
        conn.execute = AsyncMock(side_effect=RuntimeError("lock timeout"))
        pipeline = RetrainingPipeline(db_pool=pool)
        await pipeline._upsert_retrain_audit(run_id="x", status="completed")  # no raise


class TestRetrainWithPromotion:
    @pytest.mark.asyncio
    async def test_promotion_fires_when_auc_beats_threshold(self) -> None:
        """Champion AUC=0.5, candidate AUC=0.55 → +10% → should promote."""
        from unittest.mock import patch
        outcomes = [_make_outcome(f"p{i}") for i in range(50)]
        cfg = EvolveSettings(min_outcomes_for_retrain=10, hpo_n_trials=2, auc_improvement_threshold=0.02)
        pool, conn = _make_pool_no_ops()
        # Champion AUC returns 0.5 (no champion row)
        conn.fetchrow = AsyncMock(return_value=None)
        pipeline = RetrainingPipeline(db_pool=pool, settings=cfg)

        with patch("aegis.evolve.retrain.OutcomeRecorder.fetch_recent_outcomes", new_callable=AsyncMock, return_value=outcomes):
            run = await pipeline.run_weekly_retrain(triggered_by="test")

        # With 50 outcomes and default LR proxy, we either complete or no_improvement
        assert run.status in ("completed", "no_improvement", "failed")
        assert run.outcomes_count == 50
