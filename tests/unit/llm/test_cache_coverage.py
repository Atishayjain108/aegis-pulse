"""Cover aegis.llm.cache — LRU + Redis two-layer LLM response cache."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.llm import cache as c
from aegis.llm.gateway.response import LLMResponse, TokenUsage


def _resp(content: str = "hi") -> LLMResponse:
    return LLMResponse(
        content=content,
        provider="ollama",
        model="llama3.2",
        usage=TokenUsage(input_tokens=3, output_tokens=5, total_tokens=8),
        latency_ms=12.0,
        request_id="r1",
        created_at=datetime.now(UTC),
        meta={"k": "v"},
        router_short_circuit=False,
    )


MSGS = [{"role": "user", "content": "hello"}]


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, val: str) -> None:
        self.store[key] = val

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self.store.pop(k, None)

    async def keys(self, pattern: str) -> list[str]:
        return list(self.store)

    async def aclose(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# key + serialization
# --------------------------------------------------------------------------- #


def test_make_cache_key_stable_and_provider_sensitive() -> None:
    k1 = c._make_cache_key(MSGS, provider="ollama", model="m", temperature=0.2)
    k2 = c._make_cache_key(MSGS, provider="ollama", model="m", temperature=0.2)
    k3 = c._make_cache_key(MSGS, provider="groq", model="m", temperature=0.2)
    assert k1 == k2
    assert k1 != k3


def test_response_dict_roundtrip() -> None:
    d = c._response_to_dict(_resp("abc"))
    assert d["_cached"] is True
    restored = c._dict_to_response(d)
    assert restored.content == "abc"
    assert restored.usage.total_tokens == 8
    assert restored.meta["from_cache"] is True


# --------------------------------------------------------------------------- #
# _LRUCache
# --------------------------------------------------------------------------- #


def test_lru_set_get_miss_and_stats() -> None:
    lru = c._LRUCache(max_size=2)
    assert lru.get("absent", ttl_s=10) is None
    lru.set("a", _resp())
    assert lru.get("a", ttl_s=10) is not None
    assert lru.size == 1
    assert lru.hit_rate > 0
    assert lru.stats["hits"] == 1


def test_lru_eviction() -> None:
    lru = c._LRUCache(max_size=2)
    lru.set("a", _resp())
    lru.set("b", _resp())
    lru.set("c", _resp())  # evicts oldest "a"
    assert lru.get("a", ttl_s=10) is None
    assert lru.get("c", ttl_s=10) is not None


def test_lru_ttl_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    lru = c._LRUCache()
    lru.set("a", _resp())
    # negative ttl forces expiry path
    assert lru.get("a", ttl_s=-1) is None


def test_lru_invalidate_and_clear() -> None:
    lru = c._LRUCache()
    lru.set("a", _resp())
    lru.invalidate("a")
    assert lru.get("a", ttl_s=10) is None
    lru.set("b", _resp())
    lru.clear()
    assert lru.size == 0


# --------------------------------------------------------------------------- #
# LLMCache — LRU-only
# --------------------------------------------------------------------------- #


async def test_cache_disabled_is_noop() -> None:
    cache = c.LLMCache(enabled=False)
    await cache.set(MSGS, _resp())
    assert await cache.get(MSGS) is None


async def test_cache_high_temperature_bypass() -> None:
    cache = c.LLMCache()
    await cache.set(MSGS, _resp(), temperature=0.9)
    assert await cache.get(MSGS, temperature=0.9) is None


async def test_cache_lru_hit_roundtrip() -> None:
    cache = c.LLMCache()
    await cache.set(MSGS, _resp("cached!"))
    hit = await cache.get(MSGS)
    assert hit is not None
    assert hit.content == "cached!"
    assert hit.meta["from_cache"] is True
    st = cache.stats()
    assert st["enabled"] is True


# --------------------------------------------------------------------------- #
# LLMCache — with injected Redis
# --------------------------------------------------------------------------- #


async def test_cache_redis_set_get_invalidate_clear() -> None:
    cache = c.LLMCache(redis_url="redis://x")
    fake = _FakeRedis()
    cache._redis = fake  # inject (skip lazy connect)

    await cache.set(MSGS, _resp("via-redis"))
    assert fake.store  # stored in redis

    # Clear LRU so the next get must hit Redis layer.
    cache._lru.clear()
    hit = await cache.get(MSGS)
    assert hit is not None
    assert hit.content == "via-redis"

    await cache.invalidate(MSGS)
    cache._lru.clear()
    assert await cache.get(MSGS) is None

    await cache.set(MSGS, _resp())
    await cache.clear_all()
    assert fake.store == {}
    await cache.aclose()
