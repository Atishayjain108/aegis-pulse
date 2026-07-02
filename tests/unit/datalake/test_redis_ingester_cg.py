"""CONN-4 — consumer-group semantics of the Bronze Redis ingester.

Asserts the XREADGROUP-based contract:
- consumer group auto-created (MKSTREAM) and BUSYGROUP swallowed
- new entries are read, written, and ACKed only after persistence
- PEL entries abandoned by dead consumers are reclaimed via XAUTOCLAIM
- reclaim is best-effort: missing/raising xautoclaim never blocks ingest
- a failed batch write propagates and leaves entries un-ACKed (PEL retry)
- read batch size and stat counts are accurate

No real Redis required — everything runs against in-memory fakes.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from aegis.datalake.bronze.ingest_redis import RedisStreamIngester
from aegis.datalake.errors import UpstreamUnavailableError

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _StubWriter:
    """Duck-typed BronzeWriter: records calls, returns a manifest-shaped obj."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def write(self, **kwargs: Any) -> Any:
        if self.fail:
            raise RuntimeError("simulated parquet write failure")
        self.calls.append(kwargs)
        return SimpleNamespace(row_count=len(list(kwargs["rows"])))


def _entry(msg_id: str, payload: dict[str, Any] | str) -> tuple[str, dict[str, str]]:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return (msg_id, {"body": body})


class _FakeRedisNoAutoclaim:
    """Minimal consumer-group fake without xautoclaim (older redis-py)."""

    def __init__(
        self,
        *,
        batches: list[list[tuple[str, dict[str, str]]]] | None = None,
        busygroup: bool = True,
        group_create_error: Exception | None = None,
    ) -> None:
        self._batches = list(batches or [])
        self._busygroup = busygroup
        self._group_create_error = group_create_error
        self.xgroup_create_calls: list[dict[str, Any]] = []
        self.xreadgroup_calls: list[dict[str, Any]] = []
        self.xack_calls: list[tuple[Any, ...]] = []

    async def xgroup_create(self, **kwargs: Any) -> None:
        self.xgroup_create_calls.append(kwargs)
        if self._group_create_error is not None:
            raise self._group_create_error
        if self._busygroup:
            raise Exception("BUSYGROUP Consumer Group name already exists")

    async def xreadgroup(self, **kwargs: Any) -> list[Any]:
        self.xreadgroup_calls.append(kwargs)
        if not self._batches:
            return []
        return [("aegis:phase2:graph_results", self._batches.pop(0))]

    async def xack(self, *args: Any) -> int:
        self.xack_calls.append(args)
        return len(args) - 2


class _FakeRedis(_FakeRedisNoAutoclaim):
    """Adds XAUTOCLAIM with configurable PEL contents / failure."""

    def __init__(
        self,
        *,
        pel_entries: list[tuple[str, dict[str, str]]] | None = None,
        autoclaim_error: Exception | None = None,
        three_tuple_reply: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._pel_entries = list(pel_entries or [])
        self._autoclaim_error = autoclaim_error
        self._three_tuple_reply = three_tuple_reply
        self.xautoclaim_calls: list[dict[str, Any]] = []

    async def xautoclaim(self, *args: Any, **kwargs: Any) -> Any:
        self.xautoclaim_calls.append({"args": args, **kwargs})
        if self._autoclaim_error is not None:
            raise self._autoclaim_error
        if self._three_tuple_reply:
            return ("0-0", self._pel_entries, [])
        return ("0-0", self._pel_entries)


def _ingester(writer: _StubWriter | None = None) -> RedisStreamIngester:
    return RedisStreamIngester(writer=writer or _StubWriter())  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


async def test_happy_path_entries_ingested_and_acked() -> None:
    writer = _StubWriter()
    redis = _FakeRedis(
        batches=[
            [
                _entry("1-1", {"trend_id": "t1", "finished_at": "2026-06-11T00:00:00+00:00"}),
                _entry("1-2", {"trend_id": "t2", "finished_at": "2026-06-11T01:00:00+00:00"}),
            ]
        ]
    )
    stats = await _ingester(writer).ingest_once(redis=redis)

    assert stats.entries_read == 2
    assert stats.entries_written == 2
    assert stats.partitions_written == 1
    # ACK happens after persistence and covers exactly the processed IDs.
    assert len(redis.xack_calls) == 1
    acked = set(redis.xack_calls[0][2:])
    assert acked == {"1-1", "1-2"}


async def test_consumer_group_auto_created_with_mkstream() -> None:
    redis = _FakeRedis(busygroup=False)
    await _ingester().ingest_once(redis=redis)
    assert len(redis.xgroup_create_calls) == 1
    assert redis.xgroup_create_calls[0]["mkstream"] is True


async def test_busygroup_error_swallowed() -> None:
    redis = _FakeRedis(busygroup=True)
    stats = await _ingester().ingest_once(redis=redis)
    assert stats.entries_read == 0  # no raise — empty run completes


async def test_non_busygroup_group_error_raises() -> None:
    redis = _FakeRedis(group_create_error=Exception("NOAUTH Authentication required"))
    with pytest.raises(UpstreamUnavailableError):
        await _ingester().ingest_once(redis=redis)


async def test_pel_entries_reclaimed_and_acked() -> None:
    writer = _StubWriter()
    redis = _FakeRedis(
        pel_entries=[
            _entry("0-9", {"trend_id": "stale", "finished_at": "2026-06-10T12:00:00+00:00"})
        ],
        batches=[[_entry("2-1", {"trend_id": "fresh", "finished_at": "2026-06-11T02:00:00+00:00"})]],
    )
    stats = await _ingester(writer).ingest_once(redis=redis)

    assert len(redis.xautoclaim_calls) == 1
    assert redis.xautoclaim_calls[0]["min_idle_time"] == 300_000
    assert stats.entries_read == 2  # 1 reclaimed + 1 new
    assert stats.entries_written == 2
    acked = set(redis.xack_calls[0][2:])
    assert acked == {"0-9", "2-1"}


async def test_pel_reclaim_three_tuple_reply_handled() -> None:
    redis = _FakeRedis(
        pel_entries=[
            _entry("0-5", {"trend_id": "stale", "finished_at": "2026-06-10T00:00:00+00:00"})
        ],
        three_tuple_reply=True,
    )
    stats = await _ingester().ingest_once(redis=redis)
    assert stats.entries_read == 1
    assert stats.entries_written == 1


async def test_missing_xautoclaim_is_graceful() -> None:
    redis = _FakeRedisNoAutoclaim(
        batches=[[_entry("3-1", {"trend_id": "t", "finished_at": "2026-06-11T03:00:00+00:00"})]]
    )
    stats = await _ingester().ingest_once(redis=redis)
    assert stats.entries_read == 1
    assert stats.entries_written == 1


async def test_xautoclaim_failure_does_not_block_new_entries() -> None:
    redis = _FakeRedis(
        autoclaim_error=RuntimeError("xautoclaim unsupported"),
        batches=[[_entry("4-1", {"trend_id": "t", "finished_at": "2026-06-11T04:00:00+00:00"})]],
    )
    stats = await _ingester().ingest_once(redis=redis)
    assert stats.entries_read == 1
    assert stats.entries_written == 1


async def test_failed_write_leaves_entries_unacked() -> None:
    """Persistence failure → exception propagates, nothing ACKed → PEL retry."""
    writer = _StubWriter(fail=True)
    redis = _FakeRedis(
        batches=[[_entry("5-1", {"trend_id": "t", "finished_at": "2026-06-11T05:00:00+00:00"})]]
    )
    with pytest.raises(RuntimeError, match="simulated parquet write failure"):
        await _ingester(writer).ingest_once(redis=redis)
    assert redis.xack_calls == []


async def test_read_batch_size_respected() -> None:
    redis = _FakeRedis()
    ingester = RedisStreamIngester(writer=_StubWriter(), read_batch=42)  # type: ignore[arg-type]
    await ingester.ingest_once(redis=redis)
    assert redis.xreadgroup_calls[0]["count"] == 42
    assert redis.xautoclaim_calls[0]["count"] == 42


async def test_counts_accurate_with_poison_entries() -> None:
    """Bad-JSON rows are ACKed (poison-pill guard) but not written."""
    writer = _StubWriter()
    redis = _FakeRedis(
        batches=[
            [
                _entry("6-1", {"trend_id": "ok", "finished_at": "2026-06-11T06:00:00+00:00"}),
                _entry("6-2", "{not valid json"),
            ]
        ]
    )
    stats = await _ingester(writer).ingest_once(redis=redis)
    assert stats.entries_read == 2
    assert stats.entries_written == 1
    acked = set(redis.xack_calls[0][2:])
    assert acked == {"6-1", "6-2"}  # poison entry ACKed so the group never wedges


async def test_empty_stream_returns_zero_stats() -> None:
    redis = _FakeRedis()
    stats = await _ingester().ingest_once(redis=redis)
    assert stats.entries_read == 0
    assert stats.entries_written == 0
    assert stats.partitions_written == 0
    assert stats.last_message_id is None
    assert redis.xack_calls == []
