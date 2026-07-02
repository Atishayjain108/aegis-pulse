"""ResearchOp — market research / sizing / trends / demand depth.

Backed by the existing :class:`aegis.intelligence.research_engine.ResearchEngine`
(5-pass multi-source methodology over the scrape swarm). The operator adapts the
research *depth* to the user's autonomy preference and translates the structured
``ResearchReport`` into a grounded :class:`OperatorResult`.

Heuristic-first: ResearchEngine produces numbers deterministically from the
harvested signals; no LLM is required for a usable result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from aegis.mentor.operators.base import OperatorContext, OperatorResult

if TYPE_CHECKING:
    from aegis.mentor.schemas import UserProfile

_log = structlog.get_logger("aegis.mentor.operators.research")


class ResearchOp:
    """Market-research operator."""

    name = "research"

    def __init__(self, engine: Any | None = None) -> None:
        # Injectable for tests; lazily built per-run otherwise so the heavy
        # scrape stack is only imported when actually used.
        self._engine = engine

    def _get_engine(self, context: OperatorContext) -> Any:
        if self._engine is not None:
            return self._engine
        from aegis.intelligence.research_engine import ResearchEngine

        return ResearchEngine(pool=context.pool, redis=context.redis)

    async def run(
        self,
        profile: UserProfile,
        request: str,
        context: OperatorContext,
    ) -> OperatorResult:
        query = (request or profile.sector_raw or profile.sector).strip()
        if not query:
            return OperatorResult(
                operator=self.name,
                reasoning="No query or sector to research.",
                confidence=0.0,
            )

        engine = self._get_engine(context)
        try:
            report = await engine.research(query, depth=context.depth)
        except Exception as exc:  # never crash the fleet — degrade to empty
            _log.warning("mentor.research.failed", query=query, error=str(exc)[:200])
            return OperatorResult(
                operator=self.name,
                reasoning=f"Research harvest failed: {type(exc).__name__}.",
                confidence=0.0,
            )

        findings = list(report.cross_verified_findings)
        # Top single-source claims add colour but are flagged as unverified.
        findings += [f"(unverified) {c}" for c in report.unverified_claims[:3]]

        return OperatorResult(
            operator=self.name,
            findings=findings,
            actions=list(report.recommended_actions),
            sources=list(report.sources_consulted),
            reasoning=report.executive_summary,
            confidence=float(report.confidence_score),
            data={
                "trend_verdict": report.trend_verdict,
                "signal_count": report.signal_count,
                "opportunities": list(report.opportunities),
                "risks": list(report.risks),
                "topic_type": report.topic_type,
                "depth": report.research_depth,
            },
        )
