"""Phase 3 ``predictions`` → Bronze ingester.

Pulls the Phase 3 prediction rows out of Postgres into the Bronze layer.
The Phase 3 schema is documented in ``db/migrations/0002_predictions.sql``
of the main repo. Key columns we capture for the lake:

* ``prediction_id`` — UUID primary key.
* ``trend_id`` / ``tenant_id`` — partitioning + RLS keys.
* ``finished_at`` — TimescaleDB partition key in Phase 3; we use it as the
  Bronze partition key too so the lake and the source are aligned.
* probability fields, horizons, model manifest, latency stats — all stored
  verbatim in ``raw_json`` so Silver can reshape without re-reading source.

The ingester is *async* and uses keyset pagination by ``(finished_at, prediction_id)``
to handle large backfills without lookahead leakage.
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
class PredictionIngestCursor:
    """Last successfully ingested prediction watermark."""

    last_finished_at: datetime
    last_prediction_id: str


@dataclass(frozen=True, slots=True)
class PredictionIngestStats:
    rows_read: int
    rows_written: int
    partitions_written: int
    new_cursor: PredictionIngestCursor | None


class PostgresPredictionsIngester:
    """Pull rows from Phase 3 ``predictions`` into Bronze.

    Parameters
    ----------
    writer:
        Configured :class:`BronzeWriter`.
    tenant_id:
        Tenant under which to read (RLS scoping).
    batch_size:
        Rows per keyset page. Keep moderate (5k) to balance memory + round trips.
    max_batches_per_run:
        Safety stop — bounds a single run when backfilling huge ranges.
    """

    BRONZE_TABLE = "predictions"

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
    ) -> PredictionIngestStats:
        """Pull all predictions with ``finished_at`` in ``(since, until)``."""
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
        last_seen_ts: datetime | None = None
        last_seen_id: str | None = None
        batches_done = 0

        async for batch in self._read_batches(pool=pool, since=since, until=until):
            if not batch:
                break
            rows_read += len(batch)
            for row in batch:
                finished = row["finished_at"]
                if finished.tzinfo is None:
                    finished = finished.replace(tzinfo=UTC)
                day_key = finished.date().isoformat()
                per_day_buckets[day_key].append(_row_to_bronze(row))
                if last_seen_ts is None or finished > last_seen_ts:
                    last_seen_ts = finished
                    last_seen_id = str(row["prediction_id"])
            batches_done += 1
            if batches_done >= self._max_batches_per_run:
                _log.info(
                    "predictions.ingest.max_batches_reached",
                    batches_done=batches_done,
                )
                break

        partitions_written = 0
        for day_key, rows in sorted(per_day_buckets.items()):
            manifest = self._writer.write(
                table=self.BRONZE_TABLE,
                rows=rows,
                source="postgres.predictions",
                partition_date=day_key,
                tenant_id=self._tenant_id,
            )
            partitions_written += 1
            rows_written += manifest.row_count

        new_cursor = (
            PredictionIngestCursor(
                last_finished_at=last_seen_ts,
                last_prediction_id=last_seen_id or "",
            )
            if last_seen_ts is not None
            else None
        )
        return PredictionIngestStats(
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
        """Keyset-paginated reader. Tolerates Phase 3 schema variants gracefully."""
        cursor_ts: datetime = since
        cursor_id: str | None = None

        # We SELECT * to be resilient to additive schema changes — the Bronze
        # layer is schema-on-read by design.
        sql_first = """
            SELECT *
            FROM predictions
            WHERE tenant_id = $1
              AND finished_at > $2
              AND finished_at < $3
            ORDER BY finished_at ASC, prediction_id ASC
            LIMIT $4
        """
        sql_paged = """
            SELECT *
            FROM predictions
            WHERE tenant_id = $1
              AND ((finished_at = $2 AND prediction_id::text > $3)
                   OR finished_at > $2)
              AND finished_at < $4
            ORDER BY finished_at ASC, prediction_id ASC
            LIMIT $5
        """

        while True:
            async with pool.acquire() as conn:
                # RLS scoping — Phase 1 convention.
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
            cursor_ts = last["finished_at"]
            if cursor_ts.tzinfo is None:
                cursor_ts = cursor_ts.replace(tzinfo=UTC)
            cursor_id = str(last["prediction_id"])
            if len(batch) < self._batch_size:
                return


def _row_to_bronze(row: dict[str, Any]) -> dict[str, Any]:
    """Convert an asyncpg Record-dict into a JSON-serializable Bronze row."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=UTC)
            out[k] = v.isoformat()
        elif isinstance(v, dict | list):
            out[k] = v
        elif hasattr(v, "hex") and not isinstance(v, bytes | str):
            # UUID
            out[k] = str(v)
        else:
            try:
                json.dumps(v)
                out[k] = v
            except (TypeError, ValueError):
                out[k] = str(v)
    return out


__all__ = [
    "PostgresPredictionsIngester",
    "PredictionIngestCursor",
    "PredictionIngestStats",
]
