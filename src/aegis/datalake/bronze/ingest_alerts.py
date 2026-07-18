"""Phase 4 ``alerts`` → Bronze ingester.

Reads from the Phase 4 ``alerts`` TimescaleDB hypertable (migration
``0003_execute.sql`` in the main repo) and lands records in Bronze
partitioned by ``created_at`` UTC date.

Why a separate ingester (rather than reusing the signals one)
------------------------------------------------------------
* Different partition key (``created_at`` vs ``captured_at``).
* Different keyset cursor.
* Alerts carry richer envelope fields (verdict, score, confidence, halt_reason)
  that we want to bring through to Bronze raw — Silver/Gold will lift those
  out into typed columns.
"""

from __future__ import annotations

import json
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
class AlertIngestCursor:
    """Last successfully ingested alert watermark."""

    last_created_at: datetime
    last_alert_id: str


@dataclass(frozen=True, slots=True)
class AlertIngestStats:
    rows_read: int
    rows_written: int
    partitions_written: int
    new_cursor: AlertIngestCursor | None


class PostgresAlertsIngester:
    """Pull rows from Phase 4 ``alerts`` into Bronze."""

    BRONZE_TABLE = "alerts"

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
        pool: Any,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> AlertIngestStats:
        """Pull all alerts with ``created_at`` in ``(since, until)``."""
        if pool is None:
            raise UpstreamUnavailableError(
                "no asyncpg pool provided",
                hint="pass the Phase 4 / Phase 1 asyncpg pool",
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
        last_seen_ts: datetime | None = None
        last_seen_id: str | None = None
        batches_done = 0

        async for batch in self._read_batches(pool=pool, since=since, until=until):
            if not batch:
                break
            rows_read += len(batch)
            for row in batch:
                created = row["created_at"]
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                day_key = created.date().isoformat()
                per_day_buckets[day_key].append(_row_to_bronze(row))
                if last_seen_ts is None or created > last_seen_ts:
                    last_seen_ts = created
                    last_seen_id = str(row["alert_id"])
            batches_done += 1
            if batches_done >= self._max_batches_per_run:
                _log.info(
                    "alerts.ingest.max_batches_reached",
                    batches_done=batches_done,
                )
                break

        partitions_written = 0
        for day_key, rows in sorted(per_day_buckets.items()):
            manifest = self._writer.write(
                table=self.BRONZE_TABLE,
                rows=rows,
                source="postgres.alerts",
                partition_date=day_key,
                tenant_id=self._tenant_id,
            )
            partitions_written += 1
            rows_written += manifest.row_count

        new_cursor = (
            AlertIngestCursor(
                last_created_at=last_seen_ts,
                last_alert_id=last_seen_id or "",
            )
            if last_seen_ts is not None
            else None
        )
        return AlertIngestStats(
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
        """Keyset-paginated reader over ``(created_at, alert_id)``."""
        cursor_ts: datetime = since
        cursor_id: str | None = None

        sql_first = """
            SELECT *
            FROM alerts
            WHERE tenant_id = $1
              AND created_at > $2
              AND created_at < $3
            ORDER BY created_at ASC, alert_id ASC
            LIMIT $4
        """
        sql_paged = """
            SELECT *
            FROM alerts
            WHERE tenant_id = $1
              AND ((created_at = $2 AND alert_id::text > $3)
                   OR created_at > $2)
              AND created_at < $4
            ORDER BY created_at ASC, alert_id ASC
            LIMIT $5
        """

        while True:
            async with pool.acquire() as conn:
                await conn.execute(
                    "SET LOCAL app.current_tenant = $1", self._tenant_id
                )
                if cursor_id is None:
                    records = await conn.fetch(
                        sql_first,
                        self._tenant_id,
                        cursor_ts,
                        until,
                        self._batch_size,
                    )
                else:
                    records = await conn.fetch(
                        sql_paged,
                        self._tenant_id,
                        cursor_ts,
                        cursor_id,
                        until,
                        self._batch_size,
                    )
            if not records:
                return
            batch = [dict(r) for r in records]
            yield batch
            last = batch[-1]
            cursor_ts = last["created_at"]
            if cursor_ts.tzinfo is None:
                cursor_ts = cursor_ts.replace(tzinfo=UTC)
            cursor_id = str(last["alert_id"])
            if len(batch) < self._batch_size:
                return


def _row_to_bronze(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=UTC)
            out[k] = v.isoformat()
        elif isinstance(v, dict | list):
            out[k] = v
        elif hasattr(v, "hex") and not isinstance(v, bytes | str):
            out[k] = str(v)
        else:
            try:
                json.dumps(v)
                out[k] = v
            except (TypeError, ValueError):
                out[k] = str(v)
    return out


__all__ = [
    "AlertIngestCursor",
    "AlertIngestStats",
    "PostgresAlertsIngester",
]
