"""CONN-1: unified cross-phase event-bus publisher tests."""
from __future__ import annotations

import json

import pytest

from aegis.core.event_bus import (
    STREAM_COMPLIANCE,
    STREAM_EVOLVE,
    STREAM_GEO,
    publish_event,
    reset_client,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_client()
    yield
    reset_client()


class _FakeRedis:
    def __init__(self) -> None:
        self.adds: list[tuple] = []

    async def xadd(self, stream, fields, *, maxlen=None, approximate=None):
        self.adds.append((stream, fields, maxlen, approximate))
        return "1-0"


class _BoomRedis:
    async def xadd(self, *a, **k):
        raise RuntimeError("redis down")


def test_stream_names_are_distinct():
    assert len({STREAM_GEO, STREAM_COMPLIANCE, STREAM_EVOLVE}) == 3


async def test_publish_event_xadds_capped_json_body():
    fake = _FakeRedis()
    ok = await publish_event(STREAM_GEO, {"a": 1, "b": "x"}, redis_client=fake)
    assert ok is True
    stream, fields, maxlen, approximate = fake.adds[0]
    assert stream == STREAM_GEO
    assert json.loads(fields["body"]) == {"a": 1, "b": "x"}
    assert maxlen == 10_000
    assert approximate is True


async def test_publish_event_serialises_non_json_types():
    from datetime import UTC, datetime

    fake = _FakeRedis()
    ok = await publish_event(STREAM_EVOLVE, {"ts": datetime(2025, 1, 1, tzinfo=UTC)}, redis_client=fake)
    assert ok is True
    # default=str keeps it from raising on datetime.
    assert "2025-01-01" in fake.adds[0][1]["body"]


async def test_publish_event_swallows_redis_errors():
    ok = await publish_event(STREAM_COMPLIANCE, {"x": 1}, redis_client=_BoomRedis())
    assert ok is False


async def test_publish_event_no_client_returns_false(monkeypatch):
    # No injected client and holder fails to build one → False, never raises.
    from aegis.core import event_bus

    async def _none():
        return None

    monkeypatch.setattr(event_bus._HOLDER, "get", _none)
    ok = await publish_event(STREAM_GEO, {"x": 1})
    assert ok is False
