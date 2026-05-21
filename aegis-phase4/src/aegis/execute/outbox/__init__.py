"""Outbox writer + drainer for at-least-once delivery.

Phase 4 implements the classic transactional outbox pattern:

1. ``OutboxWriter.enqueue(alert)`` inserts the alert row and a matching
   ``alert_outbox`` row inside one transaction — so an alert is durable
   the moment ``submit()`` returns.
2. ``Drainer`` is a long-lived asyncio task that periodically claims a
   batch of pending rows (``SELECT … FOR UPDATE SKIP LOCKED``), fans
   them out to every enabled notifier, and either marks each row
   delivered or schedules a retry with decorrelated-jitter backoff.

The two pieces are decoupled so the writer can run in any process
(API, CLI, batch jobs) while a dedicated drainer process owns the
fan-out. Killswitch state is checked on every drain tick.

Public surface:

* ``OutboxWriter`` — sync, called from the request / submission path.
* ``Drainer`` — async, long-lived, started as a background task.
"""

from __future__ import annotations

from aegis.execute.outbox.drainer import Drainer
from aegis.execute.outbox.writer import OutboxWriter

__all__ = ["Drainer", "OutboxWriter"]
