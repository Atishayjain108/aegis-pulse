"""tests/unit/llm/test_cache.py — LLMCache tests"""
from __future__ import annotations
import pytest


def _make_response(content: str = "Cached response"):
    from aegis.llm.gateway.response import LLMResponse, TokenUsage
    return LLMResponse(content=content, provider="ollama", model="test",
                       usage=TokenUsage(10, 20, 30), latency_ms=50.0)


class TestLRUCache:
    def test_set_and_get(self):
        from aegis.llm.cache import _LRUCache
        cache = _LRUCache(max_size=10)
        response = _make_response()
        cache.set("key1", response)
        result = cache.get("key1", ttl_s=3600)
        assert result is not None
        assert result.content == "Cached response"

    def test_miss_returns_none(self):
        from aegis.llm.cache import _LRUCache
        cache = _LRUCache(max_size=10)
        assert cache.get("missing", ttl_s=3600) is None

    def test_evicts_oldest_when_full(self):
        from aegis.llm.cache import _LRUCache
        cache = _LRUCache(max_size=3)
        for i in range(4):
            cache.set(f"key{i}", _make_response(f"content{i}"))
        assert cache.size <= 3

    def test_hit_rate_calculation(self):
        from aegis.llm.cache import _LRUCache
        cache = _LRUCache(max_size=10)
        cache.set("k", _make_response())
        cache.get("k", 3600)   # hit
        cache.get("miss", 3600)  # miss
        assert cache.hit_rate == pytest.approx(0.5)


class TestLLMCache:
    @pytest.mark.asyncio()
    async def test_set_and_get_lru_hit(self):
        from aegis.llm.cache import LLMCache
        cache = LLMCache(redis_url=None, ttl_s=3600)
        messages = [{"role": "user", "content": "hello"}]
        response = _make_response()

        await cache.set(messages, response, temperature=0.2)
        result = await cache.get(messages, temperature=0.2)
        assert result is not None
        assert result.meta.get("from_cache")

    @pytest.mark.asyncio()
    async def test_high_temperature_bypasses_cache(self):
        from aegis.llm.cache import LLMCache
        cache = LLMCache(redis_url=None, ttl_s=3600)
        messages = [{"role": "user", "content": "creative"}]
        await cache.set(messages, _make_response(), temperature=0.9)
        result = await cache.get(messages, temperature=0.9)
        assert result is None  # bypassed

    @pytest.mark.asyncio()
    async def test_disabled_cache_always_misses(self):
        from aegis.llm.cache import LLMCache
        cache = LLMCache(redis_url=None, enabled=False)
        messages = [{"role": "user", "content": "test"}]
        await cache.set(messages, _make_response())
        result = await cache.get(messages)
        assert result is None

    @pytest.mark.asyncio()
    async def test_clear_all_empties_lru(self):
        from aegis.llm.cache import LLMCache
        cache = LLMCache(redis_url=None)
        messages = [{"role": "user", "content": "test"}]
        await cache.set(messages, _make_response())
        await cache.clear_all()
        assert cache._lru.size == 0

    def test_stats_returns_dict(self):
        from aegis.llm.cache import LLMCache
        cache = LLMCache(redis_url=None)
        stats = cache.stats()
        assert "enabled" in stats
        assert "lru" in stats

    @pytest.mark.asyncio()
    async def test_different_temperatures_different_keys(self):
        from aegis.llm.cache import LLMCache
        cache = LLMCache(redis_url=None)
        messages = [{"role": "user", "content": "same"}]
        await cache.set(messages, _make_response("low_temp"), temperature=0.0)
        await cache.set(messages, _make_response("mid_temp"), temperature=0.3)
        r1 = await cache.get(messages, temperature=0.0)
        r2 = await cache.get(messages, temperature=0.3)
        assert r1 is not None
        assert r2 is not None
        assert r1.content != r2.content


class TestMakeCacheKey:
    def test_same_inputs_same_key(self):
        from aegis.llm.cache import _make_cache_key
        messages = [{"role": "user", "content": "hello"}]
        k1 = _make_cache_key(messages, temperature=0.2)
        k2 = _make_cache_key(messages, temperature=0.2)
        assert k1 == k2

    def test_different_content_different_key(self):
        from aegis.llm.cache import _make_cache_key
        k1 = _make_cache_key([{"role": "user", "content": "hello"}])
        k2 = _make_cache_key([{"role": "user", "content": "world"}])
        assert k1 != k2

    def test_different_temperature_different_key(self):
        from aegis.llm.cache import _make_cache_key
        messages = [{"role": "user", "content": "test"}]
        k1 = _make_cache_key(messages, temperature=0.0)
        k2 = _make_cache_key(messages, temperature=0.2)
        assert k1 != k2
