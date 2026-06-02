"""
RED_TEAM agent — adversarial reviewer.

Its single job: TRY TO FALSIFY the SCOUT thesis. RED_TEAM only fires
on potential P0 (breakout) alerts and exists to prevent a confident
SCOUT from steamrolling into action when there are obvious red flags.

Falsifiers (each adds a hit to the falsifier list):

    F1: coordination_risk  >= 0.50            (likely bot-driven)
    F2: author_diversity   <  0.10  AND signal_count >= 20
                                              (astroturf signature)
    F3: rate_24h_per_h     <  V_THRESHOLD_LOW * 0.25
                                              (no sustained 24h trend)
    F4: novelty            <  0.15            (probably already done)
    F5: coordination_risk  +  (1 - diversity) > 1.20
                                              (composite suspicion)
    F6: only one platform   AND  signal_count > 50
                                              (single-platform astroturf)
    F7: scout_score itself < 0.55             (SCOUT was on the fence)
    F8: compliance flagged anything             (regulatory risk)

Verdict policy:

    >= 2 falsifiers      → BLOCK   (red_team_passed=False)
    1 falsifier          → HOLD    (red_team_passed=True with warning)
    0 falsifiers         → PROCEED (red_team_passed=True)

RED_TEAM has unilateral veto power: a BLOCK here halts the pipeline
even if SCOUT was a 0.95 PROCEED. This is doctrinally correct — when
there's any reasonable doubt, the supervisor would rather miss the
trade than be wrong.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from ..tools.velocity import V_THRESHOLD_LOW, classify
from .base import AgentNode

if TYPE_CHECKING:
    from ..state import GraphState

_log = structlog.get_logger("aegis.agents.nodes.red_team")


class RedTeamAgent(AgentNode):
    """Red-Team: adversarial falsifier."""

    name = "red_team"

    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        falsifiers: list[str] = []

        coord = max(0.0, min(1.0, candidate.coordination_risk))
        if coord >= 0.50:
            falsifiers.append("F1:coordination_risk_high")

        if candidate.signal_count > 0:
            diversity = candidate.unique_authors / candidate.signal_count
        else:
            diversity = 0.0

        if diversity < 0.10 and candidate.signal_count >= 20:
            falsifiers.append("F2:astroturf_signature")

        # Check sustained 24h trend.
        v_result = await classify(
            velocity_1h=candidate.velocity_1h,
            velocity_6h=candidate.velocity_6h,
            velocity_24h=candidate.velocity_24h,
        )
        rate_24h_per_h = 0.0
        if v_result.ok and isinstance(v_result.data, dict):
            rate_24h_per_h = float(v_result.data.get("rate_24h_per_h", 0.0))
        if rate_24h_per_h < V_THRESHOLD_LOW * 0.25:
            falsifiers.append("F3:no_24h_sustain")

        if max(0.0, min(1.0, candidate.novelty)) < 0.15:
            falsifiers.append("F4:low_novelty")

        if coord + (1.0 - diversity) > 1.20:
            falsifiers.append("F5:composite_suspicion")

        platforms = candidate.platforms or []
        if len(platforms) <= 1 and candidate.signal_count > 50:
            falsifiers.append("F6:single_platform_astroturf")

        scout_score = float(state.get("scout_score", 0.0))
        if scout_score < 0.55:
            falsifiers.append("F7:scout_below_strong")

        if state.get("compliance_flags"):
            falsifiers.append("F8:compliance_flagged")

        if state.get("compliance_passed") is False:
            # Hard fail — explicit compliance failure is automatic veto.
            falsifiers.append("F8b:compliance_block")

        n_falsifiers = len(falsifiers)
        if n_falsifiers >= 2:
            verdict = AgentVerdict.BLOCK
            passed = False
        elif n_falsifiers == 1:
            verdict = AgentVerdict.HOLD
            passed = True  # passes with warning
        else:
            verdict = AgentVerdict.PROCEED
            passed = True

        # Score = 1.0 - normalized falsifier weight, capped at 0.0.
        score = max(0.0, 1.0 - n_falsifiers / 4.0)
        confidence = 0.6 + 0.4 * min(1.0, candidate.signal_count / 50.0)
        confidence = max(0.0, min(1.0, confidence))

        reasoning = f"falsifiers={n_falsifiers}: {', '.join(falsifiers) if falsifiers else 'none'}"

        details: dict[str, Any] = {
            "red_team_passed": passed,
            "red_team_falsifiers": list(falsifiers),
            "falsifier_count": n_falsifiers,
            "diversity_ratio": diversity,
            "coordination_risk": coord,
            "rate_24h_per_h": rate_24h_per_h,
            "scout_score_used": scout_score,
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

    async def _augment_with_llm(
        self,
        candidate: TrendCandidate,
        state: GraphState,
        heuristic: AgentDecision,
    ) -> AgentDecision | None:
        # Always consult LLM on HOLD (1 falsifier) — the LLM might
        # spot a 2nd qualitative falsifier the regex didn't catch.
        # Don't run on BLOCK (already failed) or clean PROCEED.
        if heuristic.verdict is not AgentVerdict.HOLD:
            return None
        try:
            user_text, _ver = prompts.render(
                "red_team",
                title=candidate.title,
                summary=(candidate.summary or "")[:600],
                falsifiers=", ".join(heuristic.details.get("red_team_falsifiers", [])),
                scout_score=heuristic.details.get("scout_score_used", 0.0),
            )
        except Exception as exc:
            _log.debug("red_team.llm_input_build_failed", error=str(exc))
            return None
        system_text = (
            "You are RED_TEAM. Hunt for additional falsifiers. Reply JSON only: "
            '{"reasoning": "<≤80 words listing the strongest doubt>", '
            '"confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        return self._llm_apply(heuristic, resp)

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        return {
            "red_team_passed": bool(decision.details.get("red_team_passed", False)),
            "red_team_falsifiers": list(decision.details.get("red_team_falsifiers", []) or []),
        }
