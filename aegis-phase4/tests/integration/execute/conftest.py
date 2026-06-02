"""Integration test fixtures.

`FakeRepository` is a 100% in-memory implementation of the same public
surface as `AlertRepository`. It mimics every SQL behaviour we depend on
(idempotent inserts via alert_id PK, FOR UPDATE SKIP LOCKED semantics
serialised via asyncio.Lock, etc.).

This lets us exercise the full pipeline + drainer + API stack end-to-end
without a Postgres dependency.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest
from aegis.execute.schemas.alert import (
    Alert,
    AlertOutboxRow,
    AlertStatus,
    DeliveryAttempt,
    DeliveryStatus,
)
from aegis.execute.schemas.intent import ExecutionIntent
from aegis.execute.utils.time import utc_now


class FakeRepository:
    """In-memory mimic of AlertRepository."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # alert_id → Alert
        self.alerts: dict[str, Alert] = {}
        # alert_id → dict (outbox row state)
        self.outbox: dict[str, dict[str, Any]] = {}
        # list of DeliveryAttempt
        self.deliveries: list[DeliveryAttempt] = []
        # intent_id → ExecutionIntent
        self.intents: dict[str, ExecutionIntent] = {}

    # --- alerts ---
    async def insert_alert(self, alert: Alert) -> bool:
        async with self._lock:
            if alert.alert_id in self.alerts:
                return False
            self.alerts[alert.alert_id] = alert
            return True

    async def get_alert(self, *, tenant_id: str, alert_id: str) -> Alert | None:
        a = self.alerts.get(alert_id)
        if a is None:
            return None
        if str(a.tenant_id) != str(tenant_id):
            return None  # RLS mimic
        return a

    async def list_recent_alerts(
        self, *, tenant_id: str, limit: int = 50
    ) -> list[Alert]:
        if limit <= 0:
            return []
        out = [
            a for a in self.alerts.values() if str(a.tenant_id) == str(tenant_id)
        ]
        out.sort(key=lambda a: a.created_at, reverse=True)
        return out[:limit]

    # --- outbox ---
    async def upsert_outbox_pending(self, alert: Alert) -> None:
        async with self._lock:
            if alert.alert_id in self.outbox:
                return
            self.outbox[alert.alert_id] = {
                "alert_id": alert.alert_id,
                "tenant_id": str(alert.tenant_id),
                "status": "pending",
                "attempts": 0,
                "next_retry_at": None,
                "last_error_code": None,
                "last_error_message": None,
                "enqueued_at": utc_now(),
            }

    async def claim_pending(
        self, *, tenant_id: str, limit: int
    ) -> list[AlertOutboxRow]:
        if limit <= 0:
            return []
        async with self._lock:
            now = utc_now()
            claimed: list[tuple[str, dict[str, Any]]] = []
            # Stable ordering by enqueued_at
            candidates = sorted(
                (
                    (k, row)
                    for k, row in self.outbox.items()
                    if row["status"] == "pending"
                    and (row["next_retry_at"] is None or row["next_retry_at"] <= now)
                    and row["tenant_id"] == str(tenant_id)
                ),
                key=lambda kv: kv[1]["enqueued_at"],
            )
            for k, row in candidates[:limit]:
                row["status"] = "delivering"
                claimed.append((k, row))
            out: list[AlertOutboxRow] = []
            for k, row in claimed:
                alert = self.alerts[k]
                out.append(
                    AlertOutboxRow(
                        alert=alert,
                        status=AlertStatus.DELIVERING,
                        attempts=row["attempts"],
                        next_retry_at=None,
                        last_error_code=row["last_error_code"],
                        enqueued_at=row["enqueued_at"],
                    )
                )
            return out

    async def mark_delivered(self, *, tenant_id: str, alert_id: str) -> None:
        async with self._lock:
            row = self.outbox.get(alert_id)
            if row is None:
                return
            row["status"] = "delivered"
            row["last_error_code"] = None
            row["last_error_message"] = None

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
        async with self._lock:
            row = self.outbox.get(alert_id)
            if row is None:
                return
            row["status"] = "failed" if terminal else "pending"
            row["attempts"] = attempts
            row["next_retry_at"] = next_retry_at
            row["last_error_code"] = error_code
            row["last_error_message"] = error_message

    async def pending_count(self, *, tenant_id: str) -> int:
        return sum(
            1
            for r in self.outbox.values()
            if r["status"] == "pending" and r["tenant_id"] == str(tenant_id)
        )

    # --- deliveries ---
    async def insert_delivery(
        self, *, tenant_id: str, attempt: DeliveryAttempt
    ) -> int:
        async with self._lock:
            self.deliveries.append(attempt)
            return len(self.deliveries)

    async def list_deliveries(
        self, *, tenant_id: str, alert_id: str
    ) -> list[DeliveryAttempt]:
        return [d for d in self.deliveries if d.alert_id == alert_id]

    # --- intents ---
    async def insert_intent(self, intent: ExecutionIntent) -> bool:
        async with self._lock:
            if intent.intent_id in self.intents:
                return False
            self.intents[intent.intent_id] = intent
            return True


@pytest.fixture
def fake_repo() -> FakeRepository:
    return FakeRepository()


# Convenience: a recording notifier used by tests.
class RecordingNotifier:
    """Captures every envelope, can be configured to fail N times first."""

    def __init__(
        self,
        *,
        name: str = "rec",
        enabled: bool = True,
        fail_first_n: int = 0,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.name = name
        self.enabled = enabled
        self.timeout_s = 1.0
        self._fail_first_n = fail_first_n
        self._raise = raise_exc
        self.calls = 0
        self.received = []

    async def send(self, envelope):
        self.calls += 1
        self.received.append(envelope)
        if self._raise is not None and self.calls <= self._fail_first_n:
            raise self._raise
        if self.calls <= self._fail_first_n:
            return _result(self.name, DeliveryStatus.FAILURE, "AEGIS-EXEC-0031", "stub fail")
        return _result(self.name, DeliveryStatus.SUCCESS, None, None)

    async def aclose(self) -> None:
        return None


def _result(name, status, code, msg):
    from aegis.execute.schemas.notification import NotificationResult

    return NotificationResult(
        channel=name,
        status=status,
        http_status=None,
        latency_ms=0.0,
        error_code=code,
        error_message=msg,
    )
