"""Idempotency dedupe via Redis SET NX with TTL.

Used by the dispatch path to suppress identical alerts within `dedup_ttl_s`.
Falls back to a process-local in-memory cache if Redis is unavailable —
this is intentional: the database outbox provides the durable dedupe; this
layer is a fast-path optimisation.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

import structlog

from aegis.execute.constants import DEDUP_DEFAULT_TTL_S, DEDUP_KEY_PREFIX

_log = structlog.get_logger(__name__)


@runtime_checkable
class _RedisLike(Protocol):
    """Minimal Redis interface we depend on (sync `set` + `ttl`)."""

    async def set(self, name: str, value: str, *args, **kwargs) -> bool | None:
        ...


class Deduper:
    """Idempotency cache.

    `should_emit(alert_id)` is the only public method. Returns True the
    first time, False on subsequent calls within the TTL.

    `redis_client` is optional. If None, a process-local dict is used.
    """

    __slots__ = ("_local", "_prefix", "_redis", "_ttl_s")

    def __init__(
        self,
        *,
        redis_client: _RedisLike | None = None,
        ttl_s: int = DEDUP_DEFAULT_TTL_S,
        prefix: str = DEDUP_KEY_PREFIX,
    ) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be > 0")
        self._redis = redis_client
        self._ttl_s = int(ttl_s)
        self._prefix = prefix
        # Local fallback: alert_id → expiry epoch seconds.
        self._local: dict[str, float] = {}

    def _key(self, alert_id: str) -> str:
        return f"{self._prefix}{alert_id}"

    async def should_emit(self, alert_id: str) -> bool:
        """Return True iff this is the first time we see `alert_id` in TTL."""
        if not alert_id:
            return False
        if self._redis is not None:
            try:
                # NX = only set if absent; EX = TTL; returns True if set, None otherwise
                res = await self._redis.set(
                    self._key(alert_id), "1", nx=True, ex=self._ttl_s
                )
                return bool(res)
            except Exception as exc:  # pragma: no cover - exercised in tests via stub
                _log.warning(
                    "deduper.redis_unavailable_falling_back",
                    error=str(exc),
                    alert_id=alert_id,
                )
        # Local fallback
        now = time.monotonic()
        self._sweep_local(now)
        if alert_id in self._local and self._local[alert_id] > now:
            return False
        self._local[alert_id] = now + self._ttl_s
        return True

    def _sweep_local(self, now: float) -> None:
        # Cheap GC: remove expired entries; bounded by dict size.
        if not self._local:
            return
        # Avoid mutating dict during iteration.
        expired = [k for k, exp in self._local.items() if exp <= now]
        for k in expired:
            self._local.pop(k, None)


__all__ = ["Deduper"]
