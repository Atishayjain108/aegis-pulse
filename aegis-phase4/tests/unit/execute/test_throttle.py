"""Throttle tests — local fallback path."""

from __future__ import annotations

from uuid import uuid4

import pytest

from aegis.execute.policy.throttle import Throttle


async def test_allows_up_to_limit_then_blocks():
    t = Throttle(per_minute=3)
    tid = uuid4()
    assert await t.allow(tid, "log") is True
    assert await t.allow(tid, "log") is True
    assert await t.allow(tid, "log") is True
    assert await t.allow(tid, "log") is False


async def test_different_channels_independent():
    t = Throttle(per_minute=1)
    tid = uuid4()
    assert await t.allow(tid, "log") is True
    assert await t.allow(tid, "ntfy") is True
    assert await t.allow(tid, "log") is False


async def test_invalid_per_minute_raises():
    with pytest.raises(ValueError):
        Throttle(per_minute=0)


async def test_redis_path_via_stub():
    class _StubRedis:
        def __init__(self) -> None:
            self.counter = 0
            self.expired = False

        async def incr(self, name):
            self.counter += 1
            return self.counter

        async def expire(self, name, t):
            self.expired = True
            return True

    rc = _StubRedis()
    t = Throttle(redis_client=rc, per_minute=2)
    tid = uuid4()
    assert await t.allow(tid, "log") is True
    assert await t.allow(tid, "log") is True
    assert await t.allow(tid, "log") is False
    assert rc.expired is True
