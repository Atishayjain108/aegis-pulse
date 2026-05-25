# Phase 10 Integration Guide

How Phase 10 plugs into the existing AEGIS Pulse stack (Phases 1–4).

## Phase 1 (Postgres signals)

Phase 10 reads from the `signals` table via **asyncpg** keyset pagination on
`(captured_at, signal_id)`. It honors RLS by issuing
`SET LOCAL app.current_tenant = $1` inside each transaction.

```python
from aegis.datalake.bronze.ingest_postgres import PostgresSignalsIngester
from aegis.datalake.facade import DataLake
from aegis.datalake.settings import DataLakeSettings

import asyncpg
import asyncio

async def daily():
    pool = await asyncpg.create_pool(dsn=...)
    with DataLake.session(DataLakeSettings()) as lake:
        ingester = PostgresSignalsIngester(writer=lake.bronze)
        stats = await ingester.ingest(pool=pool)
        print(stats)
    await pool.close()

asyncio.run(daily())
```

## Phase 2 (Redis graph_results stream)

Phase 10 consumes the `aegis:phase2:graph_results` stream. The stream field
name is **`body`** (not `payload` — historical gotcha documented in
`CLAUDE.md`).

```python
from aegis.datalake.bronze.ingest_redis import RedisStreamIngester
import redis.asyncio as aioredis

async def drain_phase2():
    client = aioredis.from_url("redis://localhost:6380/0")
    with DataLake.session() as lake:
        ing = RedisStreamIngester(
            writer=lake.bronze,
            stream_key="aegis:phase2:graph_results",
            consumer_name="datalake-1",
        )
        stats = await ing.ingest_once(redis=client, max_iterations=10)
        print(stats)
    await client.aclose()
```

The consumer group `aegis-datalake-bronze` is auto-created (BUSYGROUP-tolerant).

## Phase 3 (predictions)

Reads from the Phase 3 `predictions` TimescaleDB hypertable via
`PostgresPredictionsIngester`. Same RLS + asyncpg conventions as Phase 1.

## Phase 4 (alerts)

Reads from the Phase 4 `alerts` table via `PostgresAlertsIngester`. The Phase 4
FastAPI app can mount the Phase 10 read-only router under `/datalake`:

```python
# In aegis-phase4/src/aegis/execute/api/main.py
from aegis.datalake.api import build_router as build_datalake_router
from aegis.datalake.facade import DataLake
from aegis.datalake.settings import DataLakeSettings

app = FastAPI()
# ... existing setup ...
lake = DataLake.open(DataLakeSettings())
app.include_router(build_datalake_router(lake=lake))

@app.on_event("shutdown")
async def _close_lake() -> None:
    lake.close()
```

## Dashboard

The Phase 1 dashboard (`aegis-dashboard` on :8300) can call the Phase 4
`/datalake/query` endpoint directly, or query DuckDB in-process by sharing the
same `DataLake` instance.

## Storage layout

Object keys follow the canonical layout:

```
{layer}/{table}/dt=YYYY-MM-DD/tenant_id={uuid}/{batch_id}.parquet
{layer}/{table}/dt=YYYY-MM-DD/tenant_id={uuid}/{batch_id}_manifest.json
```

This is **Hive partition layout**, so DuckDB's `hive_partitioning=true` automatically
populates `dt` and `tenant_id` as queryable columns.

## CLI

A typical day:

```bash
# Bronze ingest from Postgres
aegis-datalake ingest-postgres-signals --dsn $AEGIS_DATALAKE_POSTGRES_DSN
aegis-datalake ingest-postgres-predictions --dsn $AEGIS_DATALAKE_POSTGRES_DSN
aegis-datalake ingest-postgres-alerts --dsn $AEGIS_DATALAKE_POSTGRES_DSN

# Drain Redis stream
aegis-datalake ingest-redis --redis-url $AEGIS_DATALAKE_REDIS_URL

# Silver + Gold for today
aegis-datalake build-silver --date today
aegis-datalake build-gold --date today

# OR: one-shot end-to-end
aegis-datalake daily --date today --dsn $AEGIS_DATALAKE_POSTGRES_DSN
```

## Environment variables

| Variable                              | Default                                          | Meaning                              |
|---------------------------------------|--------------------------------------------------|--------------------------------------|
| `AEGIS_DATALAKE_BUCKET`               | `aegis-datalake`                                 | MinIO bucket                          |
| `AEGIS_DATALAKE_S3_ENDPOINT`          | `http://localhost:9002`                          | MinIO endpoint (Phase 1 uses 9002)    |
| `AEGIS_DATALAKE_S3_ACCESS_KEY`        | —                                                | MinIO access key                      |
| `AEGIS_DATALAKE_S3_SECRET_KEY`        | —                                                | MinIO secret key                      |
| `AEGIS_DATALAKE_USE_LOCAL_FILESYSTEM` | `false`                                          | Use FS instead of MinIO (dev)         |
| `AEGIS_DATALAKE_LOCAL_ROOT`           | `~/.aegis-datalake`                              | Local FS root when above is true      |
| `AEGIS_DATALAKE_CATALOG_DB_PATH`      | `~/.aegis-datalake/catalog.db`                   | SQLite catalog path                   |
| `AEGIS_DATALAKE_POSTGRES_DSN`         | `postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis` | Phase 1 DB                       |
| `AEGIS_DATALAKE_REDIS_URL`            | `redis://localhost:6380/0`                       | Phase 2 stream                        |
| `AEGIS_DATALAKE_TENANT_ID`            | `00000000-0000-0000-0000-000000000001`           | RLS tenant scope                      |
| `AEGIS_DATALAKE_SILVER_RETENTION_DAYS`| `365`                                            | Silver retention policy               |
| `AEGIS_DATALAKE_GOLD_RETENTION_DAYS`  | `1095`                                           | Gold retention policy (~3y)           |
