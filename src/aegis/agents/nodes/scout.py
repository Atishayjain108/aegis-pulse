"""
SCOUT agent — trend discovery + initial viability scoring.

Heuristic combines (in this order of importance):

  1. Velocity-class breakout score      [weight 0.40]
  2. Commercial intent                  [weight 0.25]
  3. Novelty                            [weight 0.15]
  4. Sentiment magnitude (|x|)          [weight 0.10]
  5. Cross-platform breadth             [weight 0.10]

Subtractions:
  * coordination_risk * 0.30 (penalize obvious bot-coordinated trends)
  * If unique_authors / signal_count < 0.10 → astroturf penalty 0.20

Verdict mapping:
  score >= 0.70  → PROCEED
  score >= 0.45  → HOLD (more data needed)
  else           → BLOCK (do not advance)

The SCOUT score is also written to `state["scout_score"]` so
downstream agents can read it without traversing the decisions list.

Author: AEGIS Pulse core team
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from ..tools.velocity import classify
from .base import AgentNode

if TYPE_CHECKING:
    from ..state import GraphState

_PROCEED_THRESHOLD = 0.70
_HOLD_THRESHOLD = 0.45


class ScoutAgent(AgentNode):
    name = "scout"

    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        # Compute the velocity class via the deterministic tool.
        v_result = await classify(
            velocity_1h=candidate.velocity_1h,
            velocity_6h=candidate.velocity_6h,
            velocity_24h=candidate.velocity_24h,
        )
        breakout = float(v_result.data["breakout_score"]) if v_result.ok else 0.0
        velocity_class = v_result.data["class"] if v_result.ok else "flat"

        # Commercial intent and novelty are already in [0,1].
        ci = max(0.0, min(1.0, candidate.commercial_intent))
        nov = max(0.0, min(1.0, candidate.novelty))
        sent_mag = abs(max(-1.0, min(1.0, candidate.sentiment)))

        # Breadth: number of unique platforms, capped at 5.
        breadth_raw = min(5, len(candidate.platforms or []))
        breadth = breadth_raw / 5.0

        # Author diversity ratio. Astroturf if < 10%.
        if candidate.signal_count > 0:
            diversity = candidate.unique_authors / candidate.signal_count
        else:
            diversity = 0.0
        astroturf_penalty = 0.20 if (diversity < 0.10 and candidate.signal_count >= 20) else 0.0

        coord_penalty = 0.30 * max(0.0, min(1.0, candidate.coordination_risk))

        score = (
            0.40 * breakout
            + 0.25 * ci
            + 0.15 * nov
            + 0.10 * sent_mag
            + 0.10 * breadth
            - coord_penalty
            - astroturf_penalty
        )
        score = max(0.0, min(1.0, score))

        if score >= _PROCEED_THRESHOLD:
            verdict = AgentVerdict.PROCEED
        elif score >= _HOLD_THRESHOLD:
            verdict = AgentVerdict.HOLD
        else:
            verdict = AgentVerdict.BLOCK

        # Confidence is bounded by signal volume — 50 signals = 1.0.
        confidence = min(1.0, candidate.signal_count / 50.0) * 0.7 + 0.3

        reasoning_parts = [
            f"velocity_class={velocity_class} breakout={breakout:.2f}",
            f"commercial_intent={ci:.2f}",
            f"novelty={nov:.2f}",
            f"breadth={breadth_raw}/5",
            f"sentiment_mag={sent_mag:.2f}",
            f"author_diversity={diversity:.2f}",
        ]
        if coord_penalty:
            reasoning_parts.append(f"coord_penalty=-{coord_penalty:.2f}")
        if astroturf_penalty:
            reasoning_parts.append(f"astroturf_penalty=-{astroturf_penalty:.2f}")
        reasoning = "; ".join(reasoning_parts)

        return AgentDecision(
            agent=self.name,
            trend_id=candidate.trend_id,
            correlation_id=candidate.correlation_id,
            verdict=verdict,
            score=score,
            confidence=confidence,
            reasoning=reasoning,
            details={
                "velocity_class": velocity_class,
                "breakout_score": breakout,
                "diversity_ratio": diversity,
                "astroturf_penalty": astroturf_penalty,
                "coordination_penalty": coord_penalty,
            },
        )

    async def _augment_with_llm(
        self,
        candidate: TrendCandidate,
        state: GraphState,
        heuristic: AgentDecision,
    ) -> AgentDecision | None:
        # Only consult LLM when the heuristic is on the fence (HOLD).
        # No point burning tokens to second-guess clear PROCEED/BLOCKs.
        if heuristic.verdict is not AgentVerdict.HOLD:
            return None

        try:
            user_text, _ver = prompts.render(
                "scout",
                title=candidate.title,
                summary=candidate.summary[:600],
                representative_text=candidate.representative_text[:800],
                platforms=", ".join(candidate.platforms or []),
                signal_count=candidate.signal_count,
                heuristic_score=round(heuristic.score, 3),
                breakout_class=heuristic.details.get("velocity_class", "flat"),
            )
        except Exception:
            return None

        system_text = (
            "You are SCOUT, the trend discovery agent. Reply with JSON only:\n"
            '{"reasoning": "<≤80 words>", "confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        return self._llm_apply(heuristic, resp)

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        return {
            "scout_score": decision.score,
            "scout_verdict": decision.verdict,
        }
