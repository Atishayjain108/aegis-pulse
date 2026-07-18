"""Unit tests for RetrainingPipeline."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from aegis.evolve.config import EvolveSettings
from aegis.evolve.constants import (
    DEFAULT_CHAMPION_AUC,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NO_IMPROVEMENT,
    STATUS_SHADOW_DEPLOYED,
)
from aegis.evolve.retrain import RetrainingPipeline
from aegis.evolve.schemas import TradeOutcome


def _make_cfg(**overrides) -> EvolveSettings:
    return EvolveSettings(**overrides)


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


class TestRetrainingPipelineNoPool:
    @pytest.mark.asyncio
    async def test_champion_auc_no_pool_returns_default(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None)
        auc = await pipeline.get_champion_auc()
        assert auc == DEFAULT_CHAMPION_AUC

    @pytest.mark.asyncio
    async def test_fetch_recent_runs_no_pool(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None)
        runs = await pipeline.fetch_recent_runs()
        assert runs == []

    @pytest.mark.asyncio
    async def test_rollback_no_pool(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None)
        ok = await pipeline.rollback_champion()
        assert ok is False

    @pytest.mark.asyncio
    async def test_retrain_insufficient_outcomes(self) -> None:
        cfg = _make_cfg(min_outcomes_for_retrain=100)
        pipeline = RetrainingPipeline(db_pool=None, settings=cfg)

        with patch(
            "aegis.evolve.retrain.OutcomeRecorder.fetch_recent_outcomes",
            new_callable=AsyncMock,
            return_value=[_make_outcome() for _ in range(5)],
        ):
            run = await pipeline.run_weekly_retrain()

        assert run.status == STATUS_NO_IMPROVEMENT
        assert "Only 5 outcomes" in (run.error_message or "")

    @pytest.mark.asyncio
    async def test_retrain_with_enough_outcomes(self) -> None:
        cfg = _make_cfg(min_outcomes_for_retrain=10, hpo_n_trials=2)
        pipeline = RetrainingPipeline(db_pool=None, settings=cfg)
        outcomes = [_make_outcome(f"p{i}") for i in range(50)]

        with patch(
            "aegis.evolve.retrain.OutcomeRecorder.fetch_recent_outcomes",
            new_callable=AsyncMock,
            return_value=outcomes,
        ):
            run = await pipeline.run_weekly_retrain()

        assert run.status in (STATUS_COMPLETED, STATUS_NO_IMPROVEMENT, STATUS_FAILED)
        assert run.outcomes_count == 50

    @pytest.mark.asyncio
    async def test_retrain_exception_returns_failed(self) -> None:
        cfg = _make_cfg(min_outcomes_for_retrain=10)
        pipeline = RetrainingPipeline(db_pool=None, settings=cfg)

        with patch(
            "aegis.evolve.retrain.OutcomeRecorder.fetch_recent_outcomes",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB exploded"),
        ):
            run = await pipeline.run_weekly_retrain()

        assert run.status == STATUS_FAILED
        assert "DB exploded" in (run.error_message or "")


class TestPreprocessOutcomes:
    def test_split_ratios(self) -> None:
        outcomes = [_make_outcome(f"p{i}") for i in range(100)]
        X_tr, X_vl, X_ts, y_tr, y_vl, y_ts = RetrainingPipeline._preprocess_outcomes(outcomes)
        assert X_tr.shape[0] == 70
        assert X_vl.shape[0] == 15
        assert X_ts.shape[0] == 15

    def test_feature_dim(self) -> None:
        from aegis.evolve.constants import EVOLVE_FEATURE_DIM
        outcomes = [_make_outcome(f"p{i}") for i in range(50)]
        X_tr, *_ = RetrainingPipeline._preprocess_outcomes(outcomes)
        assert X_tr.shape[1] == EVOLVE_FEATURE_DIM

    def test_labels_binary(self) -> None:
        outcomes = [_make_outcome(f"p{i}") for i in range(50)]
        _, _, _, y_tr, y_vl, y_ts = RetrainingPipeline._preprocess_outcomes(outcomes)
        for y in (y_tr, y_vl, y_ts):
            assert set(np.unique(y)).issubset({0, 1})

    def test_successful_mapped_to_1(self) -> None:
        outcomes = [_make_outcome() for _ in range(100)]  # all "successful"
        X_tr, X_vl, X_ts, y_tr, y_vl, y_ts = RetrainingPipeline._preprocess_outcomes(outcomes)
        assert y_tr.sum() == 70  # all ones

    def test_failed_mapped_to_0(self) -> None:
        outcomes = []
        for i in range(100):
            o = TradeOutcome(
                execution_plan_id=f"p{i}", trend_id="t",
                prediction_score=0.5, prediction_confidence=0.5,
                resolution_status="full_refund",
            )
            outcomes.append(o)
        _, _, _, y_tr, _, _ = RetrainingPipeline._preprocess_outcomes(outcomes)
        assert y_tr.sum() == 0  # all zeros


class TestTrainAndEvaluate:
    def _make_arrays(self, n: int = 200) -> tuple:
        rng = np.random.default_rng(0)
        X = rng.standard_normal((n, 20))
        y = (rng.random(n) > 0.5).astype(int)
        h = n // 2
        return X[:h], y[:h], X[h:], y[h:]

    def test_metrics_keys(self) -> None:
        X_tr, y_tr, X_ts, y_ts = self._make_arrays()
        metrics = RetrainingPipeline._train_and_evaluate(
            "patchts", X_tr, y_tr, X_tr, y_tr, X_ts, y_ts, {}
        )
        assert set(metrics) >= {"train_auc", "val_auc", "test_auc", "test_precision", "test_recall", "test_f1"}

    def test_auc_in_range(self) -> None:
        X_tr, y_tr, X_ts, y_ts = self._make_arrays()
        metrics = RetrainingPipeline._train_and_evaluate(
            "heuristic", X_tr, y_tr, X_tr, y_tr, X_ts, y_ts, {}
        )
        for key in ("train_auc", "val_auc", "test_auc"):
            assert 0 <= metrics[key] <= 1


class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_no_previous_model(self) -> None:
        pool, conn = _make_pool_no_ops()
        conn.fetchrow = AsyncMock(return_value=None)
        pipeline = RetrainingPipeline(db_pool=pool)
        ok = await pipeline.rollback_champion()
        assert ok is False

    @pytest.mark.asyncio
    async def test_rollback_success(self) -> None:
        pool, conn = _make_pool_no_ops()
        prev_row = MagicMock()
        prev_row.__getitem__ = lambda self, k: "prev-model-id"
        conn.fetchrow = AsyncMock(return_value=prev_row)
        pipeline = RetrainingPipeline(db_pool=pool)
        ok = await pipeline.rollback_champion()
        assert ok is True

    @pytest.mark.asyncio
    async def test_rollback_db_failure(self) -> None:
        pool, conn = _make_pool_no_ops()
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("db fail"))
        pipeline = RetrainingPipeline(db_pool=pool)
        ok = await pipeline.rollback_champion()
        assert ok is False


def _make_candidate(auc: float = 0.9, cid: str = "cand-1"):
    from aegis.evolve.schemas import ModelCandidate

    return ModelCandidate(candidate_id=cid, architecture="heuristic", test_auc=auc)


class TestShadowDeployment:
    """PASS3-3C: improved candidates shadow-deploy instead of instant promote."""

    @pytest.mark.asyncio
    async def test_improved_candidate_registers_shadow_not_promote(self) -> None:
        pool, conn = _make_pool_no_ops()
        cfg = _make_cfg(min_outcomes_for_retrain=10, fast_promote=False)
        pipeline = RetrainingPipeline(db_pool=pool, settings=cfg)
        outcomes = [_make_outcome(f"p{i}") for i in range(50)]

        with (
            patch(
                "aegis.evolve.retrain.OutcomeRecorder.fetch_recent_outcomes",
                new_callable=AsyncMock,
                return_value=outcomes,
            ),
            patch.object(
                pipeline,
                "_train_candidate",
                new_callable=AsyncMock,
                return_value=_make_candidate(auc=0.9),
            ),
            patch("aegis.core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            run = await pipeline.run_weekly_retrain()

        assert run.status == STATUS_SHADOW_DEPLOYED
        # Champion unchanged — only the shadow row was written.
        assert run.champion_after == run.champion_before
        shadow_inserts = [
            c for c in conn.execute.await_args_list if "is_shadow" in str(c.args[0])
        ]
        assert shadow_inserts, "expected an INSERT touching is_shadow"

    @pytest.mark.asyncio
    async def test_fast_promote_skips_shadow(self) -> None:
        pool, _conn = _make_pool_no_ops()
        cfg = _make_cfg(min_outcomes_for_retrain=10, fast_promote=True)
        pipeline = RetrainingPipeline(db_pool=pool, settings=cfg)
        outcomes = [_make_outcome(f"p{i}") for i in range(50)]

        with (
            patch(
                "aegis.evolve.retrain.OutcomeRecorder.fetch_recent_outcomes",
                new_callable=AsyncMock,
                return_value=outcomes,
            ),
            patch.object(
                pipeline,
                "_train_candidate",
                new_callable=AsyncMock,
                return_value=_make_candidate(auc=0.9, cid="cand-fast"),
            ),
            patch("aegis.core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            run = await pipeline.run_weekly_retrain()

        assert run.status == STATUS_COMPLETED
        assert run.champion_after == "cand-fast"

    @pytest.mark.asyncio
    async def test_register_shadow_no_pool_returns_false(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None)
        ok = await pipeline._register_shadow(_make_candidate())
        assert ok is False

    @pytest.mark.asyncio
    async def test_register_shadow_publishes_event(self) -> None:
        pool, _conn = _make_pool_no_ops()
        pipeline = RetrainingPipeline(db_pool=pool)
        with patch(
            "aegis.core.event_bus.publish_event", new_callable=AsyncMock
        ) as publish:
            ok = await pipeline._register_shadow(_make_candidate(auc=0.8))
        assert ok is True
        payload = publish.await_args[0][1]
        assert payload["event"] == "shadow_deployment_started"
        assert payload["candidate_auc"] == pytest.approx(0.8)
        assert "shadow_until" in payload

    @pytest.mark.asyncio
    async def test_register_shadow_db_failure_returns_false(self) -> None:
        pool, conn = _make_pool_no_ops()
        conn.execute = AsyncMock(side_effect=RuntimeError("insert failed"))
        pipeline = RetrainingPipeline(db_pool=pool)
        ok = await pipeline._register_shadow(_make_candidate())
        assert ok is False


class TestEvaluateShadows:
    def _pool_with_shadows(self, shadows, champion_auc: float = 0.5):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=shadows)
        conn.fetchrow = AsyncMock(return_value={"test_auc": champion_auc})
        conn.execute = AsyncMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        return pool, conn

    @pytest.mark.asyncio
    async def test_promotes_winner(self) -> None:
        pool, conn = self._pool_with_shadows(
            [{"candidate_id": "cand-1", "test_auc": 0.9}], champion_auc=0.5
        )
        pipeline = RetrainingPipeline(db_pool=pool)
        with patch("aegis.core.event_bus.publish_event", new_callable=AsyncMock):
            results = await pipeline.evaluate_shadows()
        assert len(results) == 1
        assert results[0]["promoted"] is True
        promote_updates = [
            c for c in conn.execute.await_args_list if "is_champion = TRUE" in str(c.args[0])
        ]
        assert promote_updates, "expected the shadow to be promoted to champion"

    @pytest.mark.asyncio
    async def test_retires_loser(self) -> None:
        pool, conn = self._pool_with_shadows(
            [{"candidate_id": "cand-2", "test_auc": 0.51}], champion_auc=0.5
        )
        pipeline = RetrainingPipeline(db_pool=pool)
        with patch(
            "aegis.core.event_bus.publish_event", new_callable=AsyncMock
        ) as publish:
            results = await pipeline.evaluate_shadows()
        assert results[0]["promoted"] is False
        retire_updates = [
            c for c in conn.execute.await_args_list if "is_shadow = FALSE" in str(c.args[0])
        ]
        assert retire_updates, "expected the shadow to be retired"
        assert publish.await_args[0][1]["event"] == "shadow_evaluated"

    @pytest.mark.asyncio
    async def test_no_pool_returns_empty(self) -> None:
        pipeline = RetrainingPipeline(db_pool=None)
        assert await pipeline.evaluate_shadows() == []

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_empty(self) -> None:
        pool, conn = self._pool_with_shadows([])
        conn.fetch = AsyncMock(side_effect=RuntimeError("db dead"))
        pipeline = RetrainingPipeline(db_pool=pool)
        assert await pipeline.evaluate_shadows() == []

    @pytest.mark.asyncio
    async def test_no_expired_shadows_no_events(self) -> None:
        pool, _conn = self._pool_with_shadows([])
        pipeline = RetrainingPipeline(db_pool=pool)
        with patch(
            "aegis.core.event_bus.publish_event", new_callable=AsyncMock
        ) as publish:
            results = await pipeline.evaluate_shadows()
        assert results == []
        publish.assert_not_awaited()
