# Phase 10 Runbook

Operational procedures for the Data Lake. Each section gives a symptom, a
diagnosis, and a fix.

## Health check

```bash
aegis-datalake doctor --json-out
```

Expected output:

```json
{
  "phase": "phase10",
  "version": "0.10.0",
  "backend_ok": true,
  "catalog_ok": true,
  "tables_registered": <int>,
  "storage_kind": "s3" | "local",
  "bucket": "aegis-datalake",
  "tenant_id": "<uuid>",
  "catalog_schema_version": 1,
  "target_schema_version": 1
}
```

If `backend_ok` is false:
* Check MinIO is up: `docker compose ps aegis-minio`
* Check credentials: `echo $AEGIS_DATALAKE_S3_ACCESS_KEY`
* Test reachability: `curl -sf http://localhost:9002/minio/health/ready`

If `catalog_ok` is false:
* Check disk space: `df -h ~/.aegis-datalake`
* Check the SQLite is not locked by another process: `lsof catalog.db`
* As last resort, `cp catalog.db catalog.db.bak` and re-create — the lake
  catalog rebuilds itself from `_manifest.json` sidecars (see Disaster
  Recovery below).

## Daily ingest is stuck

Symptom: `aegis-datalake daily` runs but no new partitions appear.

Diagnosis:
1. Check Phase 1 connectivity:
   ```bash
   PGPASSWORD=aegis_app_dev_pw psql -h localhost -p 5433 -U aegis_app \
     -d aegis -c "SELECT max(captured_at) FROM signals;"
   ```
2. Check there ARE new signals to ingest. The default `since` is 7 days ago.

Fix: re-run with an explicit `--since`:

```bash
aegis-datalake ingest-postgres-signals \
  --dsn $AEGIS_DATALAKE_POSTGRES_DSN \
  --since 2026-05-01T00:00:00Z
```

## Silver build rejecting too many rows

Symptom: `silver.build_signals_for_date` reports `rows_rejected > 0`.

Diagnosis: rejected rows are written to a quarantine partition under
`silver/{table}__rejected/`. Inspect them:

```bash
aegis-datalake query \
  "SELECT * FROM silver_signals__rejected ORDER BY captured_at DESC LIMIT 20"
```

Common causes:
* Schema drift — a new upstream platform produced fields Silver doesn't
  yet handle. Update `_PLATFORM_TIER` registry in `silver/builder.py`.
* Bronze ingestion bug — a recent change broke serialisation. Check the
  `bronze.write.ok` log line for that batch.

If the reject rate is above the threshold, `QualityGate.validate_or_raise`
will surface a `AEGIS-DATALAKE-0301` error with a remediation hint.

## DuckDB query returns no rows but data exists

Symptom: `SELECT * FROM silver_signals` returns 0 rows, but `aegis-datalake
list-partitions signals --layer silver` shows partitions present.

Diagnosis: the view URI might be wrong. Confirm:

```bash
aegis-datalake query "DESCRIBE silver_signals"
```

If DESCRIBE works but SELECT is empty, the underlying Parquet files exist
but DuckDB's httpfs can't reach them. Check:

```bash
curl -I "$AEGIS_DATALAKE_S3_ENDPOINT/aegis-datalake/silver/signals/?list-type=2"
```

## Retention deleted too much

Phase 10 retention is **two-step** (plan → apply). If `--apply` was accidentally
passed:

* For Silver/Gold: re-run the Bronze → Silver → Gold pipeline for the affected
  dates. Bronze is the system of record.
* For Bronze (only with explicit `--bronze-retention-days`): restore from the
  MinIO bucket's versioning history if enabled; otherwise the deletion is
  permanent.

**Best practice**: always run `aegis-datalake retention <layer>` (no `--apply`)
first, inspect the plan, then `--apply` after a peer review.

## Disaster recovery

The catalog is *re-buildable from manifests*. If `catalog.db` is corrupted:

```bash
# 1. Move the bad catalog aside
mv ~/.aegis-datalake/catalog.db ~/.aegis-datalake/catalog.db.broken

# 2. Re-run doctor — it creates a fresh empty catalog at v1.
aegis-datalake doctor

# 3. (Future) Run rebuild-from-manifests (not yet shipped — TODO #PHASE10-REBUILD).
```

In the interim, manually re-register tables by re-running the Bronze writers
against existing partitions — Bronze auto-registers on first write, so simply
re-ingesting one row per `(table, layer)` is enough to make the catalog usable.

## Performance tuning

Phase 10 is designed for laptop-scale workloads. If you hit limits:

| Limit                       | Knob                                              |
|----------------------------|--------------------------------------------------|
| DuckDB memory               | `AEGIS_DATALAKE_DUCKDB_MEMORY_LIMIT_MB` (default 2048) |
| DuckDB parallelism          | `AEGIS_DATALAKE_DUCKDB_THREAD_COUNT`             |
| Parquet compression         | edit `PARQUET_COMPRESSION` in `constants.py` (default `zstd`) |
| Parquet row-group size      | edit `PARQUET_ROW_GROUP_SIZE` (default 128_000)  |
| Bronze batch size           | `AEGIS_DATALAKE_BRONZE_BATCH_MAX_ROWS` (default 50_000) |

For >100M rows, switch to S3 with object lifecycle policies and consider
running DuckDB against Parquet via `read_parquet('s3://...', filename=true)`
patterns to skip catalog overhead.

## Where to find help

* Stable error codes: `AEGIS-DATALAKE-NNNN` — grep `src/aegis/datalake/errors.py`
* Architecture: `docs/phase10/ARCHITECTURE.md`
* Integration with other phases: `docs/phase10/INTEGRATION.md`
* Schemas: `docs/phase10/SCHEMAS.md`
