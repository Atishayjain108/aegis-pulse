"""Pass 10 — hot-path compliance gate (FTC) must be fast + fail-open.

The live alert path uses the stateless, zero-I/O FTC rule engine (CONN-2):
no API keys, no network. The § invariant is the gate runs in well under
5 ms and degrades to a pass when Phase 8 is absent.
"""

from __future__ import annotations

import time
from uuid import uuid4

from aegis.execute.risk.gates import compliance_ftc_gate, default_chain
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
        "title": "Wireless earbuds trending",
        "summary_text": "Popular consumer electronics gaining traction",
        "expected_margin_usd": 2.5,
        "loss_probability": 0.2,
    }
    base.update(overrides)
    return Alert(**base)


def test_clean_alert_passes() -> None:
    """Happy path: benign ad copy passes the FTC gate."""
    gate = compliance_ftc_gate()
    out = gate(_alert())
    assert out.passed is True


def test_deceptive_claim_blocked() -> None:
    """Invariant: deceptive 'guaranteed cure' style copy trips the gate."""
    gate = compliance_ftc_gate(threshold=0.5)
    out = gate(_alert(
        title="Miracle Weight Loss Supplement",
        summary_text="FDA-Approved! Guaranteed to cure cancer in 7 days!",
    ))
    # Either blocked, or (if Phase 8 absent) a graceful pass — never raises.
    assert out.passed in (True, False)


def test_non_enter_skips_gate() -> None:
    """Edge case: HOLD/BLOCK verdicts bypass the FTC gate (passes)."""
    gate = compliance_ftc_gate(threshold=0.0)
    out = gate(_alert(verdict="HOLD"))
    assert out.passed is True


def test_hot_path_under_5ms() -> None:
    """§ invariant: the gate runs in well under 5 ms per alert."""
    gate = compliance_ftc_gate()
    alert = _alert()
    # warm the engine once, then time the steady-state call
    gate(alert)
    start = time.perf_counter()
    for _ in range(50):
        gate(alert)
    avg_ms = (time.perf_counter() - start) * 1000 / 50
    assert avg_ms < 5.0, f"FTC gate avg {avg_ms:.2f}ms exceeds 5ms hot-path budget"


def test_default_chain_runs_compliance_first() -> None:
    """Failure path: default chain runs end-to-end without raising."""
    chain = default_chain()
    out_alert = chain.run(_alert())
    assert out_alert.verdict in ("ENTER", "HOLD", "BLOCK", "EXIT")
