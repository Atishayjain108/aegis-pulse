"""Tests for `aegis.predict.inference.runner` end-to-end."""

from __future__ import annotations

import pytest

from aegis.predict import DEFAULT_HORIZONS
from aegis.predict.inference import (
    AuditRecord,
    InferenceConfig,
    InferenceResult,
    InferenceRunner,
    predict_for_trend,
)
from aegis.predict.schemas import (
    ModelKind,
    PredictionAction,
    PredictionBundle,
    PredictionRecord,
)


class TestInferenceRunner:
    @pytest.mark.asyncio
    async def test_run_with_signals(self, synthetic_signals):
        runner = InferenceRunner()
        result = await runner.run(tenant_id="t1", trend_id="trend-1", signals=synthetic_signals)
        assert isinstance(result, InferenceResult)
        assert isinstance(result.bundle, PredictionBundle)
        assert isinstance(result.record, PredictionRecord)
        assert isinstance(result.audit, AuditRecord)
        assert tuple(sorted(p.horizon_hours for p in result.bundle.predictions)) == tuple(
            sorted(DEFAULT_HORIZONS)
        )

    @pytest.mark.asyncio
    async def test_run_with_window(self, feature_window):
        runner = InferenceRunner()
        result = await runner.run(tenant_id="t1", trend_id="trend-1", window=feature_window)
        assert result.bundle.is_heuristic_only is True
        assert len(result.halt_reasons) == 0

    @pytest.mark.asyncio
    async def test_run_without_signals_or_window_raises(self):
        runner = InferenceRunner()
        with pytest.raises(ValueError):
            await runner.run(tenant_id="t1", trend_id="trend-1")

    @pytest.mark.asyncio
    async def test_correlation_id_propagates(self, synthetic_signals):
        cfg = InferenceConfig(correlation_id="my-trace-id")
        runner = InferenceRunner(config=cfg)
        result = await runner.run(tenant_id="t1", trend_id="trend-1", signals=synthetic_signals)
        assert result.bundle.correlation_id == "my-trace-id"
        assert result.audit.correlation_id == "my-trace-id"

    @pytest.mark.asyncio
    async def test_predictor_lazy_loaded_once(self, synthetic_signals):
        runner = InferenceRunner()
        assert runner._temporal is None
        await runner.run(tenant_id="t1", trend_id="trend-1", signals=synthetic_signals)
        assert runner._temporal is not None
        first_t = runner._temporal
        # second call should reuse
        await runner.run(tenant_id="t1", trend_id="trend-2", signals=synthetic_signals)
        assert runner._temporal is first_t

    @pytest.mark.asyncio
    async def test_audit_carries_halt_reasons(self, synthetic_signals):
        # 1ms latency budget guarantees a budget breach.
        cfg = InferenceConfig(latency_budget_ms=0.001)
        runner = InferenceRunner(config=cfg)
        result = await runner.run(tenant_id="t1", trend_id="trend-1", signals=synthetic_signals)
        assert any("latency_budget_exceeded" in r for r in result.halt_reasons)
        assert any("latency_budget_exceeded" in r for r in result.audit.halt_reasons)

    @pytest.mark.asyncio
    async def test_graph_summary_present(self, synthetic_signals):
        runner = InferenceRunner()
        result = await runner.run(tenant_id="t1", trend_id="trend-1", signals=synthetic_signals)
        for k in ("n_authors", "n_platforms", "density", "coordination_score"):
            assert k in result.graph_summary

    @pytest.mark.asyncio
    async def test_disabled_causal(self, synthetic_signals):
        cfg = InferenceConfig(enable_causal=False)
        runner = InferenceRunner(config=cfg)
        result = await runner.run(tenant_id="t1", trend_id="trend-1", signals=synthetic_signals)
        assert result.causal == ()
        assert result.audit.causal_top == []

    @pytest.mark.asyncio
    async def test_record_has_signature_placeholder(self, synthetic_signals):
        runner = InferenceRunner()
        result = await runner.run(tenant_id="t1", trend_id="trend-1", signals=synthetic_signals)
        assert result.record.signature  # non-empty (schema requires)
        assert result.record.bundle == result.bundle


class TestPredictForTrendHelper:
    @pytest.mark.asyncio
    async def test_helper_runs(self, synthetic_signals):
        result = await predict_for_trend("t1", "trend-1", signals=synthetic_signals)
        assert result.bundle is not None
        assert result.duration_ms > 0


class TestFusedBundleShape:
    @pytest.mark.asyncio
    async def test_fused_bundle_marked_as_fusion(self, synthetic_signals):
        runner = InferenceRunner()
        result = await runner.run(tenant_id="t1", trend_id="trend-1", signals=synthetic_signals)
        assert result.bundle.model_kind == ModelKind.FUSION
        assert "fusion::" in result.bundle.model_id

    @pytest.mark.asyncio
    async def test_fused_predictions_in_unit_range(self, synthetic_signals):
        runner = InferenceRunner()
        result = await runner.run(tenant_id="t1", trend_id="trend-1", signals=synthetic_signals)
        for p in result.bundle.predictions:
            assert 0.0 <= p.p_breakout <= 1.0
            assert 0.0 <= p.confidence <= 1.0
            assert p.action in PredictionAction
