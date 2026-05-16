"""
NARRATIVE agent — tracks the *story* around each trend.

Trend value isn't just velocity; it's how durable the story is. A
high-velocity trend with a thin narrative ("this object goes viral
for one week then dies") is fundamentally different from one with a
thick narrative ("this is the new toothbrush — people understand
why and recommend it organically").

The heuristic combines:

    sentiment_intensity = |sentiment|         (people *care* — pos or neg)
    novelty             = candidate.novelty   (genuinely new framing)
    diversity           = unique_authors / signal_count
                                              (organic, not a single creator)
    title_signal        = some narrative cues are right in the title
                          ("life-changing", "obsessed", "everyone's
                          talking about", "you have to try")

Score:
    narrative_score = 0.35 * sentiment_intensity
                    + 0.25 * novelty
                    + 0.20 * diversity_bounded
                    + 0.15 * title_cue_score
                    + 0.05 * cross_platform_bonus

Verdicts (advisory):
    score >= 0.65 → PROCEED  (strong narrative — lean in)
    score >= 0.40 → HOLD     (lukewarm — watch)
    else          → BLOCK    (no story — info only, do not amplify)

Note: NARRATIVE is advisory like SENTINEL/GEO_ARBITRAGE. It never
adds itself to `blocked_by`.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from .base import AgentNode

if TYPE_CHECKING:
    from ..state import GraphState

# Lightweight narrative-cue lexicon. These are intentionally generic;
# domain-specific tuning happens via the LLM augmentation layer.
_NARRATIVE_CUES: tuple[str, ...] = (
    r"\blife[-\s]?changing\b",
    r"\bgame[-\s]?changer\b",
    r"\bobsessed\b",
    r"\bcan'?t\s+(?:stop|believe|live\s+without)\b",
    r"\beveryone'?s?\s+(?:talking|using|buying)\b",
    r"\byou\s+(?:have\s+to|need\s+to|gotta)\s+(?:try|see|get)\b",
    r"\bviral\b",
    r"\btrending\b",
    r"\bblowing\s+up\b",
    r"\btake\s+over\b",
    r"\binsane\b",
    r"\bunbelievable\b",
    r"\bsold\s+out\b",
    r"\bwait[-\s]?list\b",
    r"\bbest\s+(?:thing|investment|purchase)\b",
)
_CUE_RX = tuple(re.compile(p, re.IGNORECASE) for p in _NARRATIVE_CUES)


def _title_cue_score(*texts: str) -> float:
    blob = " ".join(t for t in texts if t)
    if not blob:
        return 0.0
    hits = sum(1 for rx in _CUE_RX if rx.search(blob))
    # 1 cue → 0.4, 2 → 0.7, 3+ → 1.0. Saturating.
    if hits == 0:
        return 0.0
    if hits == 1:
        return 0.40
    if hits == 2:
        return 0.70
    return 1.0


_PROCEED_THRESHOLD = 0.65
_HOLD_THRESHOLD = 0.40


class NarrativeAgent(AgentNode):
    """Narrative: story-strength + ad-copy seed."""

    name = "narrative"

    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        sentiment_intensity = abs(max(-1.0, min(1.0, candidate.sentiment)))
        novelty = max(0.0, min(1.0, candidate.novelty))

        if candidate.signal_count > 0:
            diversity = candidate.unique_authors / candidate.signal_count
        else:
            diversity = 0.0
        # Bound to [0,1] but reward only up to 0.5 (anything >50%
        # diversity is already organic — diminishing returns).
        diversity_bounded = min(1.0, diversity / 0.5)

        cue_score = _title_cue_score(
            candidate.title,
            candidate.summary,
            candidate.representative_text[:500],
        )

        # Cross-platform bonus: narrative durability often correlates
        # with cross-platform spread (more story surfaces).
        breadth = min(5, len(candidate.platforms or []))
        cross_platform_bonus = breadth / 5.0

        score = (
            0.35 * sentiment_intensity
            + 0.25 * novelty
            + 0.20 * diversity_bounded
            + 0.15 * cue_score
            + 0.05 * cross_platform_bonus
        )
        score = max(0.0, min(1.0, score))

        if score >= _PROCEED_THRESHOLD:
            verdict = AgentVerdict.PROCEED
        elif score >= _HOLD_THRESHOLD:
            verdict = AgentVerdict.HOLD
        else:
            verdict = AgentVerdict.BLOCK

        confidence = (
            0.4 + 0.5 * min(1.0, candidate.signal_count / 30.0) + 0.1 * cross_platform_bonus
        )
        confidence = max(0.0, min(1.0, confidence))

        reasoning = (
            f"sentiment_intensity={sentiment_intensity:.2f} novelty={novelty:.2f} "
            f"diversity={diversity:.2f} cue_score={cue_score:.2f} "
            f"breadth={breadth}/5"
        )

        details: dict[str, Any] = {
            "narrative_score": score,
            "sentiment_intensity": sentiment_intensity,
            "novelty": novelty,
            "diversity": diversity,
            "title_cue_score": cue_score,
            "cross_platform_bonus": cross_platform_bonus,
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
        # Narrative is the ONE place where LLM augmentation runs even
        # on PROCEED — because the LLM can produce a usable ad-copy
        # angle that no heuristic can synthesize. We still don't let
        # it flip the verdict; it just enriches `details["llm"]`.
        if heuristic.verdict is AgentVerdict.BLOCK:
            return None  # don't burn tokens on dead trends

        try:
            user_text, _ver = prompts.render(
                "narrative",
                title=candidate.title,
                summary=(candidate.summary or "")[:600],
                representative_text=(candidate.representative_text or "")[:800],
                narrative_score=round(heuristic.details.get("narrative_score", 0.0), 3),
            )
        except Exception:
            return None
        system_text = (
            "You are NARRATIVE. Reply JSON only: "
            '{"reasoning": "<≤80 words; identify the story arc>", '
            '"confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        return self._llm_apply(heuristic, resp)

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        return {"narrative_score": float(decision.details.get("narrative_score", 0.0))}

    def _merge_partial(
        self,
        decision: AgentDecision,
        state: GraphState,
    ) -> dict[str, Any]:
        # Advisory only — never adds to blocked_by.
        partial: dict[str, Any] = {"decisions": [decision]}
        partial.update(self._extra_state(decision))
        return partial
