"""Unit tests for aegis.scrape.stream_bridge.

All Redis interactions are mocked — no live infrastructure required.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aegis.scrape.stream_bridge import (
    CONSUMER_GROUP,
    PHASE0_STREAM,
    emit_to_stream,
    ensure_consumer_group,
)

# ---------------------------------------------------------------------------
# emit_to_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_xadd_targets_correct_stream() -> None:
    redis = AsyncMock()
    await emit_to_stream(redis, [{"title": "AI chips"}], topic="AI chips", tenant_id="t1")
    redis.xadd.assert_awaited_once()
    stream_name = redis.xadd.call_args[0][0]
    assert stream_name == PHASE0_STREAM


@pytest.mark.asyncio
async def test_emit_body_contains_required_fields() -> None:
    redis = AsyncMock()
    signals = [{"title": "AI chips", "platform": "hn"}, {"title": "GPT-5", "platform": "reddit"}]
    await emit_to_stream(redis, signals, topic="AI chips", tenant_id="tenant-abc")

    body_raw = redis.xadd.call_args[0][1]["body"]
    body = json.loads(body_raw)
    assert body["topic"] == "AI chips"
    assert body["tenant_id"] == "tenant-abc"
    assert body["count"] == 2
    assert len(body["signals"]) == 2


@pytest.mark.asyncio
async def test_emit_passes_maxlen_and_approximate() -> None:
    redis = AsyncMock()
    await emit_to_stream(
        redis, [{"title": "x"}], topic="t", tenant_id="t1", maxlen=500
    )
    _, kwargs = redis.xadd.call_args
    assert kwargs["maxlen"] == 500
    assert kwargs["approximate"] is True


@pytest.mark.asyncio
async def test_emit_noop_when_client_is_none() -> None:
    # Must not raise and must not attempt any Redis call.
    await emit_to_stream(None, [{"title": "x"}], topic="t", tenant_id="t1")


@pytest.mark.asyncio
async def test_emit_noop_when_signals_empty() -> None:
    redis = AsyncMock()
    await emit_to_stream(redis, [], topic="t", tenant_id="t1")
    redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_emit_swallows_redis_connection_error() -> None:
    redis = AsyncMock()
    redis.xadd.side_effect = ConnectionError("Redis down")
    # Must not propagate — best-effort contract.
    await emit_to_stream(redis, [{"title": "x"}], topic="t", tenant_id="t1")


@pytest.mark.asyncio
async def test_emit_swallows_arbitrary_redis_error() -> None:
    redis = AsyncMock()
    redis.xadd.side_effect = RuntimeError("unexpected")
    await emit_to_stream(redis, [{"title": "x"}], topic="t", tenant_id="t1")


@pytest.mark.asyncio
async def test_emit_serialises_non_string_values() -> None:
    """Signals with datetime or numeric fields must not raise."""
    import datetime

    redis = AsyncMock()
    signals = [{"scraped_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC), "score": 0.95}]
    await emit_to_stream(redis, signals, topic="t", tenant_id="t1")
    redis.xadd.assert_awaited_once()


# ---------------------------------------------------------------------------
# ensure_consumer_group
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_group_calls_xgroup_create() -> None:
    redis = AsyncMock()
    await ensure_consumer_group(redis)
    redis.xgroup_create.assert_awaited_once_with(
        PHASE0_STREAM, CONSUMER_GROUP, id="0", mkstream=True
    )


@pytest.mark.asyncio
async def test_ensure_group_noop_when_client_is_none() -> None:
    await ensure_consumer_group(None)


@pytest.mark.asyncio
async def test_ensure_group_ignores_busygroup_error() -> None:
    redis = AsyncMock()
    redis.xgroup_create.side_effect = Exception("BUSYGROUP Consumer Group name already exists")
    # Idempotent — must not raise on already-existing group.
    await ensure_consumer_group(redis)


@pytest.mark.asyncio
async def test_ensure_group_swallows_other_redis_errors() -> None:
    redis = AsyncMock()
    redis.xgroup_create.side_effect = ConnectionError("Redis unreachable")
    await ensure_consumer_group(redis)
