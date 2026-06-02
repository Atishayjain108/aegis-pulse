"""Compliance audit repository (Postgres, asyncpg, RLS).

Writes one row per verdict to ``compliance_audit`` (migration 0007). Dedup is by
the content-addressable ``content_id`` primary key (``ON CONFLICT DO NOTHING``),
matching the project-wide idempotency pattern. The connection is duck-typed so
the repo can be unit-tested with a fake.

Every query sets ``app.current_tenant`` first per the RLS convention.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from aegis.comply.errors import AuditWriteError
from aegis.comply.logging import get_logger
from aegis.comply.schemas import ComplianceVerdictResult

_log = get_logger("aegis.comply.store.audit")

_INSERT_SQL = """
INSERT INTO compliance_audit (
    content_id, tenant_id, trend_id, verdict, risk_score, confidence,
    blocking_reasons, rule_hits, jurisdictions, engine_version, checked_at
) VALUES ($1, $2::uuid, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10, $11)
ON CONFLICT (content_id) DO NOTHING
"""


@runtime_checkable
class PgConnection(Protocol):
    """Minimal asyncpg-connection-shaped protocol."""

    async def execute(self, query: str, *args: Any) -> Any:  # pragma: no cover
        ...


@runtime_checkable
class PgPool(Protocol):
    """Minimal asyncpg-pool-shaped protocol with ``acquire`` context manager."""

    def acquire(self) -> Any:  # pragma: no cover - structural type
        ...


class ComplianceAuditRepository:
    """Persists compliance verdicts under the tenant's RLS scope."""

    def __init__(self, pool: PgPool, *, tenant_id: str) -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def record(self, result: ComplianceVerdictResult) -> bool:
        """Insert ``result`` (idempotent). Returns ``True`` on success.

        Raises :class:`AuditWriteError` only on unexpected DB errors; callers
        typically treat audit writes as best-effort.
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET app.current_tenant = $1", self._tenant_id)
                await conn.execute(
                    _INSERT_SQL,
                    result.content_id,
                    self._tenant_id,
                    result.trend_id,
                    result.verdict.value,
                    result.risk_score,
                    result.confidence,
                    json.dumps(list(result.blocking_reasons)),
                    json.dumps([h.rule_id for h in result.rule_hits]),
                    json.dumps([j.value for j in result.jurisdictions]),
                    result.engine_version,
                    result.checked_at,
                )
            _log.info(
                "comply.audit_recorded",
                trend_id=result.trend_id,
                verdict=result.verdict.value,
                content_id=result.content_id[:12],
            )
            return True
        except Exception as exc:
            _log.warning("comply.audit_write_failed", trend_id=result.trend_id, error=str(exc))
            raise AuditWriteError(f"audit write failed: {exc}") from exc
