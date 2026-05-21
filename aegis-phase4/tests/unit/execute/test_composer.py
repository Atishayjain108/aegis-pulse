"""Composer behaviour tests.

Covers each verdict branch, every input combination, and the BLOCK-with-
no-halt-reason defensive fallback.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from aegis.execute.bridge.types import ComposerInput
from aegis.execute.errors import AegisExecuteError
from aegis.execute.policy.composer import compose
from aegis.execute.schemas.alert import AlertSource


def _ci(**overrides) -> ComposerInput:
    base = {
        "tenant_id": uuid4(),
        "trend_id": "t-1",
        "decision_window": "default",
    }
    base.update(overrides)
    return ComposerInput(**base)


def test_compose_raises_when_both_inputs_absent():
    ci = _ci()
    with pytest.raises(AegisExecuteError) as exc_info:
        compose(ci)
    assert exc_info.value.spec.code == "AEGIS-EXEC-0001"


def test_compose_enter_when_both_agree():
    ci = _ci(
        phase2_verdict="ENTER",
        phase2_score=0.80,
        phase2_confidence=0.70,
        phase2_priority=1,
        phase3_p_breakout_24h=0.85,
        phase3_p_decline_6h=0.10,
        phase3_confidence=0.75,
        phase3_policy_action="enter",
        phase3_expected_margin_usd=3.5,
        phase3_loss_probability=0.15,
    )
    a = compose(ci)
    assert a.verdict == "ENTER"
    assert a.source == AlertSource.PHASE2_AND_PHASE3
    # Score is blended (~0.5*0.80 + 0.5*(0.85*0.9))
    assert 0.5 < a.score <= 1.0
    assert 0.5 < a.confidence <= 1.0
    # P0 because p_breakout >= 0.80 and conf >= 0.70
    assert a.priority == 0


def test_compose_exit_when_phase3_decline_high():
    ci = _ci(
        phase2_verdict="HOLD",
        phase2_score=0.3,
        phase2_confidence=0.4,
        phase3_p_breakout_24h=0.10,
        phase3_p_decline_6h=0.85,
        phase3_p_saturation=0.5,
        phase3_confidence=0.8,
    )
    a = compose(ci)
    assert a.verdict == "EXIT"


def test_compose_exit_when_saturation_high():
    ci = _ci(
        phase2_verdict="HOLD",
        phase2_score=0.3,
        phase2_confidence=0.4,
        phase3_p_breakout_24h=0.10,
        phase3_p_decline_6h=0.10,
        phase3_p_saturation=0.85,
        phase3_confidence=0.6,
    )
    a = compose(ci)
    assert a.verdict == "EXIT"


def test_compose_block_overrides_everything():
    ci = _ci(
        phase2_verdict="BLOCK",
        phase2_score=0.99,
        phase2_confidence=0.99,
        phase2_halt_reason="compliance_block",
        phase2_blocked_by=("COMPLIANCE",),
        phase3_p_breakout_24h=0.95,
        phase3_confidence=0.95,
    )
    a = compose(ci)
    assert a.verdict == "BLOCK"
    assert a.halt_reason == "compliance_block"
    assert a.blocked_by == ("COMPLIANCE",)
    assert a.priority == 3


def test_compose_block_without_reason_gets_default():
    ci = _ci(
        phase2_verdict="BLOCK",
        phase2_score=0.1,
        phase2_confidence=0.1,
    )
    a = compose(ci)
    assert a.verdict == "BLOCK"
    assert a.halt_reason == "policy_block"


def test_compose_degraded_when_one_side_weak():
    ci = _ci(
        phase2_verdict="HOLD",
        phase2_score=0.2,
        phase2_confidence=0.3,  # below ENTER_MIN_CONFIDENCE
    )
    a = compose(ci)
    assert a.verdict == "DEGRADED"
    assert a.source == AlertSource.PHASE2_ONLY


def test_compose_hold_default():
    ci = _ci(
        phase2_verdict="HOLD",
        phase2_score=0.4,
        phase2_confidence=0.65,
        phase3_p_breakout_24h=0.30,
        phase3_p_decline_6h=0.20,
        phase3_confidence=0.55,
    )
    a = compose(ci)
    assert a.verdict == "HOLD"


def test_compose_phase3_only_path():
    ci = _ci(
        phase3_p_breakout_24h=0.95,
        phase3_p_decline_6h=0.05,
        phase3_confidence=0.85,
        phase3_policy_action="enter",
        phase3_expected_margin_usd=5.0,
        phase3_loss_probability=0.10,
    )
    a = compose(ci)
    # Phase 2 absent → score is 0.5*None + 0.5*phase3 → only phase3
    assert a.source == AlertSource.PHASE3_ONLY
    assert a.verdict == "ENTER"


def test_compose_phase2_only_path_low_conf_degrades():
    ci = _ci(
        phase2_verdict="HOLD",
        phase2_score=0.85,
        phase2_confidence=0.20,  # too low
        phase2_priority=2,
    )
    a = compose(ci)
    assert a.source == AlertSource.PHASE2_ONLY
    # With low conf, only-Phase-2 returns DEGRADED
    assert a.verdict == "DEGRADED"


def test_compose_priority_p1_when_breakout_moderate():
    ci = _ci(
        phase2_verdict="ENTER",
        phase2_score=0.65,
        phase2_confidence=0.60,
        phase2_priority=2,
        phase3_p_breakout_24h=0.65,  # >= P1 floor but < P0
        phase3_confidence=0.60,
        phase3_policy_action="enter",
    )
    a = compose(ci)
    assert a.verdict == "ENTER"
    assert a.priority == 1


def test_priority_never_demotes_upstream():
    # Upstream says P0 — composer cannot bump to P2.
    ci = _ci(
        phase2_verdict="ENTER",
        phase2_score=0.65,
        phase2_confidence=0.60,
        phase2_priority=0,  # critical from Phase 2
        phase3_p_breakout_24h=0.30,
        phase3_confidence=0.60,
        phase3_policy_action="enter",
    )
    a = compose(ci)
    assert a.priority == 0


def test_summary_includes_quant_bits():
    ci = _ci(
        phase2_verdict="ENTER",
        phase2_score=0.70,
        phase2_confidence=0.65,
        phase2_narrative="Strong creator-led signal.",
        phase3_p_breakout_24h=0.82,
        phase3_p_decline_6h=0.10,
        phase3_expected_margin_usd=4.20,
        phase3_loss_probability=0.20,
        phase3_confidence=0.72,
        phase3_policy_action="enter",
    )
    a = compose(ci)
    assert "Strong creator-led signal" in a.summary_text
    assert "p_breakout(24h)=0.82" in a.summary_text
    assert "E[margin]=$4.20" in a.summary_text
    assert "verdict=ENTER" in a.summary_text


def test_alert_id_is_deterministic_for_same_input():
    tid = uuid4()
    args = {
        "tenant_id": tid,
        "trend_id": "t-x",
        "phase2_verdict": "ENTER",
        "phase2_score": 0.80,
        "phase2_confidence": 0.70,
        "phase3_p_breakout_24h": 0.90,
        "phase3_p_decline_6h": 0.05,
        "phase3_confidence": 0.80,
        "phase3_policy_action": "enter",
    }
    a1 = compose(ComposerInput(**args))
    a2 = compose(ComposerInput(**args))
    assert a1.alert_id == a2.alert_id
