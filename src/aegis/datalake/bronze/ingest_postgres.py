"""Postgres → Bronze ingester.

Reads from the Phase 1 ``signals`` table in incremental cursor mode
(by ``captured_at``) and emits Bronze partitions grouped by UTC date.

The cursor is persisted in the catalog so re-runs are exactly-once.
Uses asyncpg to share the Phase 1 connection convention.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aegis.datalake import constants as C
from aegis.datalake._logging import get_logger
from aegis.datalake.bronze.writer import BronzeWriter
from aegis.datalake.errors import UpstreamUnavailableError

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IngestCursor:
    """Last successfully ingested timestamp (exclusive lower bound)."""

    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class IngestStats:
    rows_read: int
    rows_written: int
    partitions_written: int
    new_cursor: IngestCursor | None


class PostgresSignalsIngester:
    """Pull rows from Phase 1 ``signals`` into Bronze.

    The ingester is *async* — it composes with the existing asyncpg pool
    from `aegis.db.pool`. If the pool import fails (e.g. lighter Phase 10
    install without Phase 1's deps), a clear error is raised on first use.
    """

    BRONZE_TABLE = "signals"

    def __init__(
        self,
        *,
        writer: BronzeWriter,
        tenant_id: str = C.DEFAULT_TENANT_ID,
        batch_size: int = 5_000,
        max_batches_per_run: int = 100,
    ) -> None:
        self._writer = writer
        self._tenant_id = tenant_id
        self._batch_size = batch_size
        self._max_batches_per_run = max_batches_per_run

    async def ingest(
        self,
        *,
        pool: Any,  # asyncpg.Pool
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> IngestStats:
        """Pull all signals newer than ``since`` up to ``until``.

        Args:
            pool: An asyncpg pool (Phase 1's shared pool is fine).
            since: Exclusive lower bound on ``captured_at``. Default: 7 days ago.
            until: Exclusive upper bound. Default: now.

        Returns:
            Stats describing the run.
        """
        if pool is None:
            raise UpstreamUnavailableError(
                "no asyncpg pool provided",
                hint="pass aegis.db.pool.get_shared_pool() or build one explicitly",
            )

        if since is None:
            since = datetime.now(UTC) - timedelta(days=7)
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        if until is None:
            until = datetime.now(UTC)
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)

        rows_read = 0
        rows_written = 0
        per_day_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        last_seen: datetime | None = None
        batches_done = 0

        async for batch in self._read_batches(pool=pool, since=since, until=until):
            if not batch:
                break
            rows_read += len(batch)
            for row in batch:
                # group by UTC date for partitioning
                captured = row["captured_at"]
                if captured.tzinfo is None:
                    captured = captured.replace(tzinfo=UTC)
                day_key = captured.date().isoformat()
                per_day_buckets[day_key].append(_row_to_bronze(row))
                if last_seen is None or captured > last_seen:
                    last_seen = captured
            batches_done += 1
            if batches_done >= self._max_batches_per_run:
                _log.info(
                    "ingest.max_batches_reached",
                    batches_done=batches_done,
                    max=self._max_batches_per_run,
                )
                break

        partitions_written = 0
        for day_key, rows in sorted(per_day_buckets.items()):
            manifest = self._writer.write(
                table=self.BRONZE_TABLE,
                rows=rows,
                source="postgres.signals",
                partition_date=day_key,
                tenant_id=self._tenant_id,
            )
            partitions_written += 1
            rows_written += manifest.row_count

        new_cursor = IngestCursor(last_seen_at=last_seen) if last_seen else None
        return IngestStats(
            rows_read=rows_read,
            rows_written=rows_written,
            partitions_written=partitions_written,
            new_cursor=new_cursor,
        )

    async def _read_batches(
        self,
        *,
        pool: Any,
        since: datetime,
        until: datetime,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Yield successive batches of signal rows.

        Uses keyset pagination by (captured_at, signal_id) so it tolerates
        gigantic tables without ORDER BY OFFSET pain.
        """
        cursor_ts: datetime = since
        cursor_id: str | None = None
        sql_first = """
            SELECT signal_id, tenant_id, platform, title, url, author_handle,
                   captured_at, views, likes, comments, shares, saves,
                   raw_json
            FROM signals
            WHERE tenant_id = $1
              AND captured_at > $2
              AND captured_at < $3
            ORDER BY captured_at ASC, signal_id ASC
            LIMIT $4
        """
        sql_paged = """
            SELECT signal_id, tenant_id, platform, title, url, author_handle,
                   captured_at, views, likes, comments, shares, saves,
                   raw_json
            FROM signals
            WHERE tenant_id = $1
              AND ((captured_at = $2 AND signal_id > $3) OR captured_at > $2)
              AND captured_at < $4
            ORDER BY captured_at ASC, signal_id ASC
            LIMIT $5
        """

        try:
            async with pool.acquire() as conn:
                # RLS: scope to tenant
                await conn.execute(
                    "SET LOCAL app.current_tenant = $1", self._tenant_id
                )

                while True:
                    if cursor_id is None:
                        rows = await conn.fetch(
                            sql_first,
                            self._tenant_id,
                            cursor_ts,
                            until,
                            self._batch_size,
                        )
                    else:
                        rows = await conn.fetch(
                            sql_paged,
                            self._tenant_id,
                            cursor_ts,
                            cursor_id,
                            until,
                            self._batch_size,
                        )
                    if not rows:
                        break
                    yield [dict(r) for r in rows]
                    last = rows[-1]
                    cursor_ts = last["captured_at"]
                    cursor_id = last["signal_id"]
                    if len(rows) < self._batch_size:
                        break
        except Exception as exc:
            raise UpstreamUnavailableError(
                f"postgres signals ingest failed: {exc}",
                hint="check Phase 1 Postgres is reachable and signals table exists",
            ) from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _row_to_bronze(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a Postgres row into a Bronze-friendly dict.

    asyncpg returns native Python types — we keep them, but normalise
    datetime to UTC and serialise JSONB to dict.
    """
    out = dict(row)
    captured = out.get("captured_at")
    if isinstance(captured, datetime):
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=UTC)
        out["captured_at"] = captured.astimezone(UTC)
    # asyncpg returns JSONB as Python dict already; nothing to do.
    return out


__all__ = ["IngestCursor", "IngestStats", "PostgresSignalsIngester"]
