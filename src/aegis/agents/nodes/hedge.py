"""
HEDGE agent — portfolio-level final veto on correlation excess.

The supervisor calls HEDGE *last*, after every other agent has voted.
Its job: prevent the system from over-concentrating into a handful of
correlated bets. If we already have 3 active "fitness gear" candidates
in the working pool, a 4th one is correlation excess regardless of how
strong its individual SCOUT/AUDITOR scores are.

Heuristic:

    1. Read `active_trends` from `SharedWorkingMemory` (Phase-1 dep).
       Each entry has a `category` and a `score` field that the
       supervisor wrote there for previous candidates.
    2. Compute `category_concentration` — fraction of the active pool
       in the same category as the current candidate.
    3. Compute `correlation_excess` — concentration above the soft cap
       (default 0.40 = 40% of pool in one category).
    4. Veto verdict:
            excess >= 0.20  → BLOCK   (severe over-concentration)
            excess >= 0.05  → HOLD    (warning; size-down)
            else            → PROCEED (within risk budget)

When SharedWorkingMemory is unavailable (no Redis, fresh start, etc.)
the agent emits PROCEED with a low-data flag — failing safe is correct
because we'd rather under-veto on a fresh boot than block all trades.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from .base import AgentNode

if TYPE_CHECKING:  # pragma: no cover
    from ..llm.router import LLMRouter
    from ..memory.shared_memory import SharedWorkingMemory
    from ..state import GraphState


# Soft cap: max fraction of the active pool that may share a category.
_CONCENTRATION_CAP = 0.40
_BLOCK_EXCESS = 0.20  # 60% of pool in one category → block
_HOLD_EXCESS = 0.05  # 45% → warn


class HedgeAgent(AgentNode):
    """Hedge: portfolio-level correlation veto."""

    name = "hedge"

    def __init__(
        self,
        *,
        shared_memory: SharedWorkingMemory | None = None,
        router: LLMRouter | None = None,
        use_llm: bool = True,
        llm_max_tokens: int = 256,
        llm_temperature: float = 0.2,
        llm_timeout_s: float = 15.0,
    ) -> None:
        super().__init__(
            router=router,
            use_llm=use_llm,
            llm_max_tokens=llm_max_tokens,
            llm_temperature=llm_temperature,
            llm_timeout_s=llm_timeout_s,
        )
        self._shared_memory = shared_memory

    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        # Pull the current candidate's category from sourcer state.
        supplier = state.get("sourcer_supplier")
        my_category: str = "unknown"
        if isinstance(supplier, dict):
            my_category = str(supplier.get("category", "unknown"))

        active_categories: dict[str, int] = {}
        active_pool_size = 0
        sm_used = False

        if self._shared_memory is not None:
            sm_used = True
            try:
                my_tenant_id = state.get("tenant_id", "default")
                trend_id = state.get("trend_id", candidate.trend_id)

                actives = await self._shared_memory.active_trends(
                    max_age_seconds=24 * 60 * 60, limit=200
                )
                # Filter to current tenant only; exclude the current trend.
                actives = [
                    (t, tid) for (t, tid) in actives if t == my_tenant_id and tid != trend_id
                ]

                for ten, tid in actives:
                    other = await self._shared_memory.get_all(ten, tid)
                    if not isinstance(other, dict):
                        continue
                    cat = str(other.get("category", "unknown")) if other else "unknown"
                    active_categories[cat] = active_categories.get(cat, 0) + 1
                    active_pool_size += 1
            except Exception:  # pragma: no cover - defensive
                # If shared memory is broken, fail safe → PROCEED.
                sm_used = False
                active_categories = {}
                active_pool_size = 0

        if active_pool_size == 0:
            # No prior data → no correlation can be measured. PROCEED.
            return AgentDecision(
                agent=self.name,
                trend_id=candidate.trend_id,
                correlation_id=candidate.correlation_id,
                verdict=AgentVerdict.PROCEED,
                score=1.0,
                confidence=0.4 if sm_used else 0.2,
                reasoning="empty active pool; no correlation to measure",
                details={
                    "hedge_passed": True,
                    "hedge_correlation_excess": 0.0,
                    "active_pool_size": 0,
                    "category": my_category,
                    "shared_memory_used": sm_used,
                },
            )

        same_cat_count = active_categories.get(my_category, 0)
        # +1 to include *this* trend as if it were also accepted.
        prospective_concentration = (same_cat_count + 1) / (active_pool_size + 1)
        excess = max(0.0, prospective_concentration - _CONCENTRATION_CAP)

        if excess >= _BLOCK_EXCESS:
            verdict = AgentVerdict.BLOCK
            reason = "correlation excess: severe category over-concentration"
        elif excess >= _HOLD_EXCESS:
            verdict = AgentVerdict.HOLD
            reason = "correlation excess: warn — size-down recommended"
        else:
            verdict = AgentVerdict.PROCEED
            reason = "within concentration cap"

        # Score: 1.0 means perfectly diversified; 0.0 means one-sector
        # concentration.
        score = max(0.0, min(1.0, 1.0 - prospective_concentration))
        confidence = 0.5 + 0.5 * min(1.0, active_pool_size / 20.0)

        reasoning = (
            f"category={my_category} same_cat={same_cat_count}/{active_pool_size} "
            f"concentration={prospective_concentration:.2%} excess={excess:.2%}; {reason}"
        )

        details: dict[str, Any] = {
            "hedge_passed": verdict is AgentVerdict.PROCEED,
            "hedge_correlation_excess": excess,
            "category": my_category,
            "same_category_count": same_cat_count,
            "active_pool_size": active_pool_size,
            "prospective_concentration": prospective_concentration,
            "shared_memory_used": sm_used,
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
        # Risk decisions deserve a sanity check on HOLD only — block
        # and proceed are clear from the math.
        if heuristic.verdict is not AgentVerdict.HOLD:
            return None
        try:
            user_text, _ver = prompts.render(
                "hedge",
                title=candidate.title,
                category=heuristic.details.get("category", "unknown"),
                concentration=round(heuristic.details.get("prospective_concentration", 0.0), 3),
                excess=round(heuristic.details.get("hedge_correlation_excess", 0.0), 3),
                pool_size=heuristic.details.get("active_pool_size", 0),
            )
        except Exception:
            return None
        system_text = (
            "You are HEDGE, the portfolio risk manager. Reply JSON only: "
            '{"reasoning": "<≤80 words>", "confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        return self._llm_apply(heuristic, resp)

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        return {
            "hedge_passed": bool(decision.details.get("hedge_passed", False)),
            "hedge_correlation_excess": float(
                decision.details.get("hedge_correlation_excess", 0.0)
            ),
        }
