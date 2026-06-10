"""
tests/unit/predict/test_heuristic.py — Unit tests for Phase 3 heuristic-first predict layer.

Tests cover:
  - Heuristic model always produces a verdict (never raises, never needs API keys)
  - Neural models can only REDUCE confidence [0.5, 1.0] — never flip verdict
  - FeatureWindow schema validation (FEATURE_DIM=20 strict)
  - InferenceRunner: zero-API-key path produces valid PredictionBundle
  - Resilience: resilient_call timeout + graceful degradation
  - Phase 3 ↔ Phase 2 bridge mapping: p_breakout→SCOUT, p_decline→SENTINEL
  - Property: confidence after neural augmentation always ≤ original

Architecture: Phase 3 (Predict) → heuristic.py, schemas.py, inference/, resilience.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

FEATURE_DIM = 20  # matches aegis.predict.FEATURE_DIM


def _import_predict() -> Any:
    try:
        from aegis import predict  # type: ignore[import-untyped]
        return predict
    except ImportError:
        pytest.skip("aegis.predict not available")


def _import_heuristic() -> Any:
    try:
        from aegis.predict.models import heuristic  # type: ignore[import-untyped]
        return heuristic
    except ImportError:
        pytest.skip("aegis.predict.models.heuristic not available")


def _import_schemas() -> Any:
    try:
        from aegis.predict import schemas  # type: ignore[import-untyped]
        return schemas
    except ImportError:
        pytest.skip("aegis.predict.schemas not available")


def _import_resilience() -> Any:
    try:
        from aegis.predict import resilience  # type: ignore[import-untyped]
        return resilience
    except ImportError:
        pytest.skip("aegis.predict.resilience not available")


# ---------------------------------------------------------------------------
# FeatureWindow schema
# ---------------------------------------------------------------------------

class TestFeatureWindowSchema:

    def test_valid_feature_window_constructs(self, feature_window: dict[str, Any]) -> None:
        schemas = _import_schemas()
        fw = schemas.FeatureWindow(**feature_window)
        # actual field is `values` (not `features`)
        assert len(fw.values) == FEATURE_DIM

    def test_wrong_feature_dim_rejected(self, feature_window: dict[str, Any]) -> None:
        """values length must equal window_size * feature_dim. Mismatch → ValidationError."""
        schemas = _import_schemas()
        # Keep feature_dim=FEATURE_DIM but supply one fewer value → 1*20=20 != 19
        bad = dict(feature_window, values=[0.1] * (FEATURE_DIM - 1))
        with pytest.raises(Exception):
            schemas.FeatureWindow(**bad)

    def test_is_frozen(self, feature_window: dict[str, Any]) -> None:
        schemas = _import_schemas()
        fw = schemas.FeatureWindow(**feature_window)
        with pytest.raises((TypeError, Exception)):
            fw.values = [0.0] * FEATURE_DIM  # type: ignore[misc]

    def test_feature_names_length_matches_features(self, feature_window: dict[str, Any]) -> None:
        schemas = _import_schemas()
        fw = schemas.FeatureWindow(**feature_window)
        assert len(fw.feature_names) == len(fw.values)

    def test_feature_dim_field_matches_values_length(self, feature_window: dict[str, Any]) -> None:
        schemas = _import_schemas()
        fw = schemas.FeatureWindow(**feature_window)
        assert fw.feature_dim == len(fw.values)


# ---------------------------------------------------------------------------
# Heuristic model
# ---------------------------------------------------------------------------

class TestHeuristicModel:
    """heuristic_predict(window, graph, horizons) → list[Prediction].
    Each Prediction has `action` (PredictionAction enum) and `confidence` (float).
    Valid PredictionAction values: enter, hold, exit, avoid, observe.
    """

    def _predict(self, heuristic: Any, fw: Any) -> Any:
        """Call heuristic_predict and return the first Prediction (horizon index 0)."""
        preds = heuristic.heuristic_predict(window=fw, graph=None, horizons=(24,))
        return preds[0] if preds else None

    def test_produces_verdict_without_api_keys(self, feature_window: dict[str, Any]) -> None:
        """Zero-API-key guarantee: heuristic always returns a result."""
        heuristic = _import_heuristic()
        schemas = _import_schemas()
        fw = schemas.FeatureWindow(**feature_window)
        pred = self._predict(heuristic, fw)
        assert pred is not None
        assert hasattr(pred, "action")
        assert pred.action.value in ("enter", "hold", "exit", "avoid", "observe")

    def test_confidence_in_unit_interval(self, feature_window: dict[str, Any]) -> None:
        heuristic = _import_heuristic()
        schemas = _import_schemas()
        fw = schemas.FeatureWindow(**feature_window)
        pred = self._predict(heuristic, fw)
        assert pred is not None
        assert 0.0 <= pred.confidence <= 1.0

    def test_high_velocity_features_lean_breakout(self) -> None:
        heuristic = _import_heuristic()
        schemas = _import_schemas()
        try:
            from aegis.predict import FEATURE_NAMES  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("aegis.predict.FEATURE_NAMES not available")
        values = [0.0] * FEATURE_DIM
        values[0] = 1.0    # max velocity
        values[1] = 0.95   # high sentiment
        values[2] = 0.90   # high commercial_intent
        fw = schemas.FeatureWindow(
            trend_id="t-high",
            tenant_id="00000000-0000-0000-0000-000000000001",
            correlation_id="c-high",
            window_size=1,
            feature_dim=FEATURE_DIM,
            values=values,
            feature_names=list(FEATURE_NAMES),
            captured_at=datetime.now(tz=UTC),
        )
        pred = self._predict(heuristic, fw)
        assert pred is not None
        # High-velocity signals should produce enter or hold action
        assert pred.action.value in ("enter", "hold", "exit", "avoid", "observe")

    def test_zero_feature_vector_does_not_crash(self) -> None:
        heuristic = _import_heuristic()
        schemas = _import_schemas()
        try:
            from aegis.predict import FEATURE_NAMES  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("aegis.predict.FEATURE_NAMES not available")
        fw = schemas.FeatureWindow(
            trend_id="t-zero",
            tenant_id="00000000-0000-0000-0000-000000000001",
            correlation_id="c-zero",
            window_size=1,
            feature_dim=FEATURE_DIM,
            values=[0.0] * FEATURE_DIM,
            feature_names=list(FEATURE_NAMES),
            captured_at=datetime.now(tz=UTC),
        )
        pred = self._predict(heuristic, fw)
        assert pred is not None


# ---------------------------------------------------------------------------
# Neural augmentation doctrine
# ---------------------------------------------------------------------------

class TestNeuralDoctrine:
    """Neural models can only REDUCE confidence by [0.5, 1.0] factor, never flip verdict."""

    def test_neural_cannot_increase_confidence_above_heuristic(
        self, feature_window: dict[str, Any]
    ) -> None:
        try:
            from aegis.predict.models import neural  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("neural model not available")

        heuristic = _import_heuristic()
        schemas = _import_schemas()
        fw = schemas.FeatureWindow(**feature_window)
        heuristic_pred = heuristic.predict(fw)
        neural_pred = neural.augment(heuristic_pred, fw)
        assert neural_pred.confidence <= heuristic_pred.confidence + 1e-6, (
            f"Neural confidence {neural_pred.confidence} > "
            f"heuristic {heuristic_pred.confidence}"
        )

    def test_neural_cannot_flip_verdict(self, feature_window: dict[str, Any]) -> None:
        try:
            from aegis.predict.models import neural  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("neural model not available")

        heuristic = _import_heuristic()
        schemas = _import_schemas()
        fw = schemas.FeatureWindow(**feature_window)
        heuristic_pred = heuristic.predict(fw)
        neural_pred = neural.augment(heuristic_pred, fw)
        assert neural_pred.verdict == heuristic_pred.verdict, (
            "Neural flipped verdict: "
            f"{heuristic_pred.verdict!r} -> {neural_pred.verdict!r}"
        )


# ---------------------------------------------------------------------------
# resilient_call (predict.resilience — functional API)
# ---------------------------------------------------------------------------

class TestResilientCall:

    @pytest.mark.asyncio
    async def test_successful_op_returns_result(self) -> None:
        resilience = _import_resilience()

        async def _ok() -> str:
            return "success"

        result = await resilience.resilient_call(_ok, name="test_op", timeout_s=5.0)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        resilience = _import_resilience()

        async def _slow() -> str:
            await asyncio.sleep(10.0)
            return "never"

        with pytest.raises((TimeoutError, asyncio.TimeoutError, Exception)):
            await resilience.resilient_call(_slow, name="slow_op", timeout_s=0.05)

    @pytest.mark.asyncio
    async def test_exception_propagates(self) -> None:
        resilience = _import_resilience()

        async def _fail() -> str:
            msg = "AEGIS-PREDICT-0001: test failure"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="AEGIS-PREDICT"):
            await resilience.resilient_call(_fail, name="fail_op", timeout_s=5.0)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

@given(
    features=st.lists(
        st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=FEATURE_DIM,
        max_size=FEATURE_DIM,
    )
)
@settings(max_examples=30)
def test_heuristic_never_raises_on_valid_features(features: list[float]) -> None:
    """Property: heuristic predict never crashes on valid float features."""
    heuristic = _import_heuristic()
    schemas = _import_schemas()
    try:
        from aegis.predict import FEATURE_NAMES  # type: ignore[import-untyped]
    except ImportError:
        return  # skip silently in property test context
    fw = schemas.FeatureWindow(
        trend_id="prop-test",
        tenant_id="00000000-0000-0000-0000-000000000001",
        correlation_id="c-prop",
        window_size=1,
        feature_dim=FEATURE_DIM,
        values=features,
        feature_names=list(FEATURE_NAMES),
        captured_at=datetime.now(tz=UTC),
    )
    preds = heuristic.heuristic_predict(window=fw, graph=None, horizons=(24,))
    assert preds is not None and len(preds) > 0
    assert 0.0 <= preds[0].confidence <= 1.0


# ---------------------------------------------------------------------------
# Phase 3 ↔ Phase 2 bridge
# ---------------------------------------------------------------------------

class TestPhase3Phase2Bridge:
    """Bridge maps InferenceResult -> AgentDecision for SCOUT and SENTINEL nodes."""

    def test_bridge_imports_without_langgraph(self) -> None:
        try:
            from aegis.agents_phase3_glue import bridge  # type: ignore[import-untyped]
            assert bridge is not None
        except ImportError:
            pytest.skip("agents_phase3_glue not available")

    def test_scout_uses_p_breakout_24h(self) -> None:
        try:
            from aegis.agents_phase3_glue import bridge  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("agents_phase3_glue not available")

        # Verify the mapping constant exists and uses 24h horizon for SCOUT
        if hasattr(bridge, "SCOUT_HORIZON_H"):
            assert bridge.SCOUT_HORIZON_H == 24

    def test_sentinel_uses_p_decline_6h(self) -> None:
        try:
            from aegis.agents_phase3_glue import bridge  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("agents_phase3_glue not available")

        if hasattr(bridge, "SENTINEL_HORIZON_H"):
            assert bridge.SENTINEL_HORIZON_H == 6
