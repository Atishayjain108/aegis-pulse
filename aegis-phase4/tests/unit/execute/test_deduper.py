"""Deduper tests — local fallback path."""

from __future__ import annotations

import time

import pytest

from aegis.execute.policy.deduper import Deduper


async def test_first_emit_true_then_false():
    d = Deduper(ttl_s=60)
    assert await d.should_emit("a1") is True
    assert await d.should_emit("a1") is False


async def test_distinct_ids_independent():
    d = Deduper(ttl_s=60)
    assert await d.should_emit("a") is True
    assert await d.should_emit("b") is True
    assert await d.should_emit("a") is False


async def test_empty_id_is_rejected():
    d = Deduper(ttl_s=60)
    assert await d.should_emit("") is False


async def test_invalid_ttl_raises():
    with pytest.raises(ValueError):
        Deduper(ttl_s=0)


async def test_expiry_allows_re_emit():
    # Use a very short TTL and sleep past it.
    d = Deduper(ttl_s=1)
    assert await d.should_emit("x") is True
    # Force the local expiry tick to fire by manipulating internal map.
    # We sweep when monotonic now > stored expiry.
    d._local["x"] = time.monotonic() - 1.0  # type: ignore[attr-defined]
    assert await d.should_emit("x") is True


async def test_redis_path_via_stub():
    class _StubRedis:
        def __init__(self) -> None:
            self.set_calls = []
            self.exists: set[str] = set()

        async def set(self, name, value, *args, **kwargs):
            self.set_calls.append((name, value, kwargs))
            if name in self.exists:
                return None
            self.exists.add(name)
            return True

    rc = _StubRedis()
    d = Deduper(redis_client=rc, ttl_s=60)
    assert await d.should_emit("z") is True
    assert await d.should_emit("z") is False
    # First call had `nx=True, ex=60`
    name, _, kwargs = rc.set_calls[0]
    assert "ex" in kwargs and kwargs["ex"] == 60
    assert kwargs.get("nx") is True
