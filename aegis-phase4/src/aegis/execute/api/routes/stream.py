"""Server-Sent Events stream endpoint.

Each connected client receives every `SSEEvent` published to the bus from
the moment it connects. Closed connections are cleaned up automatically.

We intentionally avoid SSE keepalives shorter than 15 s — most proxies
are configured to tolerate 30 s idle.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from aegis.execute.api.auth import require_bearer
from aegis.execute.bus import EventBus
from aegis.execute.constants import SSE_KEEPALIVE_INTERVAL_S
from aegis.execute.schemas.dashboard import SSEEvent
from aegis.execute.utils.time import utc_now

router = APIRouter(tags=["stream"])


def _bus(request: Request) -> EventBus:
    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="event bus not initialised",
        )
    return bus


def _format_sse(event: SSEEvent) -> bytes:
    """Encode an SSEEvent as the SSE text frame."""
    payload = {
        "event": event.event,
        "data": event.data,
        "occurred_at": event.occurred_at.isoformat(),
    }
    encoded = json.dumps(payload, separators=(",", ":"))
    return f"event: {event.event}\ndata: {encoded}\n\n".encode()


async def _event_iterator(bus: EventBus, request: Request) -> AsyncIterator[bytes]:
    sub = await bus.subscribe()
    try:
        # Initial connect comment line — keeps proxies happy.
        yield b": connected\n\n"
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(
                    sub.iter_events().__anext__(),  # type: ignore[attr-defined]
                    timeout=SSE_KEEPALIVE_INTERVAL_S,
                )
                yield _format_sse(event)
            except TimeoutError:
                # Keepalive comment line.
                yield b": keepalive\n\n"
                continue
            except (StopAsyncIteration, asyncio.CancelledError):
                return
    finally:
        await bus.unsubscribe(sub)


@router.get("/stream")
async def stream(
    request: Request,
    _principal: Annotated[str, Depends(require_bearer)],
) -> StreamingResponse:
    bus = _bus(request)
    # Send a startup tick so clients know the stream is live.
    await bus.publish(
        SSEEvent(
            event="stream.opened",
            data={"opened_at": utc_now().isoformat()},
        )
    )
    return StreamingResponse(
        _event_iterator(bus, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # NGINX
        },
    )


__all__ = ["router"]
