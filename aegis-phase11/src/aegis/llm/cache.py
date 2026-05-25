"""
aegis.llm.cache — LLM Response Cache
======================================

Two-layer cache for LLM responses:
  1. In-process LRU cache (instant, zero latency) — for identical prompts
  2. Redis cache (shared across processes, survives restarts) — for near-identical prompts

Cache key is a sha256 of (provider_hint, model_hint, messages_json, temperature).
Exact match only — no fuzzy/semantic caching (that requires an embedding call).

Cache is intentionally conservative:
  - Only caches successful, non-streaming responses.
  - Never caches guardrail-blocked responses.
  - TTL defaults to 1 hour — LLM outputs go stale faster than data.
  - Cache is bypassed when ``temperature > 0.5`` (high-entropy outputs).

Author: AEGIS Engineering
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

import structlog

from aegis.llm.gateway.response import LLMResponse, TokenUsage

_log = structlog.get_logger("aegis.llm.cache")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TTL_S: int = 3600          # 1 hour
_LRU_MAX_SIZE: int = 256             # In-process LRU cap (entries, not bytes)
_REDIS_KEY_PREFIX: str = "aegis:llm:cache:"
_MAX_TEMPERATURE_FOR_CACHING: float = 0.5  # Don't cache high-entropy outputs


# ---------------------------------------------------------------------------
# Cache key computation
# ---------------------------------------------------------------------------


def _make_cache_key(
    messages: list[dict[str, str]],
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
) -> str:
    """
    Compute a deterministic cache key for a completion request.

    The key is a truncated sha256 of the serialised request parameters.
    Provider and model are included so the same prompt on different
    providers produces different cache entries.
    """
    payload = json.dumps(
        {
            "messages": messages,
            "provider": provider or "",
            "model": model or "",
            "temperature": round(temperature, 2),
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _response_to_dict(response: LLMResponse) -> dict[str, Any]:
    """Serialise an ``LLMResponse`` to a JSON-safe dict for Redis storage."""
    return {
        "content": response.content,
        "provider": response.provider,
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "latency_ms": response.latency_ms,
        "request_id": response.request_id,
        "created_at": response.created_at.isoformat(),
        "meta": response.meta,
        "router_short_circuit": response.router_short_circuit,
        "_cached": True,
    }


def _dict_to_response(data: dict[str, Any]) -> LLMResponse:
    """Deserialise a cached dict back to an ``LLMResponse``."""
    return LLMResponse(
        content=data["content"],
        provider=data["provider"],
        model=data["model"],
        usage=TokenUsage(
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
            total_tokens=data["input_tokens"] + data["output_tokens"],
        ),
        latency_ms=data.get("latency_ms", 0.0),
        request_id=data.get("request_id", "cached"),
        created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data
                   else datetime.now(UTC),
        meta={**data.get("meta", {}), "from_cache": True},
        router_short_circuit=data.get("router_short_circuit", False),
    )


# ---------------------------------------------------------------------------
# In-process LRU cache
# ---------------------------------------------------------------------------


class _LRUCache:
    """Thread-unsafe ordered-dict LRU cache (fine for single event loop)."""

    def __init__(self, max_size: int = _LRU_MAX_SIZE) -> None:
        self._cache: OrderedDict[str, tuple[LLMResponse, float]] = OrderedDict()
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    def get(self, key: str, ttl_s: int) -> LLMResponse | None:
        import time
        if key not in self._cache:
            self._misses += 1
            return None
        response, stored_at = self._cache[key]
        if time.monotonic() - stored_at > ttl_s:
            del self._cache[key]
            self._misses += 1
            return None
        self._cache.move_to_end(key)
        self._hits += 1
        return response

    def set(self, key: str, response: LLMResponse) -> None:
        import time
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (response, time.monotonic())
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total else 0.0

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 4),
        }


# ---------------------------------------------------------------------------
# Main LLMCache class
# ---------------------------------------------------------------------------


class LLMCache:
    """
    Two-layer LLM response cache: in-process LRU + optional Redis.

    Parameters
    ----------
    redis_url:
        Redis connection URL. If ``None``, only the in-process LRU is used.
    ttl_s:
        Cache TTL in seconds. Default: 3600 (1 hour).
    max_lru_size:
        Maximum in-process LRU entries. Default: 256.
    enabled:
        Global enable flag. If ``False``, all operations are no-ops.

    Example
    -------
    .. code-block:: python

        cache = LLMCache(redis_url="redis://localhost:6380/0", ttl_s=1800)

        # Try cache before calling LLM
        cached = await cache.get(messages, temperature=0.2)
        if cached:
            return cached

        response = await provider.complete(messages)
        await cache.set(messages, response, temperature=0.2)
        return response
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        ttl_s: int = _DEFAULT_TTL_S,
        max_lru_size: int = _LRU_MAX_SIZE,
        enabled: bool = True,
    ) -> None:
        self._ttl_s = ttl_s
        self._enabled = enabled
        self._lru = _LRUCache(max_size=max_lru_size)
        self._redis: Any = None  # Lazy-initialised aioredis client
        self._redis_url = redis_url
        self._redis_lock = asyncio.Lock()
        self._redis_available = False

    # ------------------------------------------------------------------
    # Redis initialisation (lazy)
    # ------------------------------------------------------------------

    async def _get_redis(self) -> Any:
        if self._redis is not None:
            return self._redis
        if not self._redis_url:
            return None
        async with self._redis_lock:
            if self._redis is not None:
                return self._redis
            try:
                import redis.asyncio as aioredis  # type: ignore[import]
                client = aioredis.from_url(
                    self._redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=2.0,
                )
                await client.ping()
                self._redis = client
                self._redis_available = True
                _log.info("llm_cache.redis_connected", url=self._redis_url)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "llm_cache.redis_unavailable",
                    error=str(exc),
                    hint="Falling back to in-process LRU cache only",
                )
                self._redis_available = False
        return self._redis

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse | None:
        """
        Look up a cached response.

        Returns ``None`` on cache miss or when caching is disabled/bypassed.

        Cache is bypassed (returns ``None``) when ``temperature > 0.5``
        because high-temperature outputs are intentionally non-deterministic.
        """
        if not self._enabled:
            return None
        if temperature > _MAX_TEMPERATURE_FOR_CACHING:
            return None

        key = _make_cache_key(messages, provider=provider, model=model, temperature=temperature)

        # Layer 1: LRU
        cached = self._lru.get(key, self._ttl_s)
        if cached is not None:
            _log.debug("llm_cache.lru_hit", key=key[:12])
            import dataclasses
            return dataclasses.replace(cached, meta={**cached.meta, "from_cache": True})

        # Layer 2: Redis
        redis = await self._get_redis()
        if redis is not None:
            try:
                raw = await redis.get(_REDIS_KEY_PREFIX + key)
                if raw:
                    data = json.loads(raw)
                    response = _dict_to_response(data)
                    self._lru.set(key, response)  # Populate LRU from Redis
                    _log.debug("llm_cache.redis_hit", key=key[:12])
                    return response
            except Exception as exc:  # noqa: BLE001
                _log.warning("llm_cache.redis_get_error", error=str(exc))

        return None

    async def set(
        self,
        messages: list[dict[str, str]],
        response: LLMResponse,
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> None:
        """
        Store a response in the cache.

        No-ops when caching is disabled or temperature is too high.
        """
        if not self._enabled:
            return
        if temperature > _MAX_TEMPERATURE_FOR_CACHING:
            return

        key = _make_cache_key(messages, provider=provider, model=model, temperature=temperature)

        # Always populate LRU
        self._lru.set(key, response)

        # Populate Redis if available
        redis = await self._get_redis()
        if redis is not None:
            try:
                serialised = json.dumps(_response_to_dict(response))
                await redis.setex(_REDIS_KEY_PREFIX + key, self._ttl_s, serialised)
                _log.debug("llm_cache.redis_set", key=key[:12], ttl_s=self._ttl_s)
            except Exception as exc:  # noqa: BLE001
                _log.warning("llm_cache.redis_set_error", error=str(exc))

    async def invalidate(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> None:
        """Invalidate a specific cache entry."""
        key = _make_cache_key(messages, provider=provider, model=model, temperature=temperature)
        self._lru.invalidate(key)
        redis = await self._get_redis()
        if redis is not None:
            try:
                await redis.delete(_REDIS_KEY_PREFIX + key)
            except Exception as exc:  # noqa: BLE001
                _log.warning("llm_cache.redis_delete_error", error=str(exc))

    async def clear_all(self) -> None:
        """Clear all cache entries (LRU and Redis)."""
        self._lru.clear()
        redis = await self._get_redis()
        if redis is not None:
            try:
                keys = await redis.keys(_REDIS_KEY_PREFIX + "*")
                if keys:
                    await redis.delete(*keys)
                _log.info("llm_cache.cleared", redis_keys_deleted=len(keys))
            except Exception as exc:  # noqa: BLE001
                _log.warning("llm_cache.clear_error", error=str(exc))

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            "enabled": self._enabled,
            "ttl_s": self._ttl_s,
            "redis_available": self._redis_available,
            "lru": self._lru.stats,
        }

    async def aclose(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # noqa: BLE001
                pass
