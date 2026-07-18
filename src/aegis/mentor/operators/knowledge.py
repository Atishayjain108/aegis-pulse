"""KnowledgeOp — domain knowledge + ground-reality facts.

Two grounded sources, never generic LLM filler:

1. The active sector's :class:`~aegis.mentor.sector.SectorPack` — real
   benchmarks, marketplaces, regulations, failure patterns (always available,
   zero I/O).
2. :mod:`aegis.memory` — accumulated, settled-outcome knowledge (opportunity
   patterns, top entities) when a DB pool is present.

Both degrade gracefully: no pool → SectorPack facts only; no deep pack → broad
baseline structural knowledge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from aegis.mentor.operators.base import OperatorContext, OperatorResult

if TYPE_CHECKING:
    from aegis.mentor.schemas import UserProfile

_log = structlog.get_logger("aegis.mentor.operators.knowledge")


class KnowledgeRetriever:
    """Pulls grounded domain facts from the SectorPack + memory ledger."""

    async def from_sector_pack(self, context: OperatorContext) -> tuple[list[str], list[str]]:
        """Return (facts, sources) drawn from the active sector pack."""
        pack = context.sector_pack
        if pack is None:
            return [], []
        facts: list[str] = []
        knowledge = pack.knowledge()
        source = f"sector_pack:{pack.sector_tag}"

        for reg in (knowledge.get("regulations") or [])[:4]:
            facts.append(f"Regulation: {reg}")
        for fp in (knowledge.get("failure_patterns") or [])[:4]:
            facts.append(f"Common failure: {fp}")
        markets = knowledge.get("marketplaces") or []
        if markets:
            facts.append(f"Active marketplaces: {', '.join(markets[:7])}")

        bench = pack.benchmarks()
        for key in ("min_viable_gross_margin_pct", "expected_return_rate", "typical_cac_inr"):
            if key in bench:
                facts.append(f"Benchmark {key}: {bench[key]}")

        return facts, ([source] if facts else [])

    async def from_memory(self, context: OperatorContext) -> tuple[list[str], list[str]]:
        """Return (facts, sources) from the settled-outcome memory ledger."""
        if context.pool is None:
            return [], []
        facts: list[str] = []
        sources: list[str] = []
        try:
            from aegis.memory.opportunity import OpportunityMemory

            patterns = await OpportunityMemory(context.pool).patterns(days_back=90, limit=5)
            for p in patterns:
                label = p.get("pattern") or p.get("opportunity_type") or p.get("type")
                if label:
                    facts.append(f"Memory: prior opportunity pattern — {label}")
            if patterns:
                sources.append("aegis.memory:opportunity")
        except Exception as exc:  # best-effort recall, never fatal
            _log.debug("mentor.knowledge.memory_recall_failed", error=str(exc)[:200])
        return facts, sources


class KnowledgeOp:
    """Domain-knowledge operator."""

    name = "knowledge"

    def __init__(self, retriever: Any | None = None) -> None:
        self._retriever = retriever or KnowledgeRetriever()

    async def run(
        self,
        profile: UserProfile,
        request: str,
        context: OperatorContext,
    ) -> OperatorResult:
        pack_facts, pack_sources = await self._retriever.from_sector_pack(context)
        mem_facts, mem_sources = await self._retriever.from_memory(context)

        findings = pack_facts + mem_facts
        sources = pack_sources + mem_sources

        deep = bool(context.sector_pack and getattr(context.sector_pack, "is_deep", False))
        if deep:
            reasoning = (
                f"Grounded domain knowledge for sector '{profile.sector}' "
                f"({len(pack_facts)} pack facts, {len(mem_facts)} memory facts)."
            )
            # A deep, verified pack earns higher confidence than the baseline.
            confidence = min(1.0, 0.55 + 0.05 * len(findings))
        elif findings:
            reasoning = "Broad baseline knowledge only — no deep pack for this sector yet."
            confidence = min(0.5, 0.2 + 0.05 * len(findings))
        else:
            reasoning = (
                f"No grounded domain knowledge available for '{profile.sector}'. "
                "Register a deep SectorPack to give expert counsel here."
            )
            confidence = 0.0

        actions: list[str] = []
        if not deep:
            actions.append(
                "Sector not yet deeply specialized — answers stay general until a "
                "deep pack is added."
            )

        return OperatorResult(
            operator=self.name,
            findings=findings,
            actions=actions,
            sources=sources,
            reasoning=reasoning,
            confidence=round(confidence, 3),
            data={"deep_sector": deep, "sector": profile.sector},
        )
