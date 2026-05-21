"""Per-(tenant, channel) throttle.

Hard ceiling on notifications sent per minute. Prevents a runaway loop
from spamming downstream channels. Like the deduper, gracefully degrades
to a local fixed-window counter if Redis is unavailable.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Protocol, runtime_checkable
from uuid import UUID

import structlog

from aegis.execute.constants import THROTTLE_KEY_PREFIX, THROTTLE_PER_CHANNEL_PER_MIN

_log = structlog.get_logger(__name__)


@runtime_checkable
class _RedisLike(Protocol):
    async def incr(self, name: str) -> int: ...
    async def expire(self, name: str, time: int) -> bool: ...


class Throttle:
    """Fixed-window rate limiter, 60-second buckets."""

    __slots__ = ("_local", "_per_minute", "_prefix", "_redis")

    def __init__(
        self,
        *,
        redis_client: _RedisLike | None = None,
        per_minute: int = THROTTLE_PER_CHANNEL_PER_MIN,
        prefix: str = THROTTLE_KEY_PREFIX,
    ) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be > 0")
        self._redis = redis_client
        self._per_minute = int(per_minute)
        self._prefix = prefix
        # Local fallback: bucket key → count
        self._local: dict[str, int] = defaultdict(int)

    def _bucket_key(self, tenant_id: UUID, channel: str) -> str:
        now_min = int(time.time() // 60)
        return f"{self._prefix}{tenant_id}:{channel}:{now_min}"

    async def allow(self, tenant_id: UUID, channel: str) -> bool:
        """Increment counter and return True iff within the per-minute budget."""
        key = self._bucket_key(tenant_id, channel)
        if self._redis is not None:
            try:
                new_val = await self._redis.incr(key)
                if new_val == 1:
                    await self._redis.expire(key, 60)
                return new_val <= self._per_minute
            except Exception as exc:  # pragma: no cover
                _log.warning(
                    "throttle.redis_unavailable_falling_back",
                    error=str(exc),
                    channel=channel,
                )
        # Local fallback (process-local; not cluster-safe).
        self._local[key] += 1
        return self._local[key] <= self._per_minute


__all__ = ["Throttle"]
