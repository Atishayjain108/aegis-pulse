"""
aegis.memory.graph — Knowledge Graph (Rule 7).

A SQL-backed relationship store (NOT a graph database). Edges connect signals →
opportunities → entities → sources → outcomes, with cumulative ``weight`` and
``evidence_count`` so repeated relationships strengthen over time. Relationship
discovery is plain SQL aggregation; optional in-memory NetworkX analysis is
available when the (already-vendored) library is present.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from aegis.memory.schemas import GraphEdge, Opportunity
from aegis.memory.taxonomy import RelationType

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.graph")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class KnowledgeGraph:
    """Append-or-strengthen edges; discover relationships by SQL aggregation."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def add_edge(self, edge: GraphEdge) -> bool:
        """Upsert an edge — on repeat, accrue weight + bump evidence_count."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                await conn.execute(
                    """
                    INSERT INTO knowledge_edges (
                        src_kind, src_id, dst_kind, dst_id, relation,
                        weight, evidence_count, first_seen, last_seen, metadata
                    ) VALUES ($1,$2,$3,$4,$5,$6,1, NOW(), NOW(), $7)
                    ON CONFLICT (tenant_id, src_kind, src_id, dst_kind, dst_id, relation)
                    DO UPDATE SET
                        weight = knowledge_edges.weight + EXCLUDED.weight,
                        evidence_count = knowledge_edges.evidence_count + 1,
                        last_seen = NOW()
                    """,
                    edge.src_kind, edge.src_id, edge.dst_kind, edge.dst_id,
                    edge.relation.value, edge.weight, json.dumps(edge.metadata),
                )
            return True
        except Exception as exc:
            _log.error("memory.graph_add_edge_failed", error=str(exc))
            return False

    async def relate_opportunity(self, opp: Opportunity) -> int:
        """Materialise edges for one opportunity: trend→opp and opp→sources.

        Returns the number of edges written. Driven by an opportunity that is
        already grounded in a settled outcome — no invented relationships.
        """
        edges = [
            GraphEdge(
                src_kind="trend", src_id=opp.trend_key,
                dst_kind="opportunity", dst_id=opp.opportunity_id,
                relation=RelationType.SIGNAL_OF,
            )
        ]
        for plat in (opp.evidence.get("source_platforms") or []):
            edges.append(
                GraphEdge(
                    src_kind="opportunity", src_id=opp.opportunity_id,
                    dst_kind="source", dst_id=str(plat),
                    relation=RelationType.SOURCED_BY,
                )
            )
        written = 0
        for e in edges:
            if await self.add_edge(e):
                written += 1
        return written

    async def neighbors(
        self, src_kind: str, src_id: str, *, limit: int = 50,
    ) -> list[GraphEdge]:
        """ROADMAP — no production reader yet (verified 2026-06-18, PROJECT
        OMEGA). Edges ARE written in production via ``relate_opportunity()``
        (capture.py:120) as opportunities settle, but this read path is exercised
        only by unit tests — no decision or report queries it. See
        docs/DORMANT_SYSTEMS.md."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT src_kind, src_id, dst_kind, dst_id, relation,
                           weight, evidence_count
                    FROM knowledge_edges
                    WHERE src_kind = $1 AND src_id = $2
                    ORDER BY weight DESC
                    LIMIT $3
                    """,
                    src_kind, src_id, limit,
                )
            return [
                GraphEdge(
                    src_kind=r["src_kind"], src_id=r["src_id"],
                    dst_kind=r["dst_kind"], dst_id=r["dst_id"],
                    relation=RelationType(r["relation"]),
                    weight=float(r["weight"]), evidence_count=int(r["evidence_count"]),
                )
                for r in rows
            ]
        except Exception as exc:
            _log.error("memory.graph_neighbors_failed", error=str(exc))
            return []

    async def discover(
        self, relation: RelationType, *, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Strongest relationships of a given type — the discovered knowledge.

        ROADMAP — no production reader yet (verified 2026-06-18). Test-only.
        See docs/DORMANT_SYSTEMS.md."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT src_id, dst_id, weight, evidence_count
                    FROM knowledge_edges
                    WHERE relation = $1
                    ORDER BY weight DESC, evidence_count DESC
                    LIMIT $2
                    """,
                    relation.value, limit,
                )
            return [
                {
                    "src_id": r["src_id"], "dst_id": r["dst_id"],
                    "weight": float(r["weight"]),
                    "evidence_count": int(r["evidence_count"]),
                }
                for r in rows
            ]
        except Exception as exc:
            _log.error("memory.graph_discover_failed", error=str(exc))
            return []
