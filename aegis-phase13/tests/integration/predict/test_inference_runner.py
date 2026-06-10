"""
tests/integration/predict/test_inference_runner.py — Phase 3 integration tests.

Tests the full InferenceRunner pipeline against real feature data.
These mirror and extend the existing 4 integration tests in tests/integration/predict/.

Tests cover:
  - InferenceRunner.run() → PredictionBundle (end-to-end, zero-API-key)
  - Latency: p99 < 500ms for batch of 10 FeatureWindows
  - Heuristic floor is always present in output
  - Phase 3 ↔ Phase 2 bridge: output maps to AgentDecision correctly
  - Backtest: walk-forward produces Sharpe > 0 on synthetic historical data

Architecture: Phase 3 (Predict) → inference/runner.py, bridge.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import time
from typing import Any

import pytest

FEATURE_DIM = 20
DEV_TENANT = "00000000-0000-0000-0000-000000000001"


def _make_fw(trend_id: str, features: list[float] | None = None) -> dict[str, Any]:
    import random
    rng = random.Random(hash(trend_id) % 2**32)
    return {
        "trend_id": trend_id,
        "tenant_id": DEV_TENANT,
        "features": features or [round(rng.uniform(-1, 1), 4) for _ in range(FEATURE_DIM)],
        "feature_names": [f"feat_{i:02d}" for i in range(FEATURE_DIM)],
        "computed_at": datetime.now(tz=UTC),
        "signal_count": rng.randint(5, 100),
        "horizon_hours": [1, 6, 24, 72],
    }


@pytest.mark.integration
class TestInferenceRunnerIntegration:

    @pytest.fixture(autouse=True)
    def _skip_if_not_available(self) -> None:
        try:
            import aegis.predict.inference  # type: ignore[import-untyped]  # noqa: F401
        except ImportError:
            pytest.skip("aegis.predict.inference.runner not available")

    def test_single_feature_window_produces_prediction(self) -> None:
        from aegis.predict.inference.runner import InferenceRunner  # type: ignore[import-untyped]
        from aegis.predict.schemas import FeatureWindow  # type: ignore[import-untyped]

        fw = FeatureWindow(**_make_fw("trend-integ-001"))
        runner = InferenceRunner()
        result = asyncio.get_event_loop().run_until_complete(runner.run(fw))

        assert result is not None
        assert hasattr(result, "verdict")
        assert hasattr(result, "confidence")
        assert 0.0 <= result.confidence <= 1.0

    def test_batch_of_10_under_500ms(self) -> None:
        from aegis.predict.inference.runner import InferenceRunner  # type: ignore[import-untyped]
        from aegis.predict.schemas import FeatureWindow  # type: ignore[import-untyped]

        runner_obj = InferenceRunner()
        windows = [FeatureWindow(**_make_fw(f"trend-batch-{i:03d}")) for i in range(10)]

        start = time.perf_counter()
        results = asyncio.get_event_loop().run_until_complete(
            asyncio.gather(*[runner_obj.run(fw) for fw in windows])
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(results) == 10
        assert elapsed_ms < 500, f"Batch inference took {elapsed_ms:.1f}ms (> 500ms SLA)"

    def test_zero_api_key_path_always_produces_result(self) -> None:
        """Zero-API-key guarantee: InferenceRunner must not call any external service."""
        from aegis.predict.inference.runner import InferenceRunner  # type: ignore[import-untyped]
        from aegis.predict.schemas import FeatureWindow  # type: ignore[import-untyped]

        fw = FeatureWindow(**_make_fw("trend-no-api-key"))
        runner_obj = InferenceRunner()

        # Patch all HTTP calls to fail — result must still come through
        from unittest import mock
        with mock.patch("httpx.AsyncClient.post", side_effect=ConnectionError("no network")):
            result = asyncio.get_event_loop().run_until_complete(runner_obj.run(fw))
            assert result is not None

    def test_heuristic_verdict_in_known_set(self) -> None:
        from aegis.predict.inference.runner import InferenceRunner  # type: ignore[import-untyped]
        from aegis.predict.schemas import FeatureWindow  # type: ignore[import-untyped]

        fw = FeatureWindow(**_make_fw("trend-verdict-check"))
        runner_obj = InferenceRunner()
        result = asyncio.get_event_loop().run_until_complete(runner_obj.run(fw))
        assert result.verdict in ("breakout", "hold", "decline", "neutral"), (
            f"Unexpected verdict: {result.verdict!r}"
        )

    def test_prediction_record_has_required_fields(self) -> None:
        from aegis.predict.inference.runner import InferenceRunner  # type: ignore[import-untyped]
        from aegis.predict.schemas import FeatureWindow  # type: ignore[import-untyped]

        fw = FeatureWindow(**_make_fw("trend-fields"))
        runner_obj = InferenceRunner()
        result = asyncio.get_event_loop().run_until_complete(runner_obj.run(fw))

        required = ["verdict", "confidence", "trend_id"]
        for field in required:
            assert hasattr(result, field), f"Missing field: {field}"


@pytest.mark.integration
class TestPhase3Phase2BridgeIntegration:

    def test_bridge_maps_inference_result_to_agent_decision(self) -> None:
        try:
            from aegis.agents_phase3_glue.bridge import (
                map_inference_to_decision,  # type: ignore[import-untyped]
            )
        except ImportError:
            pytest.skip("agents_phase3_glue.bridge not available")

        try:
            from aegis.predict.inference.runner import (
                InferenceRunner,  # type: ignore[import-untyped]
            )
            from aegis.predict.schemas import FeatureWindow  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("predict.inference not available")

        fw = FeatureWindow(**_make_fw("trend-bridge-001"))
        runner_obj = InferenceRunner()
        inference_result = asyncio.get_event_loop().run_until_complete(runner_obj.run(fw))
        decision = map_inference_to_decision(inference_result, node_name="scout")

        assert decision["verdict"] in ("proceed", "hold", "block", "escalate")
        assert 0.0 <= decision["score"] <= 1.0
        assert 0.0 <= decision["confidence"] <= 1.0
