"""
SOURCER agent — feasibility check and supplier match.

Without paid supplier-network APIs (1688, Alibaba, Faire, etc.) we
can't actually quote a real factory. What we *can* do, deterministically
and without any external dependency, is:

  1. Detect the candidate's product category from text features.
  2. Map that category to a feasibility class (easy / standard /
     hard / blocked) using a hand-curated table calibrated against
     dropshippers' real-world experience.
  3. If feasible, synthesize a *plausible* mock supplier (deterministic
     hash of trend_id → name, MOQ, lead time, unit cost) so downstream
     agents (auditor, hedge) have something to chew on.

The mock supplier is clearly marked `synthetic=True` in details so
audits never confuse it with a real PO. When a real supplier-API
integration lands in Phase 4, this heuristic becomes the fallback.

Heuristic verdicts:
    easy   + sufficient signal_count → PROCEED, supplier emitted
    standard                         → PROCEED, supplier emitted
    hard                             → HOLD, supplier emitted with caveat
    blocked categories               → BLOCK, no supplier

Author: AEGIS Pulse core team
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING, Any

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from .base import AgentNode

if TYPE_CHECKING:
    from ..state import GraphState

# ----------------------------------------------------------------------
# Category detection table.
#
# Order matters: the first matching pattern wins, so put more specific
# patterns ABOVE more general ones (e.g. "smart watch" before "watch").
# ----------------------------------------------------------------------
_CATEGORY_RULES: tuple[tuple[str, str, str], ...] = (
    # (regex, category, feasibility)
    # Hard / blocked
    (r"\b(?:medical\s+device|defibrillator|thermometer|blood\s+pressure)\b", "medical_device", "blocked"),
    (r"\b(?:firearm|ammunition|silencer|gun\s+part|holster)\b", "firearms", "blocked"),
    (r"\b(?:cbd|thc|delta[-\s]?[89]|cannabis|marijuana)\b", "cannabis", "blocked"),
    (r"\b(?:prescription|rx)\b", "rx_pharma", "blocked"),
    (r"\b(?:supplement|nootropic|protein\s+powder|vitamin)\b", "supplements", "hard"),
    (r"\b(?:cosmetic|skincare|sunscreen|serum|moisturiz)", "cosmetics", "hard"),
    (r"\b(?:powerbank|charger|battery|li[-\s]?ion|lithium)\b", "electronics_battery", "hard"),
    (r"\b(?:laser\s+pointer|laser\s+pen)\b", "laser", "hard"),
    (r"\b(?:smart\s+watch|smartwatch|fitness\s+tracker)\b", "wearable_electronics", "hard"),
    (r"\b(?:drone|quadcopter)\b", "drone", "hard"),
    (r"\b(?:children'?s?\s+toy|baby|infant|toddler)\b", "children", "hard"),
    # Standard
    (r"\b(?:earbud|headphone|speaker|bluetooth\s+(?:speaker|earbud))\b", "consumer_electronics", "standard"),
    (r"\b(?:phone\s+case|phone\s+stand|tablet\s+stand)\b", "phone_accessory", "standard"),
    (r"\b(?:bag|backpack|tote|duffel|luggage)\b", "bags", "standard"),
    (r"\b(?:shoe|sneaker|sandal|boot|slipper)\b", "footwear", "standard"),
    (r"\b(?:jewelry|necklace|earring|bracelet|ring)\b", "jewelry", "standard"),
    (r"\b(?:gym|workout|yoga|fitness|resistance\s+band|dumbbell)\b", "fitness_gear", "standard"),
    (r"\b(?:pet|cat|dog)\s+(?:bed|toy|food|leash|collar|carrier)\b", "pet_supplies", "standard"),
    (r"\b(?:kitchen|spatula|knife|cookware|cutting\s+board)\b", "kitchen", "standard"),
    # Easy (low MOQ, fast turnaround, generic factories)
    (r"\b(?:t[-\s]?shirt|hoodie|sweatshirt|hat|cap|tote\s+bag)\b", "apparel_print", "easy"),
    (r"\b(?:sticker|enamel\s+pin|patch)\b", "novelty", "easy"),
    (r"\b(?:candle|incense|diffuser|essential\s+oil)\b", "home_fragrance", "easy"),
    (r"\b(?:journal|notebook|planner|sticker\s+book)\b", "stationery", "easy"),
    (r"\b(?:mug|tumbler|water\s+bottle|coaster)\b", "drinkware", "easy"),
)

_FEASIBILITY_BASE_SCORE: dict[str, float] = {
    "easy": 0.85,
    "standard": 0.65,
    "hard": 0.40,
    "blocked": 0.0,
}

# MOQ assumptions per feasibility class — minimum order quantity that
# a typical Asian manufacturer or US POD would accept. These are
# rough-of-thumb numbers from real dropshipper interviews.
_MOQ_BY_FEASIBILITY: dict[str, int] = {
    "easy": 50,
    "standard": 200,
    "hard": 500,
    "blocked": 0,
}

# Lead-time in days from PO to first delivery.
_LEAD_TIME_BY_FEASIBILITY: dict[str, int] = {
    "easy": 7,
    "standard": 21,
    "hard": 45,
    "blocked": 0,
}

# Realistic unit-cost mid-points (USD) for each category. These feed
# the AUDITOR's monte_carlo simulation as the cost_mean prior.
_UNIT_COST_BY_CATEGORY: dict[str, float] = {
    "apparel_print": 6.0,
    "novelty": 2.0,
    "home_fragrance": 5.5,
    "stationery": 3.5,
    "drinkware": 4.5,
    "consumer_electronics": 14.0,
    "phone_accessory": 4.0,
    "bags": 9.0,
    "footwear": 12.0,
    "jewelry": 3.5,
    "fitness_gear": 7.0,
    "pet_supplies": 6.0,
    "kitchen": 7.5,
    "supplements": 4.5,
    "cosmetics": 4.0,
    "electronics_battery": 11.0,
    "laser": 6.0,
    "wearable_electronics": 18.0,
    "drone": 35.0,
    "children": 7.0,
    "medical_device": 0.0,
    "firearms": 0.0,
    "cannabis": 0.0,
    "rx_pharma": 0.0,
    "general": 8.0,
}


_COMPILED_RULES: tuple[tuple[Any, str, str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), cat, feas) for pat, cat, feas in _CATEGORY_RULES
)


def _detect_category(*texts: str) -> tuple[str, str]:
    """Return (category, feasibility). Falls back to ('general', 'standard')."""
    blob = " ".join(t for t in texts if t).lower()
    for rx, category, feasibility in _COMPILED_RULES:
        if rx.search(blob):
            return category, feasibility
    return "general", "standard"


def _synthesize_supplier(trend_id: str, *, category: str, feasibility: str) -> dict[str, Any]:
    """Build a deterministic mock supplier for the given trend.

    Same input → same supplier. Different trends → different suppliers.
    Marked `synthetic=True` so audit logs are clear.
    """
    # Deterministic hash → stable supplier name + region.
    digest = hashlib.sha256(f"sourcer:{trend_id}:{category}".encode()).hexdigest()
    seed = int(digest[:8], 16)

    regions = ("Guangdong, CN", "Yiwu, CN", "Hangzhou, CN", "Vietnam", "Mexico", "USA-domestic")
    region = regions[seed % len(regions)]

    base_cost = _UNIT_COST_BY_CATEGORY.get(category, 8.0)
    if base_cost <= 0:
        base_cost = 8.0  # blocked categories don't get a supplier; defensive

    # Apply a deterministic ±15% jitter so the cost is plausible.
    jitter = ((seed >> 8) % 30 - 15) / 100.0
    unit_cost = round(base_cost * (1.0 + jitter), 2)

    moq = _MOQ_BY_FEASIBILITY.get(feasibility, 200)
    lead_time = _LEAD_TIME_BY_FEASIBILITY.get(feasibility, 21)

    name_suffix = digest[8:14].upper()
    return {
        "name": f"AEGIS-Synth-{name_suffix}",
        "region": region,
        "category": category,
        "moq": int(moq),
        "lead_time_days": int(lead_time),
        "unit_cost": float(unit_cost),
        "feasibility": feasibility,
        "synthetic": True,
        "source": "heuristic.sourcer.v1",
    }


class SourcerAgent(AgentNode):
    """Sourcer."""

    name = "sourcer"

    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        category, feasibility = _detect_category(
            candidate.title,
            candidate.summary,
            candidate.representative_text,
        )

        # Volume-aware MOQ-friendliness: more signals → demand justifies
        # higher MOQ. We give a small bonus for high-signal trends.
        sc = max(0, int(candidate.signal_count))
        # log(1+x) compresses the long tail; 200 signals → ~0.14
        moq_bonus = math.log1p(sc) / 25.0
        moq_bonus = max(0.0, min(0.20, moq_bonus))

        base = _FEASIBILITY_BASE_SCORE.get(feasibility, 0.5)

        # Read upstream scout score if available — helps the heuristic
        # avoid sourcing weak candidates even if they're feasible.
        scout_score = float(state.get("scout_score", 0.5))
        scout_factor = 0.5 + 0.5 * scout_score  # in [0.5, 1.0]

        score = (base + moq_bonus) * scout_factor
        score = max(0.0, min(1.0, score))

        if feasibility == "blocked":
            verdict = AgentVerdict.BLOCK
            supplier: dict[str, Any] | None = None
        elif score >= 0.65:
            verdict = AgentVerdict.PROCEED
            supplier = _synthesize_supplier(
                candidate.trend_id, category=category, feasibility=feasibility
            )
        elif score >= 0.40:
            verdict = AgentVerdict.HOLD
            supplier = _synthesize_supplier(
                candidate.trend_id, category=category, feasibility=feasibility
            )
        else:
            verdict = AgentVerdict.HOLD
            supplier = None

        # Confidence increases with signal_count and decreases with
        # feasibility uncertainty.
        confidence = (
            0.4
            + 0.3 * (1.0 if feasibility in {"easy", "standard"} else 0.5)
            + 0.3 * min(1.0, sc / 100.0)
        )
        confidence = max(0.0, min(1.0, confidence))

        details: dict[str, Any] = {
            "category": category,
            "feasibility": feasibility,
            "moq_bonus": round(moq_bonus, 4),
            "scout_score_used": round(scout_score, 4),
        }
        if supplier is not None:
            details["supplier"] = supplier

        reasoning_parts = [
            f"category={category}",
            f"feasibility={feasibility}",
            f"score={score:.2f}",
        ]
        if supplier is not None:
            reasoning_parts.append(
                f"supplier moq={supplier['moq']} lead={supplier['lead_time_days']}d"
                f" unit_cost=${supplier['unit_cost']:.2f}"
            )
        else:
            reasoning_parts.append("no viable supplier")
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
        # Stash supplier on the decision so _extra_state can find it.

    async def _augment_with_llm(
        self,
        candidate: TrendCandidate,
        state: GraphState,
        heuristic: AgentDecision,
    ) -> AgentDecision | None:
        # Only consult LLM when we're on the fence — sourcing is a
        # decision that benefits from world-knowledge (is the category
        # actually feasible? Are there hidden complications?).
        if heuristic.verdict is not AgentVerdict.HOLD:
            return None
        try:
            user_text, _ver = prompts.render(
                "sourcer",
                title=candidate.title,
                summary=(candidate.summary or "")[:600],
                category=heuristic.details.get("category", "general"),
                feasibility=heuristic.details.get("feasibility", "standard"),
                heuristic_score=round(heuristic.score, 3),
            )
        except Exception:
            return None

        system_text = (
            "You are SOURCER. Reply with JSON only: "
            '{"reasoning": "<≤80 words on supply-chain feasibility>", '
            '"confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        return self._llm_apply(heuristic, resp)

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        supplier = decision.details.get("supplier")
        return {"sourcer_supplier": supplier if isinstance(supplier, dict) else None}
