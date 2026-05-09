"""
HISTORIAN agent — surfaces analogous past trends as context.

The HISTORIAN keeps a running ChromaDB collection of every trend that
has graduated through the pipeline (PROCEED or BLOCK). On each new
candidate it asks: "have we seen something like this before? what
happened to it?" and surfaces the top-k analogues with similarity
scores.

Unlike the other agents, HISTORIAN's heuristic doesn't compute a
gating score — its score is simply `mean_similarity` of the analogues
returned, and its verdict is always PROCEED. It's an *information
contributor*, not a gate. The supervisor reads `historian_analogues`
to add context to alerts and to inform AUDITOR's confidence.

When ChromaDB is unavailable (no install, no persistent volume), the
agent emits a PROCEED with empty analogues and a low-confidence flag.
The pipeline continues without it.

Author: AEGIS Pulse core team
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from ..tools.historical import find_analogues
from .base import AgentNode

if TYPE_CHECKING:  # pragma: no cover
    from ..llm.router import LLMRouter
    from ..memory.chroma_store import ChromaMemoryStore
    from ..state import GraphState


_DEFAULT_K = 5
_DEFAULT_MIN_SCORE = 0.55


class HistorianAgent(AgentNode):
    """Historian: analogues miner."""

    name = "historian"

    def __init__(
        self,
        *,
        store: ChromaMemoryStore | None = None,
        k: int = _DEFAULT_K,
        min_score: float = _DEFAULT_MIN_SCORE,
        router: LLMRouter | None = None,
        use_llm: bool = True,
        llm_max_tokens: int = 256,
        llm_temperature: float = 0.3,
        llm_timeout_s: float = 15.0,
    ) -> None:
        super().__init__(
            router=router,
            use_llm=use_llm,
            llm_max_tokens=llm_max_tokens,
            llm_temperature=llm_temperature,
            llm_timeout_s=llm_timeout_s,
        )
        self._store = store
        self._k = max(1, min(int(k), 20))
        self._min_score = max(0.0, min(1.0, float(min_score)))

    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        analogues: list[dict[str, Any]] = []
        considered = 0
        store_ok = self._store is not None

        if store_ok:
            query_text = " | ".join(
                t for t in (candidate.title, candidate.summary[:300]) if t
            ) or candidate.trend_id

            try:
                result = await find_analogues(
                    self._store,  # type: ignore[arg-type]
                    query_text=query_text,
                    k=self._k,
                    min_score=self._min_score,
                )
                if result.ok and isinstance(result.data, list):
                    analogues = list(result.data)
                    considered = int(result.metadata.get("considered", len(analogues)))
            except Exception:  # pragma: no cover
                analogues = []

        if analogues:
            mean_sim = sum(float(a.get("score", 0.0)) for a in analogues) / len(analogues)
        else:
            mean_sim = 0.0

        # Verdict: HISTORIAN always PROCEEDs (never gates) — its job
        # is to *contribute information*, not arbitrate.
        verdict = AgentVerdict.PROCEED
        score = max(0.0, min(1.0, mean_sim))
        confidence = 0.4 + 0.5 * min(1.0, len(analogues) / float(self._k))
        if not store_ok:
            confidence = 0.2  # operating blind without the store

        reasoning = (
            f"analogues={len(analogues)}/{considered or 0} "
            f"mean_similarity={mean_sim:.2f}"
        )

        details: dict[str, Any] = {
            "analogues": analogues,
            "mean_similarity": mean_sim,
            "k": self._k,
            "store_available": store_ok,
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
        # LLM augmentation is useful here ONLY when we have analogues
        # to interpret. Synthesise a brief narrative connecting the
        # current candidate to the analogues.
        analogues = heuristic.details.get("analogues") or []
        if not analogues:
            return None
        try:
            sample = "\n".join(
                f"- ({a.get('score', 0.0):.2f}) {str(a.get('text', ''))[:200]}"
                for a in analogues[:3]
            )
            user_text, _ver = prompts.render(
                "historian",
                title=candidate.title,
                summary=(candidate.summary or "")[:400],
                analogues=sample,
            )
        except Exception:
            return None
        system_text = (
            "You are HISTORIAN. Connect the current candidate to past analogues. "
            'Reply JSON only: {"reasoning": "<≤80 words>", "confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        return self._llm_apply(heuristic, resp)

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        return {
            "historian_analogues": list(decision.details.get("analogues", []) or []),
        }

    def _merge_partial(
        self,
        decision: AgentDecision,
        state: GraphState,
    ) -> dict[str, Any]:
        # Information contributor — never adds to blocked_by even if
        # somehow it emitted BLOCK (it shouldn't).
        partial: dict[str, Any] = {"decisions": [decision]}
        partial.update(self._extra_state(decision))
        return partial
