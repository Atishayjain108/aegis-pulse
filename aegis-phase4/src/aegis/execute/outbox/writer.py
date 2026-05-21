"""Outbox writer.

Persists an Alert + outbox row in the right order so a crash between the
two writes cannot lose data:

  1. INSERT INTO alerts ... ON CONFLICT DO NOTHING   (idempotent)
  2. INSERT INTO alert_outbox ... ON CONFLICT DO NOTHING

Both share `alert_id`. The second insert is fenced by the first via FK,
so it can never reference a missing alert. The caller can safely retry
on transient errors.

This module does NOT itself open transactions across the two writes
because the repository methods already use short-lived transactions
internally. For strict atomicity, callers may wrap both calls in a
larger explicit transaction.
"""

from __future__ import annotations

import structlog

from aegis.execute.errors import EXEC_OUTBOX_INSERT_FAILED, AegisExecuteError
from aegis.execute.schemas.alert import Alert
from aegis.execute.store.repository import AlertRepository

_log = structlog.get_logger(__name__)


class OutboxWriter:
    """Thin coordinator on top of AlertRepository."""

    __slots__ = ("_repo",)

    def __init__(self, repository: AlertRepository) -> None:
        self._repo = repository

    async def enqueue(self, alert: Alert) -> bool:
        """Persist alert + outbox row. Returns True if NEW, False if dedup."""
        try:
            inserted = await self._repo.insert_alert(alert)
            # Always attempt outbox upsert: catches the (rare) crash where
            # the alert was inserted earlier but outbox row failed.
            await self._repo.upsert_outbox_pending(alert)
        except AegisExecuteError:
            raise
        except Exception as exc:
            _log.error(
                "execute.outbox.insert_failed",
                alert_id=alert.alert_id,
                trend_id=alert.trend_id,
                error=str(exc),
            )
            raise AegisExecuteError(
                EXEC_OUTBOX_INSERT_FAILED,
                cause=exc,
                context={"alert_id": alert.alert_id},
            ) from exc
        _log.info(
            "execute.outbox.enqueued",
            alert_id=alert.alert_id,
            trend_id=alert.trend_id,
            new_alert=inserted,
        )
        return inserted


__all__ = ["OutboxWriter"]
