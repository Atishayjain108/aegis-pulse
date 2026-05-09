"""
COMPLIANCE agent — mandatory legal / regulatory gate.

This is a *gate* agent: every candidate must pass through it before
the supervisor permits a P0 alert. The heuristic delegates to the
`compliance_check` tool, which runs three regex tables:

    * trademark fingerprints  (Nike, Apple, Disney, Marvel, ...)
    * regulated claims        (FTC: "guaranteed earnings", FDA: drug claims)
    * due-diligence categories (children, cosmetics, electronics, ...)

Verdict mapping:
    trademark hit              → BLOCK  (counterfeit risk)
    regulated claim            → BLOCK  (legal exposure)
    due-diligence category     → HOLD   (paperwork required)
    clean                      → PROCEED

The HEDGE and RED_TEAM agents both treat `compliance_passed=False`
as a hard veto. The supervisor uses `compliance_flags` for the alert
payload so downstream consumers know exactly what tripped.

We deliberately don't run an LLM here. Compliance is a domain where
"the model said it's probably fine" is far worse than a regex false
positive — so we never let an LLM weaken a flag. We do, however,
optionally let the LLM *append a human-readable summary* on HOLD
verdicts (so an operator reviewing flags has prose context).

Author: AEGIS Pulse core team
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from ..tools.compliance_check import check as compliance_check
from .base import AgentNode

if TYPE_CHECKING:
    from ..state import GraphState

# Map of tool verdict → agent verdict.
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
        # Pull a price hint from the auditor / sourcer if present.
        # `detected_price` lets the counterfeit-risk heuristic fire
        # (low price + brand mention = likely counterfeit).
        detected_price: float | None = None
        supplier = state.get("sourcer_supplier")
        if isinstance(supplier, dict):
            uc = supplier.get("unit_cost")
            if isinstance(uc, int | float) and uc > 0:
                # Synthesised supplier estimates retail at ~4× cost.
                detected_price = float(uc) * 4.0

        result = await compliance_check(
            title=candidate.title,
            summary=candidate.summary,
            representative_text=candidate.representative_text,
            detected_price=detected_price,
        )

        if not result.ok or not isinstance(result.data, dict):
            # Tool failure → fail safe: HOLD with a tool-error flag.
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

        # Score: 1.0 means "fully clean", 0.0 means "block-level violation".
        if verdict is AgentVerdict.PROCEED:
            score = 1.0
        elif verdict is AgentVerdict.HOLD:
            score = 0.5
        else:
            score = 0.0

        # Confidence: regex matching is high-confidence by nature; we
        # trust hits more than misses (false negatives are likely on
        # obfuscated brand names like "Ⓝike"). 0.85 fixed.
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
            # Force BLOCK on counterfeit even if the verdict was HOLD.
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

    async def _augment_with_llm(
        self,
        candidate: TrendCandidate,
        state: GraphState,
        heuristic: AgentDecision,
    ) -> AgentDecision | None:
        # LLM gets to add human-readable context ONLY on HOLD. We
        # never let the LLM downgrade a BLOCK or upgrade a HOLD to
        # PROCEED — that's the whole point of having a deterministic
        # compliance gate.
        if heuristic.verdict is not AgentVerdict.HOLD:
            return None
        try:
            user_text, _ver = prompts.render(
                "compliance",
                title=candidate.title,
                summary=(candidate.summary or "")[:400],
                flags=", ".join(heuristic.details.get("compliance_flags", [])[:8]),
                tool_reason=heuristic.details.get("tool_reason", ""),
            )
        except Exception:
            return None
        system_text = (
            "You are COMPLIANCE. Provide an operator-friendly note. "
            'Reply JSON only: {"reasoning": "<≤80 words>", "confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        augmented = self._llm_apply(heuristic, resp)
        # Defensive: never let the LLM accidentally invert verdict.
        if augmented is not None and augmented.verdict is not heuristic.verdict:
            return heuristic
        return augmented

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        return {
            "compliance_passed": bool(decision.details.get("compliance_passed", False)),
            "compliance_flags": list(decision.details.get("compliance_flags", []) or []),
        }
