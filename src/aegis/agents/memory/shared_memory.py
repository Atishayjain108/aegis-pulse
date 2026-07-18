"""
Shared working memory.

Lifetime: bounded by the trend's TTL (default 24h). Backed by a
Redis Hash keyed by `aegis:wm:{tenant_id}:{trend_id}` plus a sorted
set tracking "active trends" so the supervisor can sweep stale ones.

This is intentionally *small* and *fast*. Agents leave each other
short structured breadcrumbs — not full payloads.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:  # pragma: no cover
    from redis.asyncio import Redis as RedisClient
else:  # at runtime
    try:
        from redis.asyncio import Redis as RedisClient  # type: ignore
    except ImportError:  # pragma: no cover
        RedisClient = Any  # type: ignore[misc,assignment]


_log = structlog.get_logger("aegis.agents.memory.shared")

_DEFAULT_TTL = 24 * 60 * 60  # 24h
_ACTIVE_KEY = "aegis:wm:active"


class SharedWorkingMemory:
    """Thin wrapper over a Redis Hash with TTL refresh and atomic ops."""

    def __init__(
        self,
        redis: RedisClient,
        *,
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> None:
        self._redis = redis
        self._ttl = int(ttl_seconds)

    @staticmethod
    def _key(tenant_id: str, trend_id: str) -> str:
        return f"aegis:wm:{tenant_id}:{trend_id}"

    async def put(
        self,
        tenant_id: str,
        trend_id: str,
        field: str,
        value: Any,
    ) -> None:
        """Set a single field. Resets TTL. Refreshes active-trend score."""
        key = self._key(tenant_id, trend_id)
        encoded = _encode(value)
        pipe = self._redis.pipeline()
        pipe.hset(key, field, encoded)
        pipe.expire(key, self._ttl)
        pipe.zadd(_ACTIVE_KEY, {f"{tenant_id}:{trend_id}": time.time()})
        try:
            await pipe.execute()
        except Exception:
            _log.exception("wm.put_failed", tenant_id=tenant_id, trend_id=trend_id, field=field)

    async def get(
        self,
        tenant_id: str,
        trend_id: str,
        field: str,
    ) -> Any | None:
        try:
            raw = await self._redis.hget(self._key(tenant_id, trend_id), field)
        except Exception:
            _log.exception("wm.get_failed", tenant_id=tenant_id, trend_id=trend_id)
            return None
        if raw is None:
            return None
        return _decode(raw)

    async def get_all(
        self,
        tenant_id: str,
        trend_id: str,
    ) -> dict[str, Any]:
        try:
            raw = await self._redis.hgetall(self._key(tenant_id, trend_id))
        except Exception:
            _log.exception("wm.getall_failed", tenant_id=tenant_id, trend_id=trend_id)
            return {}
        return {_str(k): _decode(v) for k, v in raw.items()}

    async def update_many(
        self,
        tenant_id: str,
        trend_id: str,
        fields: dict[str, Any],
    ) -> None:
        if not fields:
            return
        key = self._key(tenant_id, trend_id)
        mapping = {k: _encode(v) for k, v in fields.items()}
        pipe = self._redis.pipeline()
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, self._ttl)
        pipe.zadd(_ACTIVE_KEY, {f"{tenant_id}:{trend_id}": time.time()})
        try:
            await pipe.execute()
        except Exception:
            _log.exception("wm.update_many_failed", tenant_id=tenant_id, trend_id=trend_id)

    async def delete(self, tenant_id: str, trend_id: str) -> None:
        try:
            await self._redis.delete(self._key(tenant_id, trend_id))
            await self._redis.zrem(_ACTIVE_KEY, f"{tenant_id}:{trend_id}")
        except Exception:
            _log.exception("wm.delete_failed", tenant_id=tenant_id, trend_id=trend_id)

    async def active_trends(
        self,
        *,
        max_age_seconds: int | None = None,
        limit: int = 100,
    ) -> list[tuple[str, str]]:
        """Return up to `limit` active (tenant_id, trend_id) tuples."""
        try:
            now = time.time()
            min_score = (now - max_age_seconds) if max_age_seconds else 0
            raw = await self._redis.zrangebyscore(
                _ACTIVE_KEY,
                min_score,
                "+inf",
                start=0,
                num=limit,
            )
        except Exception:
            _log.exception("wm.active_failed")
            return []
        out: list[tuple[str, str]] = []
        for entry in raw:
            s = _str(entry)
            if ":" in s:
                tenant_id, trend_id = s.split(":", 1)
                out.append((tenant_id, trend_id))
        return out


def _encode(value: Any) -> str:
    """JSON-encode anything safely; fall back to str() for unknowns."""
    try:
        return json.dumps(value, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return json.dumps(str(value))


def _decode(raw: bytes | str) -> Any:
    s = _str(raw)
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return s


def _str(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)
