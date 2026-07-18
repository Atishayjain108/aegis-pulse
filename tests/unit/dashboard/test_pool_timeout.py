"""DASH-4 — pool-acquire timeout must surface as HTTP 504 + counter increment.

Covers `_acquire_pg` directly (the endpoints that wrap it in try/except
deliberately degrade to JSON error envelopes; the 504 contract applies to
unwrapped routes).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from aegis.dashboard import app as dash


class _TimeoutAcquireCM:
    async def __aenter__(self) -> Any:
        raise TimeoutError("pool exhausted")

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakePool:
    def __init__(self) -> None:
        self.acquire_calls: list[float] = []

    def acquire(self, timeout: float = 0.0) -> _TimeoutAcquireCM:
        self.acquire_calls.append(timeout)
        return _TimeoutAcquireCM()


class _CounterSpy:
    def __init__(self) -> None:
        self.count = 0

    def inc(self, *_a: Any, **_k: Any) -> None:
        self.count += 1


@pytest.fixture()
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    pool = _FakePool()
    monkeypatch.setattr(dash, "_pg_pool", pool)
    return pool


async def test_acquire_timeout_raises_504(fake_pool: _FakePool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_pool_acquire_timeout_total", _CounterSpy())
    with pytest.raises(HTTPException) as excinfo:
        async with dash._acquire_pg("postgresql://unused"):
            pass
    assert excinfo.value.status_code == 504
    assert "timed out" in excinfo.value.detail.lower()


async def test_acquire_timeout_increments_counter(
    fake_pool: _FakePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _CounterSpy()
    monkeypatch.setattr(dash, "_pool_acquire_timeout_total", spy)
    with pytest.raises(HTTPException):
        async with dash._acquire_pg("postgresql://unused"):
            pass
    assert spy.count == 1


async def test_acquire_passes_timeout_to_pool(
    fake_pool: _FakePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dash, "_pool_acquire_timeout_total", _CounterSpy())
    with pytest.raises(HTTPException):
        async with dash._acquire_pg("postgresql://unused", timeout=7.5):
            pass
    assert fake_pool.acquire_calls == [7.5]


async def test_healthy_pool_yields_connection_and_sets_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, ...]] = []

    class _Conn:
        async def execute(self, *args: Any) -> None:
            executed.append(args)

    class _OkCM:
        async def __aenter__(self) -> _Conn:
            return _Conn()

        async def __aexit__(self, *exc: Any) -> bool:
            return False

    class _OkPool:
        def acquire(self, timeout: float = 0.0) -> _OkCM:
            return _OkCM()

    monkeypatch.setattr(dash, "_pg_pool", _OkPool())
    async with dash._acquire_pg("postgresql://unused") as conn:
        assert conn is not None
    # RLS tenant GUC set on every acquire (§2)
    assert any("app.current_tenant" in str(args[0]) for args in executed)
