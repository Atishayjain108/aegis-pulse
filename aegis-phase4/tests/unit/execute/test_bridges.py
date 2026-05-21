"""Bridge tests — Phase 2 and Phase 3 → ComposerInput."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aegis.execute.bridge.phase2 import from_graph_result
from aegis.execute.bridge.phase3 import from_inference_result


# ---------------------------------------------------------------------------
# Phase 2 bridge
# ---------------------------------------------------------------------------
@dataclass
class _GraphResultStub:
    trend_id: str
    final_verdict: str
    final_score: float
    final_confidence: float
    final_priority: int
    halt_reason: str | None
    blocked_by: tuple[str, ...]
    decisions: list


def test_phase2_bridge_basic():
    tid = uuid4()
    g = _GraphResultStub(
        trend_id="t-1",
        final_verdict="ENTER",
        final_score=0.7,
        final_confidence=0.65,
        final_priority=1,
        halt_reason=None,
        blocked_by=(),
        decisions=[],
    )
    ci = from_graph_result(g, tenant_id=tid, decision_window="w", correlation_id="c")
    assert ci.tenant_id == tid
    assert ci.trend_id == "t-1"
    assert ci.phase2_verdict == "ENTER"
    assert ci.phase2_score == 0.7
    assert ci.phase2_confidence == 0.65
    assert ci.phase2_priority == 1
    assert ci.phase2_halt_reason is None
    assert ci.phase2_blocked_by == ()


def test_phase2_bridge_block_passes_through_reason():
    tid = uuid4()
    g = _GraphResultStub(
        trend_id="t",
        final_verdict="BLOCK",
        final_score=0.1,
        final_confidence=0.1,
        final_priority=3,
        halt_reason="compliance_block",
        blocked_by=("COMPLIANCE", "REDTEAM"),
        decisions=[],
    )
    ci = from_graph_result(g, tenant_id=tid)
    assert ci.phase2_verdict == "BLOCK"
    assert ci.phase2_halt_reason == "compliance_block"
    assert ci.phase2_blocked_by == ("COMPLIANCE", "REDTEAM")


def test_phase2_bridge_rejects_non_graphresult():
    with pytest.raises(TypeError):
        from_graph_result(SimpleNamespace(trend_id="x"), tenant_id=uuid4())


# ---------------------------------------------------------------------------
# Phase 3 bridge
# ---------------------------------------------------------------------------
@dataclass
class _PredStub:
    horizon_hours: int
    p_breakout: float
    p_decline: float
    p_saturation: float
    expected_margin_usd: float | None
    loss_probability: float | None
    confidence: float


@dataclass
class _BundleStub:
    predictions: list


@dataclass
class _InfResultStub:
    trend_id: str
    bundle: _BundleStub
    policy_action: str | None = "enter"


def test_phase3_bridge_picks_by_horizon():
    tid = uuid4()
    bundle = _BundleStub(
        predictions=[
            _PredStub(1, 0.5, 0.1, 0.2, 1.0, 0.3, 0.5),
            _PredStub(6, 0.4, 0.7, 0.4, 2.0, 0.4, 0.6),
            _PredStub(24, 0.8, 0.05, 0.5, 3.0, 0.2, 0.75),
        ]
    )
    r = _InfResultStub(trend_id="t", bundle=bundle, policy_action="ENTER")
    ci = from_inference_result(r, tenant_id=tid)
    assert ci.phase3_p_breakout_24h == 0.8  # 24h horizon
    assert ci.phase3_p_decline_6h == 0.7  # 6h horizon
    assert ci.phase3_p_saturation == 0.5  # max horizon (24h)
    # Means
    assert ci.phase3_expected_margin_usd == pytest.approx(2.0)
    assert ci.phase3_loss_probability == pytest.approx((0.3 + 0.4 + 0.2) / 3)
    assert ci.phase3_confidence == pytest.approx((0.5 + 0.6 + 0.75) / 3)
    assert ci.phase3_policy_action == "enter"


def test_phase3_bridge_empty_predictions_all_none():
    bundle = _BundleStub(predictions=[])
    r = _InfResultStub(trend_id="x", bundle=bundle, policy_action=None)
    ci = from_inference_result(r, tenant_id=uuid4())
    assert ci.phase3_p_breakout_24h is None
    assert ci.phase3_p_decline_6h is None
    assert ci.phase3_p_saturation is None
    assert ci.phase3_policy_action is None


def test_phase3_bridge_picks_closest_horizon_when_target_missing():
    bundle = _BundleStub(
        predictions=[
            _PredStub(72, 0.9, 0.05, 0.6, 5.0, 0.2, 0.8),
        ]
    )
    r = _InfResultStub(trend_id="t", bundle=bundle)
    ci = from_inference_result(r, tenant_id=uuid4())
    # 72h is closest to both 24h and 6h targets.
    assert ci.phase3_p_breakout_24h == 0.9
    assert ci.phase3_p_decline_6h == 0.05
