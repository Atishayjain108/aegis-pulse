"""CONN-1 — unified cross-phase event bus.

Phases 7/8/9 (geo, compliance, evolve) historically produced results that flowed
nowhere: a REST call returned JSON to the caller and the insight evaporated. This
helper lets each phase publish its result onto a capped Redis stream that mirrors
the established Phase 2 pattern (``XADD stream {"body": <json>}``, ``maxlen``
approximate), so the dashboard and data lake can consume a single, uniform feed.

Every call is **best-effort**: a publish failure (no Redis, network blip) is
logged and swallowed — emitting an analytics event must never break the request
that produced it.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

_log = structlog.get_logger("aegis.core.event_bus")

# Canonical per-phase stream names (mirror "aegis:phase2:graph_results").
STREAM_GEO = "aegis:phase7:geo_opportunities"
STREAM_COMPLIANCE = "aegis:phase8:compliance_assessments"
STREAM_EVOLVE = "aegis:phase9:evolve_events"

_DEFAULT_MAXLEN = 10_000

# Module-level cached async client — these endpoints are low-frequency, so one
# lazily-built shared connection is plenty and avoids per-request churn.
class _ClientHolder:
    """Lazily-built shared async Redis client.

    Holding state on an instance keeps the module free of ``global`` statements.
    """

    def __init__(self) -> None:
        self.client: Any = None
        self.failed = False

    async def get(self) -> Any:
        if self.client is not None or self.failed:
            return self.client
        try:
            import redis.asyncio as aioredis

            from aegis.config import settings

            self.client = aioredis.from_url(settings().redis_url_str, decode_responses=True)
        except Exception as exc:  # pragma: no cover - defensive
            self.failed = True
            _log.debug("event_bus.client_init_failed", error=str(exc))
            self.client = None
        return self.client

    def reset(self) -> None:
        self.client = None
        self.failed = False


_HOLDER = _ClientHolder()


async def publish_event(
    stream: str,
    payload: dict[str, Any],
    *,
    maxlen: int = _DEFAULT_MAXLEN,
    redis_client: Any = None,
) -> bool:
    """Publish one event to a phase stream. Returns True on success.

    Never raises — emitting an event must not break the producing request.
    """
    client = redis_client if redis_client is not None else await _HOLDER.get()
    if client is None:
        return False
    try:
        await client.xadd(
            stream,
            {"body": json.dumps(payload, default=str)},
            maxlen=maxlen,
            approximate=True,
        )
        return True
    except Exception as exc:
        _log.debug("event_bus.publish_failed", stream=stream, error=str(exc))
        return False


def reset_client() -> None:
    """Drop the cached client (test/maintenance hook)."""
    _HOLDER.reset()


__all__ = [
    "STREAM_COMPLIANCE",
    "STREAM_EVOLVE",
    "STREAM_GEO",
    "publish_event",
    "reset_client",
]
