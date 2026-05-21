"""Postgres repository for Phase 4 alerts, outbox, deliveries, intents.

Uses the **shared pool pattern** established by Phase 1: callers (CLI,
workers, API) set `aegis.db.pool.get_shared_pool()` once at startup; this
module fetches the pool lazily on every call. No DI plumbing required.

If `aegis.db.pool` is unavailable (e.g. unit-test environment), the user
may pass a pool object directly to the repository via the `pool` arg.

The repository ALWAYS sets `app.current_tenant` per transaction so RLS
gates fire correctly. This matches the convention used by Phases 1-3.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol, cast, runtime_checkable

import structlog

from aegis.execute.errors import EXEC_OUTBOX_NO_POOL, AegisExecuteError
from aegis.execute.schemas.alert import (
    Alert,
    AlertOutboxRow,
    AlertStatus,
    DeliveryAttempt,
    DeliveryStatus,
)
from aegis.execute.schemas.intent import ExecutionIntent
from aegis.execute.utils.time import utc_now

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pool resolution (avoid hard import on aegis.db.pool so unit tests pass
# without Phase 1 installed)
# ---------------------------------------------------------------------------
@runtime_checkable
class _PoolLike(Protocol):
    """Minimal asyncpg pool surface we depend on."""

    def acquire(self) -> Any: ...


def _resolve_pool(explicit: _PoolLike | None) -> _PoolLike:
    if explicit is not None:
        return explicit
    try:
        # Lazy import: keeps unit tests free of asyncpg/Phase 1.
        from aegis.db.pool import get_shared_pool  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AegisExecuteError(
            EXEC_OUTBOX_NO_POOL,
            cause=exc,
            context={"hint": "Pass `pool=` explicitly or call set_shared_pool()."},
        ) from exc
    try:
        pool = get_shared_pool()
    except RuntimeError as exc:
        raise AegisExecuteError(
            EXEC_OUTBOX_NO_POOL,
            cause=exc,
            context={"hint": "Phase 1 set_shared_pool() not called."},
        ) from exc
    if pool is None:
        raise AegisExecuteError(
            EXEC_OUTBOX_NO_POOL,
            context={"hint": "Phase 1 set_shared_pool() not called."},
        )
    return cast(_PoolLike, pool)


class AlertRepository:
    """Repository for Phase 4 tables.

    Each public method is independently safe to call concurrently. They
    each acquire one connection, set the RLS tenant context, and commit
    via the implicit transaction created by the asyncpg context manager.

    All datetimes returned are timezone-aware UTC.
    """

    __slots__ = ("_pool",)

    def __init__(self, *, pool: _PoolLike | None = None) -> None:
        self._pool = pool

    # ------------------------------------------------------------------ helpers
    async def _set_tenant(self, conn: Any, tenant_id: str) -> None:
        # set_config returns a row; we don't read it.
        await conn.execute("SELECT set_config('app.current_tenant', $1, true)", tenant_id)

    # ------------------------------------------------------------------ alerts
    async def insert_alert(self, alert: Alert) -> bool:
        """INSERT … ON CONFLICT (alert_id) DO NOTHING.

        Returns True if a row was actually inserted, False if it was a
        duplicate (idempotent retry).
        """
        pool = _resolve_pool(self._pool)
        sql = """
            INSERT INTO alerts (
                alert_id, tenant_id, trend_id, decision_window,
                verdict, priority, score, confidence,
                source,
                p_breakout_24h, p_decline_6h, p_saturation,
                expected_margin_usd, loss_probability,
                advised_units, advised_capital_usd,
                halt_reason, blocked_by,
                title, summary_text, correlation_id, created_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,
                $10,$11,$12,$13,$14,$15,$16,$17,$18,
                $19,$20,$21,$22
            )
            ON CONFLICT (alert_id) DO NOTHING
            RETURNING alert_id
        """
        async with pool.acquire() as conn:
            await self._set_tenant(conn, str(alert.tenant_id))
            row = await conn.fetchrow(
                sql,
                alert.alert_id,
                alert.tenant_id,
                alert.trend_id,
                alert.decision_window,
                alert.verdict,
                alert.priority,
                alert.score,
                alert.confidence,
                str(alert.source),
                alert.p_breakout_24h,
                alert.p_decline_6h,
                alert.p_saturation,
                alert.expected_margin_usd,
                alert.loss_probability,
                alert.advised_units,
                alert.advised_capital_usd,
                alert.halt_reason,
                list(alert.blocked_by),
                alert.title,
                alert.summary_text,
                alert.correlation_id,
                alert.created_at,
            )
            inserted = row is not None
            _log.info(
                "execute.repo.alert_inserted",
                alert_id=alert.alert_id,
                inserted=inserted,
            )
            return inserted

    async def get_alert(self, *, tenant_id: str, alert_id: str) -> Alert | None:
        pool = _resolve_pool(self._pool)
        async with pool.acquire() as conn:
            await self._set_tenant(conn, tenant_id)
            row = await conn.fetchrow(
                "SELECT * FROM alerts WHERE alert_id = $1", alert_id
            )
            if row is None:
                return None
            return _row_to_alert(row)

    async def list_recent_alerts(
        self, *, tenant_id: str, limit: int = 50
    ) -> list[Alert]:
        if limit <= 0:
            return []
        pool = _resolve_pool(self._pool)
        async with pool.acquire() as conn:
            await self._set_tenant(conn, tenant_id)
            rows = await conn.fetch(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT $1", int(limit)
            )
            return [_row_to_alert(r) for r in rows]

    # ------------------------------------------------------------------ outbox
    async def upsert_outbox_pending(self, alert: Alert) -> None:
        """Upsert an outbox row in 'pending' state.

        Called inside the same transaction as insert_alert in production.
        Here we keep it as a separate call so callers can choose whether
        the alert row's existence is a prerequisite (it always is, but
        the DB FK enforces it).
        """
        pool = _resolve_pool(self._pool)
        sql = """
            INSERT INTO alert_outbox (alert_id, tenant_id, status, attempts, enqueued_at, updated_at)
            VALUES ($1, $2, 'pending', 0, $3, $3)
            ON CONFLICT (alert_id) DO NOTHING
        """
        async with pool.acquire() as conn:
            await self._set_tenant(conn, str(alert.tenant_id))
            await conn.execute(sql, alert.alert_id, alert.tenant_id, utc_now())

    async def claim_pending(
        self, *, tenant_id: str, limit: int
    ) -> list[AlertOutboxRow]:
        """Atomically claim up to `limit` pending rows for this drainer.

        Uses SELECT … FOR UPDATE SKIP LOCKED to avoid contention between
        concurrent drainer instances. Rows are flipped to 'delivering'
        in the same transaction; subsequent drainers cannot see them.
        """
        if limit <= 0:
            return []
        pool = _resolve_pool(self._pool)
        # NOTE: we explicitly project o.attempts and o.enqueued_at so the
        # drainer sees the actual retry count (it uses that to compute
        # backoff). The JOIN aliases ensure no column shadowing.
        sql_select = """
            SELECT o.alert_id        AS o_alert_id,
                   o.attempts        AS o_attempts,
                   o.enqueued_at     AS o_enqueued_at,
                   o.last_error_code AS o_last_error_code,
                   a.*
            FROM alert_outbox o
            JOIN alerts a USING (alert_id)
            WHERE o.status = 'pending'
              AND (o.next_retry_at IS NULL OR o.next_retry_at <= NOW())
              AND o.tenant_id::text = current_setting('app.current_tenant', TRUE)
            ORDER BY o.enqueued_at ASC
            LIMIT $1
            FOR UPDATE OF o SKIP LOCKED
        """
        sql_flip = """
            UPDATE alert_outbox
               SET status = 'delivering',
                   updated_at = NOW()
             WHERE alert_id = ANY($1::text[])
        """
        async with pool.acquire() as conn, conn.transaction():
            await self._set_tenant(conn, tenant_id)
            rows = await conn.fetch(sql_select, int(limit))
            if not rows:
                return []
            ids = [r["o_alert_id"] for r in rows]
            await conn.execute(sql_flip, ids)
        # Build outbox rows outside the transaction.
        out: list[AlertOutboxRow] = []
        for r in rows:
            alert = _row_to_alert(r)
            out.append(
                AlertOutboxRow(
                    alert=alert,
                    status=AlertStatus.DELIVERING,
                    attempts=int(r["o_attempts"]),
                    next_retry_at=None,
                    last_error_code=r["o_last_error_code"],
                    enqueued_at=r["o_enqueued_at"],
                )
            )
        return out

    async def mark_delivered(self, *, tenant_id: str, alert_id: str) -> None:
        pool = _resolve_pool(self._pool)
        async with pool.acquire() as conn:
            await self._set_tenant(conn, tenant_id)
            await conn.execute(
                """
                UPDATE alert_outbox
                   SET status = 'delivered',
                       updated_at = NOW(),
                       last_error_code = NULL,
                       last_error_message = NULL
                 WHERE alert_id = $1
                """,
                alert_id,
            )

    async def mark_retry(
        self,
        *,
        tenant_id: str,
        alert_id: str,
        next_retry_at: datetime,
        attempts: int,
        error_code: str | None,
        error_message: str | None,
        terminal: bool = False,
    ) -> None:
        """Reschedule (terminal=False) or mark failed (terminal=True)."""
        pool = _resolve_pool(self._pool)
        status = "failed" if terminal else "pending"
        async with pool.acquire() as conn:
            await self._set_tenant(conn, tenant_id)
            await conn.execute(
                """
                UPDATE alert_outbox
                   SET status = $2,
                       attempts = $3,
                       next_retry_at = $4,
                       last_error_code = $5,
                       last_error_message = $6,
                       updated_at = NOW()
                 WHERE alert_id = $1
                """,
                alert_id,
                status,
                int(attempts),
                next_retry_at,
                error_code,
                error_message,
            )

    async def pending_count(self, *, tenant_id: str) -> int:
        pool = _resolve_pool(self._pool)
        async with pool.acquire() as conn:
            await self._set_tenant(conn, tenant_id)
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM alert_outbox WHERE status = 'pending'"
            )
            return int(row["n"]) if row else 0

    # ----------------------------------------------------------- deliveries
    async def insert_delivery(
        self, *, tenant_id: str, attempt: DeliveryAttempt
    ) -> int:
        pool = _resolve_pool(self._pool)
        async with pool.acquire() as conn:
            await self._set_tenant(conn, tenant_id)
            row = await conn.fetchrow(
                """
                INSERT INTO alert_deliveries (
                    alert_id, tenant_id, channel, attempt_no, status,
                    http_status, latency_ms, error_code, error_message, occurred_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                RETURNING delivery_id
                """,
                attempt.alert_id,
                tenant_id,
                attempt.channel,
                attempt.attempt_no,
                str(attempt.status),
                attempt.http_status,
                attempt.latency_ms,
                attempt.error_code,
                attempt.error_message,
                attempt.occurred_at,
            )
            return int(row["delivery_id"]) if row else 0

    async def list_deliveries(
        self, *, tenant_id: str, alert_id: str
    ) -> list[DeliveryAttempt]:
        pool = _resolve_pool(self._pool)
        async with pool.acquire() as conn:
            await self._set_tenant(conn, tenant_id)
            rows = await conn.fetch(
                "SELECT * FROM alert_deliveries WHERE alert_id = $1 ORDER BY occurred_at ASC",
                alert_id,
            )
            return [
                DeliveryAttempt(
                    alert_id=r["alert_id"],
                    channel=r["channel"],
                    attempt_no=r["attempt_no"],
                    status=DeliveryStatus(r["status"]),
                    http_status=r["http_status"],
                    latency_ms=float(r["latency_ms"]),
                    error_code=r["error_code"],
                    error_message=r["error_message"],
                    occurred_at=r["occurred_at"],
                )
                for r in rows
            ]

    # --------------------------------------------------------- intents
    async def insert_intent(self, intent: ExecutionIntent) -> bool:
        pool = _resolve_pool(self._pool)
        sql = """
            INSERT INTO execution_intents (
                intent_id, alert_id, tenant_id, trend_id, kind, status,
                advised_units, advised_capital_usd, expected_margin_usd,
                loss_probability, horizon_hours, rationale, created_at, expires_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14
            )
            ON CONFLICT (intent_id) DO NOTHING
            RETURNING intent_id
        """
        async with pool.acquire() as conn:
            await self._set_tenant(conn, str(intent.tenant_id))
            row = await conn.fetchrow(
                sql,
                intent.intent_id,
                intent.alert_id,
                intent.tenant_id,
                intent.trend_id,
                str(intent.kind),
                str(intent.status),
                intent.advised_units,
                intent.advised_capital_usd,
                intent.expected_margin_usd,
                intent.loss_probability,
                intent.horizon_hours,
                intent.rationale,
                intent.created_at,
                intent.expires_at,
            )
            return row is not None


# ---------------------------------------------------------------------------
# Internal: row → Alert reconstruction
# ---------------------------------------------------------------------------
def _row_to_alert(row: Any) -> Alert:
    """Convert an asyncpg Record (or dict-like) to an Alert.

    Tolerates extra keys (joined queries) and missing optional columns.
    """
    blocked_by_raw: Iterable[str] | None = row["blocked_by"]
    return Alert(
        alert_id=row["alert_id"],
        tenant_id=row["tenant_id"],
        trend_id=row["trend_id"],
        decision_window=row["decision_window"],
        verdict=row["verdict"],
        priority=int(row["priority"]),
        score=float(row["score"]),
        confidence=float(row["confidence"]),
        source=row["source"],
        p_breakout_24h=row["p_breakout_24h"],
        p_decline_6h=row["p_decline_6h"],
        p_saturation=row["p_saturation"],
        expected_margin_usd=row["expected_margin_usd"],
        loss_probability=row["loss_probability"],
        advised_units=row["advised_units"],
        advised_capital_usd=row["advised_capital_usd"],
        halt_reason=row["halt_reason"],
        blocked_by=tuple(blocked_by_raw or ()),
        title=row["title"],
        summary_text=row["summary_text"] or "",
        correlation_id=row["correlation_id"],
        created_at=row["created_at"],
    )


__all__ = ["AlertRepository"]
