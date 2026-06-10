"""Outbox drainer worker.

Runs as a long-lived asyncio task. On each tick:

  1. Claims up to N pending rows (status=pending, retry-due) using
     SELECT … FOR UPDATE SKIP LOCKED → flips them to 'delivering'.
  2. For each row, fans out to every enabled notifier concurrently.
  3. Records each attempt in `alert_deliveries`.
  4. If ALL notifiers succeeded → mark outbox row 'delivered'.
     If at least one failed and attempts < max_attempts → reschedule
     with decorrelated jitter.
     If at least one failed and attempts >= max_attempts → mark 'failed'.

The drainer is killswitch-aware: it polls the switch at the top of every
tick and yields (no claim) while tripped.

Designed to be safe with multiple concurrent drainer instances: row
locking + SKIP LOCKED prevents double-claim.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from contextlib import suppress

import structlog

from aegis.execute.constants import OUTBOX_BACKOFF_BASE_S, OUTBOX_BACKOFF_MAX_S
from aegis.execute.errors import AegisExecuteError
from aegis.execute.killswitch.switch import KillSwitch
from aegis.execute.metrics import metrics
from aegis.execute.notifiers.base import Notifier
from aegis.execute.schemas.alert import (
    Alert,
    AlertEnvelope,
    DeliveryAttempt,
    DeliveryStatus,
)
from aegis.execute.store.repository import AlertRepository
from aegis.execute.utils.backoff import next_backoff_seconds
from aegis.execute.utils.time import utc_now

_log = structlog.get_logger(__name__)


class Drainer:
    """Outbox drainer.

    Parameters
    ----------
    repository : AlertRepository
        Storage layer.
    notifiers : Iterable[Notifier]
        Enabled notifier channels. Must be non-empty — but the trivial
        `LogNotifier` is always available, so this is a soft requirement.
    killswitch : KillSwitch
        Global halt control.
    tenant_id : str
        RLS tenant context.
    drain_interval_s : float
        Poll cadence.
    drain_batch : int
        Max rows per cycle.
    max_attempts : int
        Retry ceiling per row.
    """

    __slots__ = (
        "_batch",
        "_interval_s",
        "_killswitch",
        "_max_attempts",
        "_notifiers",
        "_repo",
        "_stop",
        "_task",
        "_tenant_id",
    )

    def __init__(
        self,
        *,
        repository: AlertRepository,
        notifiers: Iterable[Notifier],
        killswitch: KillSwitch,
        tenant_id: str,
        drain_interval_s: float,
        drain_batch: int,
        max_attempts: int,
    ) -> None:
        self._repo = repository
        self._notifiers: list[Notifier] = [n for n in notifiers if n.enabled]
        self._killswitch = killswitch
        self._tenant_id = tenant_id
        self._interval_s = float(drain_interval_s)
        self._batch = int(drain_batch)
        self._max_attempts = int(max_attempts)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def notifier_count(self) -> int:
        return len(self._notifiers)

    async def start(self) -> None:
        """Start the drainer loop as a background task."""
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="execute-drainer")
        _log.info(
            "execute.drainer.started",
            tenant_id=self._tenant_id,
            notifier_count=self.notifier_count,
            interval_s=self._interval_s,
            batch=self._batch,
        )

    async def stop(self) -> None:
        """Stop the drainer loop cooperatively."""
        self._stop.set()
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        _log.info("execute.drainer.stopped", tenant_id=self._tenant_id)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.error("execute.drainer.tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except TimeoutError:
                continue

    async def _tick(self) -> None:
        """One drainer cycle."""
        # 1. Kill-switch first. If tripped, do nothing.
        tripped = await self._killswitch.is_tripped()
        with suppress(Exception):
            metrics.killswitch_tripped.set(1.0 if tripped else 0.0)
        if tripped:
            _log.debug("execute.drainer.killswitch_tripped")
            return

        # 2. Claim pending rows.
        rows = await self._repo.claim_pending(
            tenant_id=self._tenant_id, limit=self._batch
        )
        # Update the outbox-pending gauge using a fresh count so the
        # dashboard reflects backlog accurately even mid-tick.
        try:
            depth = await self._repo.pending_count(tenant_id=self._tenant_id)
            metrics.outbox_pending.labels(tenant=str(self._tenant_id)).set(float(depth))
        except Exception:
            pass
        if not rows:
            return
        _log.info("execute.drainer.claimed", count=len(rows))

        # 3. Fan-out per alert. Each alert is handled fully before the next
        # so retries don't interleave across alerts unpredictably.
        for outbox_row in rows:
            await self._dispatch_one(outbox_row.alert, outbox_row.attempts)

    async def _dispatch_one(self, alert: Alert, prior_attempts: int) -> None:
        """Dispatch a single alert to all enabled notifiers."""
        envelope = AlertEnvelope(alert=alert)
        attempts_for_logging = prior_attempts + 1

        if not self._notifiers:
            # No notifiers at all → consider delivered (LogNotifier is
            # always added by the factory; this is a defensive path).
            _log.warning(
                "execute.drainer.no_notifiers_marking_delivered",
                alert_id=alert.alert_id,
            )
            await self._repo.mark_delivered(
                tenant_id=self._tenant_id, alert_id=alert.alert_id
            )
            return

        results = await asyncio.gather(
            *(self._call_one(n, envelope, attempts_for_logging) for n in self._notifiers),
            return_exceptions=False,
        )
        any_failed = any(r.status != DeliveryStatus.SUCCESS for r in results)
        if not any_failed:
            await self._repo.mark_delivered(
                tenant_id=self._tenant_id, alert_id=alert.alert_id
            )
            _log.info(
                "execute.drainer.delivered",
                alert_id=alert.alert_id,
                trend_id=alert.trend_id,
                channels=[r.channel for r in results],
            )
            return

        # Some failed.
        terminal = attempts_for_logging >= self._max_attempts
        backoff = next_backoff_seconds(
            previous=max(0.0, OUTBOX_BACKOFF_BASE_S * (2 ** prior_attempts)),
            base=OUTBOX_BACKOFF_BASE_S,
            cap=OUTBOX_BACKOFF_MAX_S,
        )
        next_at = utc_now().replace(microsecond=0)
        # Schedule next attempt `backoff` seconds in the future.
        from datetime import timedelta

        next_at = next_at + timedelta(seconds=backoff)
        first_failed = next(r for r in results if r.status != DeliveryStatus.SUCCESS)
        await self._repo.mark_retry(
            tenant_id=self._tenant_id,
            alert_id=alert.alert_id,
            next_retry_at=next_at,
            attempts=attempts_for_logging,
            error_code=first_failed.error_code,
            error_message=first_failed.error_message,
            terminal=terminal,
        )
        _log.warning(
            "execute.drainer.partial_failure" if not terminal else "execute.drainer.terminal_failure",
            alert_id=alert.alert_id,
            attempts=attempts_for_logging,
            next_retry_at=next_at.isoformat() if not terminal else None,
            first_error=first_failed.error_code,
        )

    async def _call_one(
        self, notifier: Notifier, envelope: AlertEnvelope, attempt_no: int
    ) -> DeliveryAttempt:
        """Call one notifier; persist the attempt; return it."""
        start = time.monotonic()
        try:
            result = await notifier.send(envelope)
        except AegisExecuteError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            attempt = DeliveryAttempt(
                alert_id=envelope.alert.alert_id,
                channel=notifier.name,
                attempt_no=attempt_no,
                status=DeliveryStatus.FAILURE,
                http_status=None,
                latency_ms=elapsed_ms,
                error_code=exc.spec.code,
                error_message=exc.spec.message,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            attempt = DeliveryAttempt(
                alert_id=envelope.alert.alert_id,
                channel=notifier.name,
                attempt_no=attempt_no,
                status=DeliveryStatus.FAILURE,
                http_status=None,
                latency_ms=elapsed_ms,
                error_code="AEGIS-EXEC-9999",
                error_message=str(exc)[:1000],
            )
        else:
            attempt = DeliveryAttempt(
                alert_id=envelope.alert.alert_id,
                channel=notifier.name,
                attempt_no=attempt_no,
                status=result.status,
                http_status=result.http_status,
                latency_ms=result.latency_ms,
                error_code=result.error_code,
                error_message=result.error_message,
            )
        # Best-effort persistence; failures here are non-fatal.
        try:
            await self._repo.insert_delivery(
                tenant_id=self._tenant_id, attempt=attempt
            )
        except Exception as exc:
            _log.error(
                "execute.drainer.delivery_log_failed",
                alert_id=envelope.alert.alert_id,
                channel=notifier.name,
                error=str(exc),
            )
        # Per-delivery metrics (status label is the lowercase enum value).
        try:
            metrics.deliveries_total.labels(
                channel=notifier.name,
                status=str(attempt.status.value if hasattr(attempt.status, "value") else attempt.status),
            ).inc()
            metrics.delivery_latency_ms.labels(channel=notifier.name).observe(
                float(attempt.latency_ms)
            )
        except Exception:
            pass
        return attempt


__all__ = ["Drainer"]
