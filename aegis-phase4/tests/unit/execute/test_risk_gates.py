"""Risk gate chain tests."""

from __future__ import annotations

from uuid import uuid4

from aegis.execute.risk.gates import (
    GateChain,
    compliance_ftc_gate,
    confidence_gate,
    default_chain,
    loss_probability_gate,
    margin_floor_gate,
    tenant_blocklist_gate,
)
from aegis.execute.schemas.alert import Alert, AlertSource


def _alert(**overrides) -> Alert:
    base = {
        "alert_id": "x" * 32,
        "tenant_id": uuid4(),
        "trend_id": "t",
        "verdict": "ENTER",
        "priority": 1,
        "score": 0.7,
        "confidence": 0.7,
        "source": AlertSource.PHASE2_AND_PHASE3,
        "title": "t — ENTER",
        "expected_margin_usd": 2.5,
        "loss_probability": 0.2,
    }
    base.update(overrides)
    return Alert(**base)


def test_margin_gate_blocks_when_below_floor():
    a = _alert(expected_margin_usd=0.5)
    out = margin_floor_gate(a)
    assert out.passed is False
    assert out.reason and out.reason.code == "AEGIS-EXEC-0010"


def test_margin_gate_allows_when_at_floor():
    a = _alert(expected_margin_usd=1.0)
    out = margin_floor_gate(a)
    assert out.passed is True


def test_margin_gate_ignores_non_enter():
    a = _alert(verdict="HOLD", expected_margin_usd=0.01)
    out = margin_floor_gate(a)
    assert out.passed is True


def test_loss_prob_gate_blocks_above_ceiling():
    a = _alert(loss_probability=0.5)
    out = loss_probability_gate(a)
    assert out.passed is False
    assert out.reason and out.reason.code == "AEGIS-EXEC-0011"


def test_confidence_gate_blocks_low_conf():
    a = _alert(confidence=0.10)
    out = confidence_gate(a)
    assert out.passed is False


def test_confidence_gate_ignores_block_verdict():
    a = _alert(verdict="BLOCK", confidence=0.10, halt_reason="x")
    out = confidence_gate(a)
    assert out.passed is True


def test_tenant_blocklist_gate():
    tid = uuid4()
    gate = tenant_blocklist_gate(frozenset({str(tid)}))
    a = _alert()
    out = gate(a.model_copy(update={"tenant_id": tid}))
    assert out.passed is False


def test_chain_short_circuits_at_first_failure():
    a = _alert(expected_margin_usd=0.01, confidence=0.05)
    chain = GateChain([margin_floor_gate, confidence_gate])
    out = chain.run(a)
    # Becomes BLOCK with halt_reason set to margin (the first gate that fired).
    assert out.verdict == "BLOCK"
    assert out.halt_reason == "AEGIS-EXEC-0010"
    assert "AEGIS-EXEC-0010" in out.blocked_by


def test_default_chain_passes_clean_alert():
    chain = default_chain()
    a = _alert(expected_margin_usd=3.0, loss_probability=0.1, confidence=0.8)
    out = chain.run(a)
    assert out.verdict == "ENTER"
    assert out.alert_id == a.alert_id


def test_compliance_ftc_gate_blocks_deceptive_enter():
    # CONN-2: a deceptive-advertising ENTER must be downgraded to BLOCK.
    gate = compliance_ftc_gate()
    a = _alert(
        title="Miracle cure — guaranteed to cure cancer, FDA approved!",
        summary_text="Lose 30 pounds in 3 days, 100% guaranteed weight loss!",
    )
    out = gate(a)
    # aegis.compliance ships in this repo, so the deceptive text trips the FTC rules.
    assert out.passed is False
    assert out.reason is not None
    assert out.reason.code == "AEGIS-EXEC-0014"


def test_compliance_ftc_gate_ignores_non_enter():
    gate = compliance_ftc_gate()
    a = _alert(verdict="HOLD", title="guaranteed to cure cancer")
    assert gate(a).passed is True
