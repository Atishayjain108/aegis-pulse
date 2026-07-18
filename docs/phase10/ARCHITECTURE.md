# Phase 10 Architecture

## Overview

Phase 10 is the **medallion data lake** layer of AEGIS Pulse: Bronze (raw,
immutable Parquet), Silver (typed, conformed), Gold (business-ready
aggregations), all queried via embedded DuckDB.

## Design principles

* **Heuristic-first** — every transform is deterministic; LLMs do not touch
  the data path. This matches the project-wide doctrine and keeps the lake
  reproducible byte-for-byte.
* **Schema-on-read in Bronze, schema-on-write in Silver/Gold** — Bronze
  tolerates upstream drift; Silver/Gold enforce strict Pydantic models with a
  quality-gate quarantine path for rejects.
* **Library, not a service** — Phase 10 runs inside the existing Phase 1+
  Python process. The optional Prefect server is purely a UI; flows are plain
  async functions when Prefect isn't installed.
* **Content-addressable batches** — every Parquet write is keyed by
  `sha256(canonical_json(rows))`, so re-running a Bronze write with identical
  rows produces the same file path and a no-op.

## Component diagram

```
+-------------+    +---------------+    +---------+    +---------+
|  Phase 1    | -> | BronzeWriter  | -> | Storage | -> | Catalog |
|  Postgres   |    | (idempotent)  |    | (MinIO  |    | (SQLite)|
+-------------+    +---------------+    |  or FS) |    +---------+
                          ^             +---------+         ^
                          |                                 |
+-------------+    +---------------+                        |
|  Phase 2    | -> | RedisStream   |                        |
|  stream     |    | Ingester      |                        |
+-------------+    +---------------+                        |
                                                            |
+-------------+    +---------------+    +---------+         |
| Bronze rows | -> | SilverBuilder | -> | Storage | --------+
|             |    | + QualityGate |    |         |         |
+-------------+    +---------------+    +---------+         |
                                                            |
+-------------+    +---------------+    +---------+         |
| Silver rows | -> | GoldAggregator| -> | Storage | --------+
|             |    | (pure agg)    |    |         |
+-------------+    +---------------+    +---------+

                                        +-----------------+
                                        | DuckDB engine   |
                                        | + httpfs(S3)    |
                                        | + read-only AL  |
                                        +-----------------+
```

## Concurrency model

* **Single-process** — Bronze writes, Silver builds, Gold aggregations all
  run sequentially inside one Python process. There is no distributed
  coordination, no Spark, no Dask.
* **Optimistic concurrency** — last-write-wins on the catalog (SQLite WAL
  mode handles concurrent reads). Idempotency is enforced at the file-path
  level, not via locks.
* **DuckDB** — opens a single connection per `DuckDBQueryEngine`; protected
  by an internal `threading.RLock` so it is safe across threads in the
  same process.

## Failure modes

| Failure                                | Behavior                                                                                  |
|----------------------------------------|-------------------------------------------------------------------------------------------|
| Bronze write fails mid-flight          | Atomic write via tmp+rename → no partial file observable                                  |
| Catalog SQLite write fails             | Partition file stays on disk; next list_partitions reconciles via list_manifests          |
| Silver row fails Pydantic schema       | Quarantined to `{table}__rejected` partition; pipeline continues                          |
| Gold aggregation throws                | Stats reported; previous Gold partition untouched                                         |
| DuckDB query times out                 | `QueryTimeoutError` returned with stable AEGIS-DATALAKE-0401 code                          |
| MinIO unreachable                       | StorageError raised with hint; Bronze readers retry-able via Prefect/CLI re-runs           |

## Audit trail

Every Bronze, Silver, and Gold write produces a `_manifest.json` sidecar
containing `sha256`, `row_count`, `byte_size`, `written_at`, source, and
`batch_id`. The Catalog records these in the `partitions` table for fast
listing. Lineage edges (`upstream_table` → `downstream_table`, `transform`)
are recorded by the Silver/Gold builders.
