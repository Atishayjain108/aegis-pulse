"""
COMPLIANCE agent — mandatory legal / regulatory gate.

Verdict computation is delegated to the Phase 8 ``aegis.comply`` engine:
a deterministic, network-free, multi-jurisdiction rule engine with trademark
screening and counterfeit detection.  If Phase 8 is not installed the node
falls back to the legacy ``compliance_check`` tool (regex-only).

Verdict mapping (fail-closed):
    CLEAR  → PROCEED
    FLAG   → HOLD / escalate
    BLOCK  → BLOCK
    error  → HOLD (confidence 0.30) — gate must never fail open

The HEDGE and RED_TEAM agents both treat ``compliance_passed=False`` as a hard
veto. The supervisor uses ``compliance_flags`` for the alert payload.

LLM augmentation is fail-closed: the model may only *append* reasoning or
escalate a verdict — it cannot clear a blocked item.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from ..tools.compliance_check import check as compliance_check
from .base import AgentNode

if TYPE_CHECKING:
    from ..state import GraphState

_log = structlog.get_logger("aegis.agents.nodes.compliance")

# Phase 8 bridge (optional — degrades gracefully to legacy tool when absent)
try:
    from aegis.comply.bridge.agents_bridge import evaluate_for_compliance_node as _p8_evaluate

    _PHASE8_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _PHASE8_AVAILABLE = False
    _p8_evaluate = None  # type: ignore[assignment]

# Phase 2 verdict vocabulary → AgentVerdict
_P2_VERDICT_MAP: dict[str, AgentVerdict] = {
    "proceed": AgentVerdict.PROCEED,
    "hold": AgentVerdict.HOLD,
    "escalate": AgentVerdict.HOLD,   # Phase 8 uses "escalate"; Phase 2 calls it HOLD
    "block": AgentVerdict.BLOCK,
}

# Legacy tool verdict map (fallback path)
_TOOL_VERDICT_MAP: dict[str, AgentVerdict] = {
    "block": AgentVerdict.BLOCK,
    "hold": AgentVerdict.HOLD,
    "proceed": AgentVerdict.PROCEED,
}


class ComplianceAgent(AgentNode):
    """Compliance: mandatory regulatory gate."""

    name = "compliance"

    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        if _PHASE8_AVAILABLE and _p8_evaluate is not None:
            return self._decide_via_phase8(candidate, state)
        return await self._decide_legacy(candidate, state)

    # ------------------------------------------------------------------
    # Phase 8 path (deterministic multi-jurisdiction engine)
    # ------------------------------------------------------------------

    def _decide_via_phase8(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        """Run the Phase 8 compliance engine (sync, network-free)."""
        detected_price: float | None = None
        supplier = state.get("sourcer_supplier")
        if isinstance(supplier, dict):
            uc = supplier.get("unit_cost")
            if isinstance(uc, int | float) and uc > 0:
                detected_price = float(uc) * 4.0

        payload: dict[str, Any] = candidate.model_dump()
        if detected_price is not None:
            payload["price"] = detected_price

        decision_dict = _p8_evaluate(payload)  # type: ignore[call-arg]

        verdict = _P2_VERDICT_MAP.get(str(decision_dict.get("verdict", "hold")), AgentVerdict.HOLD)
        score = float(decision_dict.get("score", 0.5))
        confidence = float(decision_dict.get("confidence", 0.75))
        reasoning = str(decision_dict.get("reasoning", ""))

        comply = decision_dict.get("compliance", {})
        details: dict[str, Any] = {
            "compliance_passed": verdict is AgentVerdict.PROCEED,
            "compliance_flags": comply.get("blocking_reasons", []),
            "remedy": comply.get("remediation", []),
            "rule_ids": comply.get("rule_ids", []),
            "trademarks": comply.get("trademarks", []),
            "counterfeit": comply.get("counterfeit", []),
            "risk_score": comply.get("risk_score", 0.0),
            "content_id": comply.get("content_id", ""),
            "engine_version": comply.get("engine_version", ""),
            "phase8": True,
        }

        return AgentDecision(
            agent=self.name,
            trend_id=candidate.trend_id,
            correlation_id=candidate.correlation_id,
            verdict=verdict,
            score=score,
            confidence=confidence,
            reasoning=reasoning,
            details=details,
        )

    # ------------------------------------------------------------------
    # Legacy path (regex-only compliance_check tool)
    # ------------------------------------------------------------------

    async def _decide_legacy(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        detected_price: float | None = None
        supplier = state.get("sourcer_supplier")
        if isinstance(supplier, dict):
            uc = supplier.get("unit_cost")
            if isinstance(uc, int | float) and uc > 0:
                detected_price = float(uc) * 4.0

        result = await compliance_check(
            title=candidate.title,
            summary=candidate.summary,
            representative_text=candidate.representative_text,
            detected_price=detected_price,
        )

        if not result.ok or not isinstance(result.data, dict):
            return AgentDecision(
                agent=self.name,
                trend_id=candidate.trend_id,
                correlation_id=candidate.correlation_id,
                verdict=AgentVerdict.HOLD,
                score=0.0,
                confidence=0.3,
                reasoning="compliance_check tool failed; failing safe",
                details={
                    "compliance_passed": False,
                    "compliance_flags": ["tool_error"],
                    "tool_error": True,
                },
            )

        tool_verdict = str(result.data.get("verdict", "hold"))
        flags: list[str] = list(result.data.get("flags", []) or [])
        counterfeit_risk = bool(result.data.get("counterfeit_risk", False))
        reason = str(result.data.get("reason", "no flags"))

        verdict = _TOOL_VERDICT_MAP.get(tool_verdict, AgentVerdict.HOLD)
        compliance_passed = verdict is AgentVerdict.PROCEED
        score = 1.0 if verdict is AgentVerdict.PROCEED else (0.5 if verdict is AgentVerdict.HOLD else 0.0)
        confidence = 0.85

        details: dict[str, Any] = {
            "compliance_passed": compliance_passed,
            "compliance_flags": flags,
            "tool_verdict": tool_verdict,
            "tool_reason": reason,
            "trademark_hits": list(result.data.get("trademark_hits", []) or []),
            "regulated_hits": list(result.data.get("regulated_hits", []) or []),
            "due_diligence_hits": list(result.data.get("due_diligence_hits", []) or []),
            "counterfeit_risk": counterfeit_risk,
        }

        if counterfeit_risk:
            verdict = AgentVerdict.BLOCK
            score = 0.0
            details["compliance_passed"] = False
            details["counterfeit_block"] = True

        reasoning_parts = [f"verdict={tool_verdict}", f"reason={reason}"]
        if flags:
            reasoning_parts.append(f"flags={','.join(flags[:5])}")
        if counterfeit_risk:
            reasoning_parts.append("counterfeit_risk=True")
        reasoning = "; ".join(reasoning_parts)

        return AgentDecision(
            agent=self.name,
            trend_id=candidate.trend_id,
            correlation_id=candidate.correlation_id,
            verdict=verdict,
            score=score,
            confidence=confidence,
            reasoning=reasoning,
            details=details,
        )

    # ------------------------------------------------------------------
    # LLM augmentation (fail-closed — may only escalate, never clear)
    # ------------------------------------------------------------------

    async def _augment_with_llm(
        self,
        candidate: TrendCandidate,
        state: GraphState,
        heuristic: AgentDecision,
    ) -> AgentDecision | None:
        if heuristic.verdict is not AgentVerdict.HOLD:
            return None
        try:
            user_text, _ver = prompts.render(
                "compliance",
                title=candidate.title,
                summary=(candidate.summary or "")[:400],
                flags=", ".join(heuristic.details.get("compliance_flags", [])[:8]),
                tool_reason=heuristic.details.get("tool_reason", heuristic.reasoning),
            )
        except Exception as exc:
            _log.debug("compliance.llm_input_build_failed", error=str(exc))
            return None
        system_text = (
            "You are COMPLIANCE. Provide an operator-friendly note. "
            'Reply JSON only: {"reasoning": "<≤80 words>", "confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        augmented = self._llm_apply(heuristic, resp)
        if augmented is not None and augmented.verdict is not heuristic.verdict:
            return heuristic
        return augmented

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        return {
            "compliance_passed": bool(decision.details.get("compliance_passed", False)),
            "compliance_flags": list(decision.details.get("compliance_flags", []) or []),
        }
