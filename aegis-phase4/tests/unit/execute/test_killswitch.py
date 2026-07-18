"""Killswitch tests — armed / tripped / fail-closed."""

from __future__ import annotations

import pytest

from aegis.execute.errors import AegisExecuteError
from aegis.execute.killswitch.switch import KillSwitch


class _StubRedis:
    def __init__(self, *, raise_on_get=False) -> None:
        self.store: dict[str, str] = {}
        self.raise_on_get = raise_on_get
        self.set_calls = []

    async def get(self, name):
        if self.raise_on_get:
            raise RuntimeError("redis down")
        return self.store.get(name)

    async def set(self, name, value):
        self.set_calls.append((name, value))
        self.store[name] = value
        return True

    async def delete(self, *names):
        n = 0
        for k in names:
            if k in self.store:
                self.store.pop(k)
                n += 1
        return n


async def test_no_redis_armed_when_not_fail_closed():
    ks = KillSwitch(redis_client=None, fail_closed=False)
    assert await ks.is_tripped() is False
    assert await ks.state() == "ARMED"


async def test_no_redis_tripped_when_fail_closed():
    ks = KillSwitch(redis_client=None, fail_closed=True)
    assert await ks.is_tripped() is True
    assert await ks.state() == "TRIPPED"


async def test_redis_path_arm_then_trip():
    rc = _StubRedis()
    ks = KillSwitch(redis_client=rc)
    assert await ks.is_tripped() is False
    await ks.trip(reason="incident")
    assert await ks.is_tripped() is True
    await ks.arm(reason="resolved")
    assert await ks.is_tripped() is False


async def test_redis_outage_is_fail_closed_by_default():
    rc = _StubRedis(raise_on_get=True)
    ks = KillSwitch(redis_client=rc, fail_closed=True)
    assert await ks.is_tripped() is True


async def test_redis_outage_can_be_fail_open():
    rc = _StubRedis(raise_on_get=True)
    ks = KillSwitch(redis_client=rc, fail_closed=False)
    assert await ks.is_tripped() is False


async def test_raise_if_tripped():
    rc = _StubRedis()
    ks = KillSwitch(redis_client=rc)
    await ks.raise_if_tripped()  # not tripped → no error
    await ks.trip(reason="x")
    with pytest.raises(AegisExecuteError) as ei:
        await ks.raise_if_tripped()
    assert ei.value.spec.code == "AEGIS-EXEC-0020"


async def test_trip_without_redis_raises():
    ks = KillSwitch(redis_client=None)
    with pytest.raises(AegisExecuteError) as ei:
        await ks.trip()
    assert ei.value.spec.code == "AEGIS-EXEC-0021"
