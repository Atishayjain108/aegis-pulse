"""Event bus tests."""

from __future__ import annotations

import asyncio

from aegis.execute.bus import EventBus, Subscriber
from aegis.execute.schemas.dashboard import SSEEvent


async def test_subscribe_and_receive():
    bus = EventBus()
    sub = await bus.subscribe()
    e = SSEEvent(event="x", data={"k": "v"})
    delivered = await bus.publish(e)
    assert delivered == 1
    received = await asyncio.wait_for(sub.iter_events().__anext__(), timeout=0.5)
    assert received.event == "x"
    assert received.data == {"k": "v"}


async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    sub = await bus.subscribe()
    await bus.unsubscribe(sub)
    delivered = await bus.publish(SSEEvent(event="x", data={}))
    assert delivered == 0


async def test_multiple_subscribers_each_receive():
    bus = EventBus()
    s1 = await bus.subscribe()
    s2 = await bus.subscribe()
    e = SSEEvent(event="fan", data={})
    n = await bus.publish(e)
    assert n == 2
    assert (await asyncio.wait_for(s1.iter_events().__anext__(), timeout=0.5)).event == "fan"
    assert (await asyncio.wait_for(s2.iter_events().__anext__(), timeout=0.5)).event == "fan"


async def test_overflow_drops_oldest():
    sub = Subscriber("sub-test", max_q=2)
    # Fill the queue
    assert await sub.put(SSEEvent(event="a", data={"i": 1})) is True
    assert await sub.put(SSEEvent(event="b", data={"i": 2})) is True
    # Third forces a drop of the oldest, then enqueues.
    assert await sub.put(SSEEvent(event="c", data={"i": 3})) is True
    assert sub.dropped_count == 1
    # Now consume — should see b, c (a was dropped).
    seen = []
    seen.append(await asyncio.wait_for(sub.iter_events().__anext__(), timeout=0.5))
    seen.append(await asyncio.wait_for(sub.iter_events().__anext__(), timeout=0.5))
    assert [s.data["i"] for s in seen] == [2, 3]
