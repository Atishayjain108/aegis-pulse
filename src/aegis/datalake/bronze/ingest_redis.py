"""Redis Streams → Bronze ingester.

Pulls Phase 2 graph results from the ``aegis:phase2:graph_results`` Redis
stream and lands them in the ``agent_results`` Bronze table.

Uses a consumer group (``aegis-datalake-bronze``) so multiple datalake
workers can scale horizontally without double-reading.

CRITICAL: stream field is ``body`` (not ``payload`` — see CLAUDE.md gotcha
fixed 2026-05-18).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aegis.datalake import constants as C
from aegis.datalake._logging import get_logger
from aegis.datalake.bronze.writer import BronzeWriter
from aegis.datalake.errors import UpstreamUnavailableError

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StreamIngestStats:
    """Outcome of a stream ingest run."""

    entries_read: int
    entries_written: int
    partitions_written: int
    last_message_id: str | None


class RedisStreamIngester:
    """Generic Phase-2-style stream → Bronze ingester.

    The default config reads :data:`PHASE2_STREAM_KEY` but the same class
    handles the Swarm stream too — just pass a different ``stream_key`` and
    ``consumer_group``.
    """

    def __init__(
        self,
        *,
        writer: BronzeWriter,
        bronze_table: str = "agent_results",
        stream_key: str = C.PHASE2_STREAM_KEY,
        stream_field: str = C.PHASE2_STREAM_FIELD,
        consumer_group: str = C.PHASE2_CONSUMER_GROUP,
        consumer_name: str = C.PHASE2_CONSUMER_NAME_DEFAULT,
        tenant_id: str = C.DEFAULT_TENANT_ID,
        block_ms: int = C.PHASE2_BLOCK_MS,
        read_batch: int = C.PHASE2_READ_BATCH,
        source_label: str = "redis.phase2_graph_results",
    ) -> None:
        self._writer = writer
        self._bronze_table = bronze_table
        self._stream_key = stream_key
        self._stream_field = stream_field
        self._consumer_group = consumer_group
        self._consumer_name = consumer_name
        self._tenant_id = tenant_id
        self._block_ms = block_ms
        self._read_batch = read_batch
        self._source_label = source_label

    async def _ensure_group(self, redis: Any) -> None:
        """Create the consumer group if it doesn't exist (idempotent)."""
        try:
            await redis.xgroup_create(
                name=self._stream_key,
                groupname=self._consumer_group,
                id="0-0",  # consume from beginning on first run
                mkstream=True,
            )
            _log.info(
                "redis.consumer_group.created",
                stream=self._stream_key,
                group=self._consumer_group,
            )
        except Exception as exc:
            # BUSYGROUP is normal — group already exists
            if "BUSYGROUP" in str(exc):
                return
            raise UpstreamUnavailableError(
                f"failed to ensure consumer group on {self._stream_key}: {exc}",
                hint="check Redis URL and that the stream exists",
            ) from exc

    async def _reclaim_stale_entries(
        self, redis: Any, *, min_idle_ms: int = 300_000
    ) -> list[tuple[Any, dict[Any, Any]]]:
        """Reclaim PEL entries held by dead consumers (crash recovery).

        CONN-4: a consumer that read entries and crashed before XACK leaves
        them in the Pending Entries List forever. XAUTOCLAIM transfers
        entries idle > 5 minutes to this consumer so they are re-processed.
        Returns ``[]`` when the client lacks ``xautoclaim`` (older redis-py
        or test fakes) or the call fails — reclaim is best-effort, the new
        entries path must never be blocked by it.
        """
        xautoclaim = getattr(redis, "xautoclaim", None)
        if xautoclaim is None:
            return []
        try:
            resp = await xautoclaim(
                self._stream_key,
                self._consumer_group,
                self._consumer_name,
                min_idle_time=min_idle_ms,
                start_id="0-0",
                count=self._read_batch,
            )
        except Exception as exc:
            _log.warning(
                "redis.xautoclaim.failed",
                stream=self._stream_key,
                error=str(exc),
            )
            return []
        # redis-py returns (next_start_id, entries) or
        # (next_start_id, entries, deleted_ids) depending on version.
        entries = resp[1] if isinstance(resp, list | tuple) and len(resp) >= 2 else []
        if entries:
            _log.info(
                "redis.xautoclaim.reclaimed",
                stream=self._stream_key,
                count=len(entries),
            )
        return list(entries or [])

    async def ingest_once(
        self,
        *,
        redis: Any,
        max_iterations: int = 10,
    ) -> StreamIngestStats:
        """Drain currently-available entries (one pass, no infinite loop).

        Returns immediately if no entries are pending.
        """
        if redis is None:
            raise UpstreamUnavailableError("no redis client provided")

        await self._ensure_group(redis)

        from collections import defaultdict

        per_day_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        entries_read = 0
        last_id: str | None = None
        successful_ids: list[str] = []

        # CONN-4: crash recovery — process entries abandoned in the PEL by a
        # dead consumer before reading new ones.
        for msg_id, fields in await self._reclaim_stale_entries(redis):
            entries_read += 1
            last_id = _decode_if_bytes(msg_id)
            bronze_row = self._decode_entry(last_id, fields)
            if bronze_row is None:
                successful_ids.append(last_id)
                continue
            day_key = _to_day_key(bronze_row.get("finished_at"))
            per_day_buckets[day_key].append(bronze_row)
            successful_ids.append(last_id)

        for _ in range(max_iterations):
            try:
                resp = await redis.xreadgroup(
                    groupname=self._consumer_group,
                    consumername=self._consumer_name,
                    streams={self._stream_key: ">"},
                    count=self._read_batch,
                    block=self._block_ms,
                )
            except Exception as exc:
                raise UpstreamUnavailableError(
                    f"xreadgroup failed: {exc}",
                    hint="check Redis connectivity",
                ) from exc

            if not resp:
                break

            # resp: [(stream_name, [(msg_id, {field: value}), ...]), ...]
            for _stream_name, entries in resp:
                if not entries:
                    continue
                for msg_id, fields in entries:
                    entries_read += 1
                    last_id = _decode_if_bytes(msg_id)
                    bronze_row = self._decode_entry(last_id, fields)
                    if bronze_row is None:
                        # Acknowledge bad rows too — never get stuck
                        successful_ids.append(last_id)
                        continue
                    finished_at = bronze_row.get("finished_at")
                    day_key = _to_day_key(finished_at)
                    per_day_buckets[day_key].append(bronze_row)
                    successful_ids.append(last_id)

        partitions_written = 0
        entries_written = 0
        for day_key, rows in sorted(per_day_buckets.items()):
            manifest = self._writer.write(
                table=self._bronze_table,
                rows=rows,
                source=self._source_label,
                partition_date=day_key,
                tenant_id=self._tenant_id,
            )
            partitions_written += 1
            entries_written += manifest.row_count

        # ACK after persistence — at-least-once with idempotent writes
        if successful_ids:
            try:
                await redis.xack(
                    self._stream_key, self._consumer_group, *successful_ids
                )
            except Exception as exc:  # pragma: no cover
                _log.warning("redis.xack.failed", error=str(exc))

        return StreamIngestStats(
            entries_read=entries_read,
            entries_written=entries_written,
            partitions_written=partitions_written,
            last_message_id=last_id,
        )

    def _decode_entry(
        self, msg_id: str, fields: dict[Any, Any]
    ) -> dict[str, Any] | None:
        """Decode a stream entry into a Bronze row.

        The Phase 2 stream emits ``{"body": <json-string>}``. We unwrap and
        flatten one level so the Bronze row is friendly to Arrow inference.
        """
        # redis-py returns bytes by default; tolerate both forms
        body_raw = fields.get(self._stream_field) or fields.get(
            self._stream_field.encode("utf-8")
        )
        if body_raw is None:
            _log.warning(
                "redis.entry.missing_body",
                msg_id=msg_id,
                fields=list(fields.keys()),
            )
            return None
        body_text = _decode_if_bytes(body_raw)
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as exc:
            _log.warning(
                "redis.entry.invalid_json", msg_id=msg_id, error=str(exc)
            )
            return None

        # Stamp ingestion metadata
        payload["_msg_id"] = msg_id
        payload["_ingested_at"] = datetime.now(UTC).isoformat()
        return payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _decode_if_bytes(v: Any) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8")
    return str(v)


def _to_day_key(value: Any) -> str:
    """Coerce an ISO/datetime field to a UTC date string."""
    if value is None:
        return datetime.now(UTC).date().isoformat()
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return datetime.now(UTC).date().isoformat()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).date().isoformat()


__all__ = ["RedisStreamIngester", "StreamIngestStats"]
