"""
aegis.memory.entity — Entity Memory (Rule 3).

Long-term memory for products / brands / companies / suppliers / marketplaces /
regions / regulations / technologies. Entities are observed (upsert bumps
``n_observations`` + ``last_seen``) and judged from settled opportunity outcomes
(``record_outcome`` → trust recomputed as the realized rate). Best-effort
persistence: log on error, never raise.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from aegis.memory.schemas import Entity
from aegis.memory.taxonomy import EntityKind

if TYPE_CHECKING:
    from asyncpg import Pool

_log = structlog.get_logger("aegis.memory.entity")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
_PRIOR_TRUST = 0.5


class EntityMemory:
    """Upsert + track + score long-lived entities."""

    def __init__(self, db_pool: Pool, tenant_id: str = _DEFAULT_TENANT) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def upsert(
        self,
        entity_kind: EntityKind,
        canonical_name: str,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> bool:
        """Record an observation of an entity (idempotent on kind+name)."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                await conn.execute(
                    """
                    INSERT INTO entities (
                        entity_kind, canonical_name, attributes, trust,
                        n_observations, first_seen, last_seen
                    ) VALUES ($1,$2,$3,$4,1, NOW(), NOW())
                    ON CONFLICT (tenant_id, entity_kind, canonical_name) DO UPDATE SET
                        n_observations = entities.n_observations + 1,
                        last_seen = NOW(),
                        attributes = entities.attributes || EXCLUDED.attributes
                    """,
                    entity_kind.value,
                    canonical_name.strip().lower(),
                    json.dumps(attributes or {}),
                    _PRIOR_TRUST,
                )
            return True
        except Exception as exc:
            _log.error("memory.entity_upsert_failed", error=str(exc))
            return False

    async def record_outcome(
        self, entity_kind: EntityKind, canonical_name: str,
        opportunity_id: str, outcome: str, *, contribution: float = 1.0,
    ) -> bool:
        """Attach a settled opportunity outcome to an entity + refresh its trust."""
        name = canonical_name.strip().lower()
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                row = await conn.fetchrow(
                    "SELECT entity_id FROM entities "
                    "WHERE entity_kind = $1 AND canonical_name = $2",
                    entity_kind.value, name,
                )
                if row is None:
                    return False
                entity_id = str(row["entity_id"])
                await conn.execute(
                    """
                    INSERT INTO entity_outcomes (
                        entity_id, opportunity_id, outcome, contribution
                    ) VALUES ($1,$2,$3,$4)
                    ON CONFLICT (tenant_id, entity_id, opportunity_id) DO NOTHING
                    """,
                    entity_id, opportunity_id, outcome, contribution,
                )
                # Recompute trust = realized rate over this entity's outcomes.
                await conn.execute(
                    """
                    UPDATE entities e SET trust = COALESCE((
                        SELECT AVG(CASE WHEN outcome = 'realized' THEN 1.0 ELSE 0.0 END)
                        FROM entity_outcomes WHERE entity_id = e.entity_id
                    ), $2)
                    WHERE e.entity_id = $1
                    """,
                    entity_id, _PRIOR_TRUST,
                )
            return True
        except Exception as exc:
            _log.error("memory.entity_record_outcome_failed", error=str(exc))
            return False

    async def get(self, entity_kind: EntityKind, canonical_name: str) -> Entity | None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                row = await conn.fetchrow(
                    """
                    SELECT entity_kind, canonical_name, attributes, trust,
                           n_observations, first_seen, last_seen, metadata
                    FROM entities
                    WHERE entity_kind = $1 AND canonical_name = $2
                    """,
                    entity_kind.value, canonical_name.strip().lower(),
                )
            if row is None:
                return None
            return Entity(
                entity_kind=EntityKind(row["entity_kind"]),
                canonical_name=row["canonical_name"],
                attributes=json.loads(row["attributes"]) if row["attributes"] else {},
                trust=float(row["trust"]),
                n_observations=int(row["n_observations"]),
                first_seen=row["first_seen"],
                last_seen=row["last_seen"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            )
        except Exception as exc:
            _log.error("memory.entity_get_failed", error=str(exc))
            return None

    async def top_entities(
        self, entity_kind: EntityKind, *, limit: int = 20, min_observations: int = 1,
    ) -> list[Entity]:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)", self._tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT entity_kind, canonical_name, attributes, trust,
                           n_observations, first_seen, last_seen, metadata
                    FROM entities
                    WHERE entity_kind = $1 AND n_observations >= $2
                    ORDER BY trust DESC, n_observations DESC
                    LIMIT $3
                    """,
                    entity_kind.value, min_observations, limit,
                )
            return [
                Entity(
                    entity_kind=EntityKind(r["entity_kind"]),
                    canonical_name=r["canonical_name"],
                    attributes=json.loads(r["attributes"]) if r["attributes"] else {},
                    trust=float(r["trust"]),
                    n_observations=int(r["n_observations"]),
                    first_seen=r["first_seen"],
                    last_seen=r["last_seen"],
                    metadata=json.loads(r["metadata"]) if r["metadata"] else {},
                )
                for r in rows
            ]
        except Exception as exc:
            _log.error("memory.entity_top_failed", error=str(exc))
            return []
