"""In-process pub-sub event bus for SSE streaming.

A single `EventBus` instance lives in the FastAPI app state. The drainer
(or any internal producer) calls `publish(SSEEvent)`. Each connected SSE
client owns a subscriber queue created by `subscribe()`; the queue is
async-iterable.

The bus is intentionally NOT persistent — SSE clients receive only events
that happen after they connect. Replay is achieved by querying the alerts
table.

Per-client queues are bounded by `SSE_MAX_QUEUE`. When a client falls
behind, the oldest events are dropped silently and a `bus.subscriber_drop`
log line is emitted. This prevents one slow client from backpressuring the
producer.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Final

import structlog

from aegis.execute.constants import SSE_MAX_QUEUE
from aegis.execute.schemas.dashboard import SSEEvent

_log = structlog.get_logger(__name__)


class Subscriber:
    """One SSE client's queue."""

    __slots__ = ("_dropped", "_id", "_q")

    def __init__(self, sub_id: str, max_q: int = SSE_MAX_QUEUE) -> None:
        self._q: asyncio.Queue[SSEEvent] = asyncio.Queue(maxsize=max_q)
        self._id = sub_id
        self._dropped = 0

    @property
    def subscriber_id(self) -> str:
        return self._id

    @property
    def dropped_count(self) -> int:
        return self._dropped

    async def put(self, event: SSEEvent) -> bool:
        """Enqueue. Drop oldest on overflow. Return True if delivered."""
        try:
            self._q.put_nowait(event)
            return True
        except asyncio.QueueFull:
            # Drop the oldest, push the new one. Best-effort.
            with contextlib.suppress(asyncio.QueueEmpty):  # pragma: no cover - racy
                self._q.get_nowait()
            self._dropped += 1
            try:
                self._q.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - racy
                return False
            _log.warning(
                "bus.subscriber_drop",
                subscriber_id=self._id,
                dropped_total=self._dropped,
            )
            return True

    async def iter_events(self) -> AsyncIterator[SSEEvent]:
        """Yield events forever. Cancel the consuming task to stop."""
        while True:
            event = await self._q.get()
            yield event


class EventBus:
    """Fan-out hub.

    Thread-safety: a single `asyncio.Lock` guards mutations of the
    subscriber set; `publish` is wait-free for subscribers (each put is
    non-blocking on a bounded queue).
    """

    __slots__ = ("_lock", "_next_id", "_subs")

    def __init__(self) -> None:
        self._subs: dict[str, Subscriber] = {}
        self._lock = asyncio.Lock()
        self._next_id = 0

    async def subscribe(self) -> Subscriber:
        async with self._lock:
            self._next_id += 1
            sub = Subscriber(sub_id=f"sub-{self._next_id}")
            self._subs[sub.subscriber_id] = sub
        _log.info("bus.subscribed", subscriber_id=sub.subscriber_id, total=len(self._subs))
        return sub

    async def unsubscribe(self, sub: Subscriber) -> None:
        async with self._lock:
            self._subs.pop(sub.subscriber_id, None)
        _log.info("bus.unsubscribed", subscriber_id=sub.subscriber_id, total=len(self._subs))

    async def publish(self, event: SSEEvent) -> int:
        """Fan-out to all subscribers. Returns count delivered."""
        # Snapshot under lock so we don't hold it during sends.
        async with self._lock:
            targets = list(self._subs.values())
        delivered = 0
        for sub in targets:
            if await sub.put(event):
                delivered += 1
        return delivered

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


__all__: Final = ["EventBus", "Subscriber"]
