"""Risk gate chain.

A `RiskGate` is a pure function `(Alert) → GateOutcome`. The chain runs in
order; the first gate that BLOCKS short-circuits the rest. PASS gates keep
the alert flowing through. The composer's verdict is preserved; gates can
ONLY downgrade (ENTER → BLOCK) — never upgrade.

This mirrors Phase 3's "neural can only reduce confidence" invariant.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import structlog

from aegis.execute.constants import (
    MAX_LOSS_PROBABILITY,
    MIN_EXPECTED_MARGIN_USD,
    MIN_OVERALL_CONFIDENCE,
    VERDICT_BLOCK,
    VERDICT_ENTER,
)
from aegis.execute.errors import (
    EXEC_RISK_COMPLIANCE_FTC,
    EXEC_RISK_CONFIDENCE_TOO_LOW,
    EXEC_RISK_LOSS_PROB_TOO_HIGH,
    EXEC_RISK_MARGIN_BELOW_FLOOR,
    EXEC_RISK_TENANT_BLOCKED,
    ErrorSpec,
)
from aegis.execute.schemas.alert import Alert

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """Outcome of one gate."""

    passed: bool
    reason: ErrorSpec | None = None
    # If `passed=False`, the new verdict is forced to BLOCK and `blocked_by`
    # is appended with `reason.code`. The Alert object is rebuilt by the
    # chain runner.


RiskGate = Callable[[Alert], GateOutcome]


# ---------------------------------------------------------------------------
# Built-in gates
# ---------------------------------------------------------------------------
def margin_floor_gate(alert: Alert) -> GateOutcome:
    """Block ENTER alerts whose expected margin is below floor.

    HOLD / EXIT / DEGRADED / existing BLOCK pass through untouched.
    """
    if alert.verdict != VERDICT_ENTER:
        return GateOutcome(passed=True)
    margin = alert.expected_margin_usd
    if margin is None:
        # No quant info → conservative pass (composer handled the verdict).
        return GateOutcome(passed=True)
    if margin < MIN_EXPECTED_MARGIN_USD:
        return GateOutcome(passed=False, reason=EXEC_RISK_MARGIN_BELOW_FLOOR)
    return GateOutcome(passed=True)


def loss_probability_gate(alert: Alert) -> GateOutcome:
    if alert.verdict != VERDICT_ENTER:
        return GateOutcome(passed=True)
    loss = alert.loss_probability
    if loss is None:
        return GateOutcome(passed=True)
    if loss > MAX_LOSS_PROBABILITY:
        return GateOutcome(passed=False, reason=EXEC_RISK_LOSS_PROB_TOO_HIGH)
    return GateOutcome(passed=True)


def confidence_gate(alert: Alert) -> GateOutcome:
    if alert.verdict == VERDICT_BLOCK:
        return GateOutcome(passed=True)
    if alert.confidence < MIN_OVERALL_CONFIDENCE:
        return GateOutcome(passed=False, reason=EXEC_RISK_CONFIDENCE_TOO_LOW)
    return GateOutcome(passed=True)


def compliance_ftc_gate(threshold: float = 0.80) -> RiskGate:
    """Factory: block ENTER alerts whose text trips the FTC advertising rules.

    CONN-2 — puts the Phase 8 compliance engine into the live alert path. Uses
    the **FTC rule engine** specifically because it is stateless, zero-I/O and
    needs no API keys, so it is safe on the hot path (the IPR/FDA/OFAC checks
    require a product origin/destination the trend alert does not carry, and
    make live external calls — those belong in the Phase 6 plan path, not here).

    Degrades to a pass when ``aegis.compliance`` is not importable, so Phase 4
    keeps working without Phase 8 installed.
    """
    try:
        from aegis.compliance.ftc import FTCRuleEngine

        _engine = FTCRuleEngine()
    except Exception:  # Phase 8 not installed → gate is a no-op
        _engine = None

    def _gate(alert: Alert) -> GateOutcome:
        if alert.verdict != VERDICT_ENTER or _engine is None:
            return GateOutcome(passed=True)
        _violations, risk = _engine.assess(alert.title, alert.summary_text)
        if risk >= threshold:
            return GateOutcome(passed=False, reason=EXEC_RISK_COMPLIANCE_FTC)
        return GateOutcome(passed=True)

    return _gate


def tenant_blocklist_gate(blocklist: frozenset[str]) -> RiskGate:
    """Factory: returns a gate that blocks any alert from a tenant in `blocklist`."""

    def _gate(alert: Alert) -> GateOutcome:
        if str(alert.tenant_id) in blocklist:
            return GateOutcome(passed=False, reason=EXEC_RISK_TENANT_BLOCKED)
        return GateOutcome(passed=True)

    return _gate


# ---------------------------------------------------------------------------
# Chain runner
# ---------------------------------------------------------------------------
class GateChain:
    """Composable chain of gates.

    Usage:
        chain = GateChain([margin_floor_gate, loss_probability_gate, confidence_gate])
        result_alert = chain.run(alert)

    The returned alert is either the original (all gates passed) or a new
    BLOCK-verdict alert with `halt_reason` set to the first failing code and
    `blocked_by` populated.
    """

    __slots__ = ("_gates",)

    def __init__(self, gates: list[RiskGate] | None = None) -> None:
        self._gates: list[RiskGate] = list(gates) if gates else []

    def run(self, alert: Alert) -> Alert:
        """Run the chain. Returns the possibly-modified alert."""
        for gate in self._gates:
            outcome = gate(alert)
            if not outcome.passed:
                assert outcome.reason is not None  # type narrowing
                _log.warning(
                    "execute.risk.gate_blocked",
                    alert_id=alert.alert_id,
                    trend_id=alert.trend_id,
                    reason_code=outcome.reason.code,
                    reason_message=outcome.reason.message,
                )
                # Rebuild as BLOCK. The ID stays the same (idempotent).
                return alert.model_copy(
                    update={
                        "verdict": VERDICT_BLOCK,
                        "priority": 3,  # block is informational
                        "halt_reason": outcome.reason.code,
                        "blocked_by": (*alert.blocked_by, outcome.reason.code),
                    }
                )
        return alert


def default_chain() -> GateChain:
    """Default gate chain used in production.

    The compliance (FTC) gate runs first so a deceptive-advertising ENTER is
    blocked before the quant gates even look at it.
    """
    return GateChain(
        [compliance_ftc_gate(), margin_floor_gate, loss_probability_gate, confidence_gate]
    )


__all__: Final = [
    "GateChain",
    "GateOutcome",
    "RiskGate",
    "compliance_ftc_gate",
    "confidence_gate",
    "default_chain",
    "loss_probability_gate",
    "margin_floor_gate",
    "tenant_blocklist_gate",
]
