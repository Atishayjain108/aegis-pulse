"""
SENTINEL agent — saturation detection + exit timing.

The mirror image of SCOUT. Where SCOUT looks for trends *taking off*,
SENTINEL looks for trends *peaking*: high accumulated signal_count,
declining velocity, fading novelty.

Saturation index (in [0, 1]):
    sat = 0.40 * decay_score
        + 0.25 * volume_maturity
        + 0.20 * (1 - novelty)
        + 0.15 * coordination_risk

Where:
  decay_score    = 1 - (rate_1h / rate_6h_per_h), clamped to [0,1].
                   1.0 means current 1h velocity is far below the
                   6h average — i.e., decelerating.
  volume_maturity= log1p(signal_count) / log1p(SAT_VOLUME).
                   Capped at 1.0 once we hit 1000+ signals.
  (1 - novelty)  = older / well-trodden territory.
  coordination_risk: bot-coordinated trends collapse fast.

Verdicts:
    sat >= 0.70  → PROCEED (recommend exit) with `recommended_exit=True`
    sat >= 0.45  → HOLD    (warning; tighten stop-loss)
    else         → BLOCK   (no exit signal — let it run)

Note the inverted-feel of "BLOCK" here: SENTINEL's job is to *raise*
exit alerts. BLOCK means "don't exit yet". The supervisor maps this
correctly when assembling the final verdict — SENTINEL never gates
the pipeline forward; it advises on exits.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import structlog

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from ..tools.velocity import classify
from .base import AgentNode

if TYPE_CHECKING:
    from ..state import GraphState

_log = structlog.get_logger("aegis.agents.nodes.sentinel")

_SAT_VOLUME_REFERENCE = 1000  # signals → maturity 1.0
_PROCEED_THRESHOLD = 0.70  # exit recommended
_HOLD_THRESHOLD = 0.45  # warning


class SentinelAgent(AgentNode):
    """Sentinel: saturation / exit-timing detection."""

    name = "sentinel"

    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        v_result = await classify(
            velocity_1h=candidate.velocity_1h,
            velocity_6h=candidate.velocity_6h,
            velocity_24h=candidate.velocity_24h,
        )
        if v_result.ok and isinstance(v_result.data, dict):
            rate_1h = float(v_result.data.get("rate_1h", 0.0))
            rate_6h_per_h = float(v_result.data.get("rate_6h_per_h", 0.0))
        else:
            rate_1h = max(0.0, candidate.velocity_1h)
            rate_6h_per_h = max(0.0, candidate.velocity_6h) / 6.0

        # Decay score: how much has the 1h rate fallen below the 6h
        # baseline? Strongly positive → decelerating.
        if rate_6h_per_h <= 0.001:
            # No meaningful baseline → no decay signal.
            decay_score = 0.0
        else:
            ratio = rate_1h / rate_6h_per_h
            decay_score = max(0.0, min(1.0, 1.0 - ratio))

        sc = max(0, int(candidate.signal_count))
        if _SAT_VOLUME_REFERENCE > 0:
            volume_maturity = math.log1p(sc) / math.log1p(_SAT_VOLUME_REFERENCE)
        else:  # pragma: no cover - guard
            volume_maturity = 0.0
        volume_maturity = max(0.0, min(1.0, volume_maturity))

        novelty_decay = 1.0 - max(0.0, min(1.0, candidate.novelty))
        coord = max(0.0, min(1.0, candidate.coordination_risk))

        sat = 0.40 * decay_score + 0.25 * volume_maturity + 0.20 * novelty_decay + 0.15 * coord
        sat = max(0.0, min(1.0, sat))

        if sat >= _PROCEED_THRESHOLD:
            verdict = AgentVerdict.PROCEED
            recommended_exit = True
        elif sat >= _HOLD_THRESHOLD:
            verdict = AgentVerdict.HOLD
            recommended_exit = False
        else:
            # Not saturated → "block" the exit signal (i.e., do not exit).
            verdict = AgentVerdict.BLOCK
            recommended_exit = False

        # Confidence rises with signal_count (we need enough data to
        # detect a decay reliably).
        confidence = 0.3 + 0.7 * min(1.0, sc / 200.0)
        confidence = max(0.0, min(1.0, confidence))

        reasoning = (
            f"saturation={sat:.2f} decay={decay_score:.2f} "
            f"volume_maturity={volume_maturity:.2f} novelty_decay={novelty_decay:.2f} "
            f"coord_risk={coord:.2f}; recommended_exit={recommended_exit}"
        )

        details: dict[str, Any] = {
            "saturation_index": sat,
            "decay_score": decay_score,
            "volume_maturity": volume_maturity,
            "novelty_decay": novelty_decay,
            "coordination_risk": coord,
            "recommended_exit": recommended_exit,
        }

        return AgentDecision(
            agent=self.name,
            trend_id=candidate.trend_id,
            correlation_id=candidate.correlation_id,
            verdict=verdict,
            score=sat,
            confidence=confidence,
            reasoning=reasoning,
            details=details,
        )

    async def _augment_with_phase3(
        self,
        candidate: TrendCandidate,
        state: GraphState,
        heuristic: AgentDecision,
    ) -> AgentDecision | None:
        """Enrich with Phase 3 decline-axis inference (p_decline, 6h horizon).

        Phase 3's p_decline replaces the heuristic saturation index as the
        score. The verdict is always kept from the heuristic floor.
        """
        try:
            from aegis.agents_phase3_glue.bridge import enrich_sentinel_decision

            enriched = await enrich_sentinel_decision(
                {
                    "trend_id": candidate.trend_id,
                    "tenant_id": state.get("tenant_id", "default"),
                    "signals": state.get("signals"),
                    "feature_window": state.get("feature_window"),
                },
                primary_horizon=6,
            )
        except Exception as exc:
            _log.warning("sentinel.phase3_skipped", reason=type(exc).__name__)
            return None

        p3 = enriched.get("phase3_decision")
        if p3 is None:
            return None

        # Skip blending if Phase 3 itself failed (inference error, no signals).
        halt_reasons: list[str] = p3.get("halt_reasons", [])
        if any(r.startswith("phase3_inference_failed") for r in halt_reasons):
            return None

        return self._blend_phase3(heuristic, p3)

    def _blend_phase3(
        self,
        heuristic: AgentDecision,
        p3: dict[str, Any],
    ) -> AgentDecision:
        """Merge Phase 3 decline-axis result into the heuristic decision.

        Score ← Phase 3 p_decline (better calibrated decline signal).
        Confidence ← 40% heuristic + 60% Phase 3.
        Verdict stays from heuristic.
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
        # Only ask the LLM when saturation is borderline (HOLD).
        if heuristic.verdict is not AgentVerdict.HOLD:
            return None
        try:
            user_text, _ver = prompts.render(
                "sentinel",
                title=candidate.title,
                summary=(candidate.summary or "")[:400],
                saturation_index=round(heuristic.details.get("saturation_index", 0.0), 3),
                decay_score=round(heuristic.details.get("decay_score", 0.0), 3),
                volume_maturity=round(heuristic.details.get("volume_maturity", 0.0), 3),
                signal_count=candidate.signal_count,
            )
        except Exception:
            return None
        system_text = (
            "You are SENTINEL, the saturation detector. Reply JSON only: "
            '{"reasoning": "<≤80 words>", "confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        return self._llm_apply(heuristic, resp)

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        return {
            "sentinel_saturation": float(decision.details.get("saturation_index", 0.0)),
            "sentinel_recommended_exit": bool(decision.details.get("recommended_exit", False)),
        }

    def _merge_partial(
        self,
        decision: AgentDecision,
        state: GraphState,
    ) -> dict[str, Any]:
        # SENTINEL never adds itself to `blocked_by` — its BLOCK verdict
        # means "don't exit yet", not "halt the pipeline". We override
        # the base behavior accordingly.
        partial: dict[str, Any] = {"decisions": [decision]}
        partial.update(self._extra_state(decision))
        return partial
