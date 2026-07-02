"""Tests for the async swarm-scale dedup pipeline (aegis.scrape.dedup).

Mirrors GODMODE PASS 2-2A behavioural invariants. Distinct from
tests/unit/db/test_dedup_minhash.py which covers the synchronous DB-path
variant.
"""

from __future__ import annotations

import sys
import time

import pytest

from aegis.scrape.dedup import DedupResult, MinHashLayer, deduplicate_batch

# Coverage/debugger tracing inflates wall time ~4-6×; relax the bound when a
# trace function is active (same pattern as test_dedup_perf.py).
_PERF_BUDGET_MS = 500.0 if sys.gettrace() is None else 3000.0


async def test_empty_batch_returns_empty() -> None:
    unique, results = await deduplicate_batch([])
    assert unique == []
    assert results == []


async def test_layer1_catches_identical_token_sets() -> None:
    """Same words, different order → exact duplicate."""
    signals = [
        {"title": "record quarterly revenue nvidia", "url": "https://a.com/1"},
        {"title": "nvidia revenue quarterly record", "url": "https://a.com/2"},
    ]
    unique, results = await deduplicate_batch(signals)
    assert len(unique) == 1
    assert results[0].dedup_method == "pass"
    assert results[1].is_duplicate is True
    assert results[1].dedup_method == "exact"


async def test_layer2_catches_near_duplicates() -> None:
    """High character-shingle overlap → minhash duplicate (when datasketch present)."""
    from aegis.scrape import dedup as mod

    base = "Breaking: the global semiconductor market surges in early trading today"
    near = "Breaking: the global semiconductor market surges in early trading todayy"
    signals = [
        {"title": base, "url": "https://x.com/1"},
        {"title": near, "url": "https://x.com/2"},
    ]
    unique, results = await deduplicate_batch(signals)
    if mod._MINHASH_AVAILABLE:
        assert len(unique) == 1
        assert results[1].is_duplicate is True
        assert results[1].dedup_method in ("minhash", "exact")
    else:
        # Without datasketch, layer 2 is skipped — distinct titles both pass.
        assert len(unique) == 2


async def test_genuinely_different_signals_not_flagged() -> None:
    signals = [
        {"title": "Apple unveils new chip architecture", "url": "https://q.com/1"},
        {"title": "Wheat prices fall sharply in Punjab markets", "url": "https://q.com/2"},
        {"title": "New regulation targets fintech lending apps", "url": "https://q.com/3"},
    ]
    unique, _ = await deduplicate_batch(signals)
    assert len(unique) == 3


async def test_redis_cache_hit_skips_recompute() -> None:
    """Second get_or_build with same content_hash deserialises the cached bytes."""
    from aegis.scrape import dedup as mod

    if not mod._MINHASH_AVAILABLE:
        pytest.skip("datasketch not installed")

    store: dict[str, bytes] = {}

    class _FakeRedis:
        def __init__(self) -> None:
            self.get_calls = 0
            self.set_calls = 0

        async def get(self, key: str):
            self.get_calls += 1
            return store.get(key)

        async def set(self, key: str, val: bytes, ex: int | None = None):
            self.set_calls += 1
            store[key] = val

    redis = _FakeRedis()
    layer = MinHashLayer()
    m1 = await layer.get_or_build("abc123def456gh0", "some text here", redis)
    m2 = await layer.get_or_build("abc123def456gh0", "some text here", redis)
    assert m1 is not None and m2 is not None
    assert redis.set_calls == 1  # only first call wrote
    assert redis.get_calls == 2


async def test_redis_unavailable_graceful() -> None:
    """A redis whose ops raise must not break dedup."""

    class _BrokenRedis:
        async def get(self, key):
            raise RuntimeError("down")

        async def set(self, key, val, ex=None):
            raise RuntimeError("down")

    signals = [{"title": "hello world signal", "url": "https://z.com/1"}]
    unique, results = await deduplicate_batch(signals, redis=_BrokenRedis())
    assert len(unique) == 1
    assert results[0].dedup_method == "pass"


async def test_dedup_method_field_populated() -> None:
    signals = [
        {"title": "alpha beta gamma", "url": "https://m.com/1"},
        {"title": "gamma beta alpha", "url": "https://m.com/2"},
    ]
    _, results = await deduplicate_batch(signals)
    assert all(isinstance(r, DedupResult) for r in results)
    assert results[0].dedup_method == "pass"
    assert results[1].dedup_method in ("exact", "minhash")


async def test_1000_signals_under_500ms() -> None:
    signals = [
        {"title": f"Test signal number {i}", "url": f"https://example.com/{i}", "confidence": 0.8}
        for i in range(1000)
    ]
    start = time.perf_counter()
    await deduplicate_batch(signals)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < _PERF_BUDGET_MS, (
        f"dedup too slow: {elapsed_ms:.0f}ms, must be < {_PERF_BUDGET_MS:.0f}ms"
    )
