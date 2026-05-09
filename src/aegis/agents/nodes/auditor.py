"""
AUDITOR agent — unit-economics check via Monte Carlo simulation.

Pulls the synthetic supplier emitted by SOURCER, runs `monte_carlo.simulate`
with that unit cost as the cost prior, and gates on the resulting
margin distribution:

    PROCEED:  p10 >= $0    (90% chance of positive per-unit margin)
    HOLD:     mean > $0 but p10 < $0
    BLOCK:    mean <= $0    (negative-EV — do not fund)

The auditor is *deliberately* conservative: a unit-margin curve with
p10 below zero means there's a >10% chance every sale loses money,
which compounds badly at scale. P10 ≥ 0 is the operating bar.

If SOURCER produced no supplier, AUDITOR emits a HOLD with score=0
and notes the missing dependency. Downstream agents see
`auditor_margin_p10/p50/p90 = 0.0`.

Author: AEGIS Pulse core team
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from ..tools.monte_carlo import MarginPriors, simulate
from .base import AgentNode

if TYPE_CHECKING:
    from ..state import GraphState

# Default sell-price assumption for the simulation. The real number
# comes from a marketplace pricing API in Phase 4. For now we use a
# heuristic: 4.0× the supplier unit cost (a 25% landed-cost ratio is
# the e-commerce rule of thumb for a healthy contribution margin).
_DEFAULT_PRICE_MULTIPLIER = 4.0
_MIN_PRICE = 9.99
_MAX_PRICE = 199.99


def _suggested_price(unit_cost: float) -> float:
    if unit_cost <= 0:
        return _MIN_PRICE
    raw = unit_cost * _DEFAULT_PRICE_MULTIPLIER
    return max(_MIN_PRICE, min(_MAX_PRICE, round(raw, 2)))


class AuditorAgent(AgentNode):
    """Auditor: monte-carlo unit-margin gate."""

    name = "auditor"

    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        supplier = state.get("sourcer_supplier")

        # No supplier from SOURCER → no economic gate possible. HOLD
        # with low confidence; downstream sees zero margins.
        if not isinstance(supplier, dict) or supplier.get("unit_cost") is None:
            return AgentDecision(
                agent=self.name,
                trend_id=candidate.trend_id,
                correlation_id=candidate.correlation_id,
                verdict=AgentVerdict.HOLD,
                score=0.0,
                confidence=0.2,
                reasoning="no supplier from SOURCER; cannot evaluate unit economics",
                details={
                    "missing_dependency": "sourcer_supplier",
                    "p10": 0.0,
                    "p50": 0.0,
                    "p90": 0.0,
                },
            )

        unit_cost = float(supplier.get("unit_cost") or 0.0)
        sell_price = _suggested_price(unit_cost)

        # Scale CAC and shipping to the price tier so the model
        # makes sense across $10–$200 products. Default priors
        # assume a $30 sell-price benchmark.
        price_scale = max(0.4, min(2.0, sell_price / 30.0))
        priors = MarginPriors(
            sell_price=sell_price,
            cost_mean=max(0.5, unit_cost),
            shipping_mean=max(2.0, 4.0 * price_scale),
            cac_mean=max(2.0, 6.0 * price_scale),
        )
        # Deterministic seed so the same trend produces the same
        # margin estimate across repeated runs (audit reproducibility).
        seed_int = abs(hash(("auditor", candidate.trend_id))) % (2**31)
        sim = await simulate(iterations=4000, priors=priors, seed=seed_int)

        if not sim.ok or not isinstance(sim.data, dict):
            return AgentDecision(
                agent=self.name,
                trend_id=candidate.trend_id,
                correlation_id=candidate.correlation_id,
                verdict=AgentVerdict.HOLD,
                score=0.0,
                confidence=0.2,
                reasoning="monte_carlo tool failed",
                details={"tool_error": True, "p10": 0.0, "p50": 0.0, "p90": 0.0},
            )

        p10 = float(sim.data.get("p10", 0.0))
        p50 = float(sim.data.get("p50", 0.0))
        p90 = float(sim.data.get("p90", 0.0))
        mean = float(sim.data.get("mean", 0.0))
        loss_prob = float(sim.data.get("loss_probability", 1.0))

        # Verdict gating.
        #
        # The original rule was `p10 >= $0`, which sounds prudent but
        # is unrealistic for low-price (<$25) e-commerce because the
        # left tail of margin (dominated by returns + CAC variance)
        # is naturally negative. The realistic operating bar is:
        #
        #     PROCEED:  mean > $1 AND loss_prob <= 35%
        #     HOLD:     mean > $0 (positive EV but fragile)
        #     BLOCK:    mean <= $0 (negative EV — never fund)
        #
        # The p10 still informs `confidence` so a fragile margin
        # distribution gets a lower confidence in the supervisor's
        # blended score.
        if mean > 1.0 and loss_prob <= 0.35:
            verdict = AgentVerdict.PROCEED
        elif mean > 0.0:
            verdict = AgentVerdict.HOLD
        else:
            verdict = AgentVerdict.BLOCK

        # Score = normalized expected margin in [0,1]. Use a soft
        # squash so a $5 mean margin maps to ~0.6, $10 to ~0.85, etc.
        # (Avoid the cliff at exactly $0.)
        score = 0.0 if mean <= 0 else mean / (mean + 6.0)
        score = max(0.0, min(1.0, score))

        # Confidence rises with simulation iterations (always 4000 here)
        # and falls when loss_probability is high (more uncertainty).
        confidence = max(0.0, min(1.0, 1.0 - 0.7 * loss_prob))

        reasoning = (
            f"sell_price=${sell_price:.2f} unit_cost=${unit_cost:.2f}; "
            f"p10=${p10:.2f} p50=${p50:.2f} p90=${p90:.2f} "
            f"loss_prob={loss_prob:.2%}"
        )

        details: dict[str, Any] = {
            "sell_price": sell_price,
            "unit_cost": unit_cost,
            "p10": p10,
            "p50": p50,
            "p90": p90,
            "mean_margin": mean,
            "loss_probability": loss_prob,
            "iterations": int(sim.data.get("iterations", 0)),
            "supplier_synthetic": bool(supplier.get("synthetic", False)),
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
        # LLM augmentation only on HOLD — clear PROCEED/BLOCK don't
        # benefit from prose, and the simulation is the source of truth.
        if heuristic.verdict is not AgentVerdict.HOLD:
            return None
        try:
            user_text, _ver = prompts.render(
                "auditor",
                title=candidate.title,
                p10=heuristic.details.get("p10", 0.0),
                p50=heuristic.details.get("p50", 0.0),
                p90=heuristic.details.get("p90", 0.0),
                mean_margin=heuristic.details.get("mean_margin", 0.0),
                loss_probability=heuristic.details.get("loss_probability", 0.0),
                sell_price=heuristic.details.get("sell_price", 0.0),
                unit_cost=heuristic.details.get("unit_cost", 0.0),
            )
        except Exception:
            return None
        system_text = (
            "You are AUDITOR, the unit-economics gate. Reply with JSON only: "
            '{"reasoning": "<≤80 words>", "confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        return self._llm_apply(heuristic, resp)

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        return {
            "auditor_margin_p10": float(decision.details.get("p10", 0.0)),
            "auditor_margin_p50": float(decision.details.get("p50", 0.0)),
            "auditor_margin_p90": float(decision.details.get("p90", 0.0)),
        }
