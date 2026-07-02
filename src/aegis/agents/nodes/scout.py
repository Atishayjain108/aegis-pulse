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

import structlog

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from ..tools.velocity import classify
from .base import AgentNode

if TYPE_CHECKING:
    from ..state import GraphState

_log = structlog.get_logger("aegis.agents.nodes.scout")

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

        # Buyer-demand signal (Google Trends). The features above measure how
        # loudly a trend is being *talked about* (media/social arrival rate);
        # this is the one keyless source of how much people actually *want to
        # buy* it. It is a reward-only nudge (max +0.12) so genuine search
        # demand can push a borderline HOLD into PROCEED, but its absence never
        # sinks the deterministic floor. Fail-open: None → no adjustment.
        demand = await self._demand_factor(candidate)
        demand_bonus = 0.12 * demand if demand is not None else 0.0
        score = max(0.0, min(1.0, score + demand_bonus))

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
        if demand is not None:
            reasoning_parts.append(f"buyer_demand={demand:.2f} (+{demand_bonus:.2f})")
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
                "buyer_demand": demand,
                "demand_bonus": demand_bonus,
            },
        )

    async def _demand_factor(self, candidate: TrendCandidate) -> float | None:
        """Google Trends buyer-demand factor for this candidate, or None.

        Isolated here (and fail-open) so the heuristic stays fully deterministic
        and offline-safe when Trends is disabled/unreachable — see
        ``aegis.agents.nodes._demand``.
        """
        # Prefer a value stamped at harvest time (see _demand.stamp_demand):
        # fetching Google Trends once per query during harvest avoids every
        # SCOUT invocation re-querying and getting 429'd. The stamped value is
        # authoritative — a sentinel of None under the key means "fetched, no
        # signal" so we still skip the live call.
        if isinstance(candidate.metadata, dict) and "buyer_demand" in candidate.metadata:
            stamped = candidate.metadata.get("buyer_demand")
            return float(stamped) if isinstance(stamped, int | float) else None
        try:
            from ._demand import demand_factor

            return await demand_factor(candidate.title)
        except Exception as exc:  # pragma: no cover - defensive
            _log.debug("scout.demand_skipped", reason=type(exc).__name__)
            return None

    async def _augment_with_phase3(
        self,
        candidate: TrendCandidate,
        state: GraphState,
        heuristic: AgentDecision,
    ) -> AgentDecision | None:
        """Enrich the heuristic decision with Phase 3 temporal/relational inference.

        Phase 3's p_breakout replaces the heuristic score (it's a better
        calibrated breakout signal). Confidence is a weighted blend. The
        verdict is always kept from the heuristic floor.
        """
        try:
            from aegis.agents_phase3_glue.bridge import enrich_scout_decision

            enriched = await enrich_scout_decision(
                {
                    "trend_id": candidate.trend_id,
                    "tenant_id": state.get("tenant_id", "default"),
                    "signals": state.get("signals"),
                    "feature_window": state.get("feature_window"),
                },
                primary_horizon=24,
            )
        except Exception as exc:
            _log.warning("scout.phase3_skipped", reason=type(exc).__name__)
            return None

        p3 = enriched.get("phase3_decision")
        if p3 is None:
            return None

        # Skip blending if Phase 3 itself failed (inference error, no signals).
        halt_reasons: list[str] = p3.get("halt_reasons", [])
        if any(r.startswith("phase3_inference_failed") for r in halt_reasons):
            return None

        # Skip blending if Phase 3 was a no-op (no signal/feature data to run
        # on). The bridge returns a score=0.0 placeholder in that case; blending
        # it would wrongly clobber the heuristic floor. Doctrine: a skipped
        # Phase 3 must never lower the deterministic score.
        if str(p3.get("reasoning", "")).startswith("phase3_skipped"):
            return None

        return self._blend_phase3(heuristic, p3)

    def _blend_phase3(
        self,
        heuristic: AgentDecision,
        p3: dict[str, Any],
    ) -> AgentDecision:
        """Merge Phase 3 result into the heuristic decision.

        Contract:
        - Verdict stays from heuristic (doctrine: Phase 3 cannot flip verdicts).
        - Score ← Phase 3 p_breakout (better calibrated breakout estimate).
        - Confidence ← weighted blend (40% heuristic, 60% Phase 3).
        - Reasoning ← heuristic + Phase 3 summary appended.
        - Details ← heuristic details + "phase3" sidecar.
        """
        p3_score = float(p3.get("score", heuristic.score))
        p3_conf = float(p3.get("confidence", heuristic.confidence))
        blended_conf = max(0.0, min(1.0, 0.4 * heuristic.confidence + 0.6 * p3_conf))

        p3_reasoning = str(p3.get("reasoning", ""))[:600]
        new_reasoning = f"{heuristic.reasoning}\n[Phase3] {p3_reasoning}".strip()[:4000]

        new_details = {
            **heuristic.details,
            "phase3": {
                "verdict": p3.get("verdict"),
                "score": p3_score,
                "model_id": p3.get("model_id"),
                "correlation_id": p3.get("correlation_id"),
                "halt_reasons": p3.get("halt_reasons", []),
            },
        }
        return heuristic.model_copy(
            update={
                "score": p3_score,
                "confidence": blended_conf,
                "reasoning": new_reasoning,
                "details": new_details,
            }
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
                swarm_context=state.get("swarm_context"),
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
