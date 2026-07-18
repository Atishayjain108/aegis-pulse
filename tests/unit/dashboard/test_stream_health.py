"""Pass 10 — DASH-2 stream-health traffic-light endpoint.

`stream_health()` reports length + last-entry age + a status per canonical
Redis stream. Missing streams show "empty" (not "error"); recent activity
shows "healthy"; stale activity shows "stale".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from aegis.dashboard import app as dash

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses

    async def xinfo_stream(self, stream: str) -> dict[str, Any]:
        val = self._responses.get(stream)
        if isinstance(val, Exception):
            raise val
        return val


def _install(monkeypatch: pytest.MonkeyPatch, responses: dict[str, Any]) -> None:
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(responses))


async def test_healthy_recent_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: a recent entry → status 'healthy' with an age."""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    stream = dash._CANONICAL_STREAMS[0]
    _install(monkeypatch, {**{s: {"length": 0, "last-generated-id": "0-0"}
                              for s in dash._CANONICAL_STREAMS},
                           stream: {"length": 5, "last-generated-id": f"{now_ms}-0"}})
    out = await dash.stream_health()
    assert out[stream]["status"] == "healthy"
    assert out[stream]["length"] == 5
    assert out[stream]["last_entry_age_seconds"] is not None


async def test_stale_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: an entry older than 1h → status 'stale'."""
    old_ms = int((datetime.now(UTC).timestamp() - 7200) * 1000)
    stream = dash._CANONICAL_STREAMS[0]
    _install(monkeypatch, {s: {"length": 1, "last-generated-id": f"{old_ms}-0"}
                           for s in dash._CANONICAL_STREAMS})
    out = await dash.stream_health()
    assert out[stream]["status"] == "stale"


async def test_missing_stream_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Edge case: 'no such key' error → status 'empty', not 'error'."""
    _install(monkeypatch, {s: RuntimeError("ERR no such key")
                           for s in dash._CANONICAL_STREAMS})
    out = await dash.stream_health()
    for s in dash._CANONICAL_STREAMS:
        assert out[s]["status"] == "empty"
        assert out[s]["length"] == 0


async def test_other_error_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure path: a non-'no such key' error surfaces as status 'error'."""
    _install(monkeypatch, {s: RuntimeError("connection refused")
                           for s in dash._CANONICAL_STREAMS})
    out = await dash.stream_health()
    stream = dash._CANONICAL_STREAMS[0]
    assert out[stream]["status"] == "error"
    assert "error" in out[stream]
