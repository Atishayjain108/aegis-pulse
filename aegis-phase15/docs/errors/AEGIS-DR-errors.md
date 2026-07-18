# AEGIS-DR-0001 — PostgreSQL Backup Failed

**Phase**: 15 (Disaster Recovery)  
**Severity**: High  
**Machine code**: `AEGIS-DR-0001`

## What happened

`pg_dump` exited with a non-zero return code, or timed out, or no `pg_dump`
binary was found in `PATH`.

## Diagnosis

```bash
# 1. Check if pg_dump is available
which pg_dump || echo "NOT FOUND"

# 2. Install if missing
sudo apt-get install -y postgresql-client

# 3. Test the DSN manually
pg_dump --format=custom --no-password \
  postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  --file=/tmp/test.dump && echo "OK"

# 4. Check Postgres container health
docker logs aegis-postgres --tail 50
```

## Fix

| Cause | Fix |
|-------|-----|
| `pg_dump` not installed | `sudo apt-get install -y postgresql-client` |
| Postgres container down | `docker compose up -d aegis-postgres` |
| Wrong DSN | Set `AEGIS_DR_PG_DSN` in `.env` |
| Timeout | Increase `AEGIS_DR_PG_DUMP_TIMEOUT_S` (default 600) |
| Permissions | Ensure `aegis_app` role has `CONNECT` + `SELECT` grants |

## Related

- `AEGIS-DR-0010` — PostgreSQL restore failed
- `AEGIS-DR-0003` — MinIO upload failed

---

# AEGIS-DR-0002 — Redis Backup Failed

**Phase**: 15  
**Severity**: Medium  
**Machine code**: `AEGIS-DR-0002`

## What happened

`BGSAVE` timed out, the RDB file was not found at the configured path,
or the upload to MinIO failed.

## Diagnosis

```bash
# 1. Check Redis is alive
redis-cli -p 6380 PING

# 2. Trigger a manual BGSAVE and check timing
redis-cli -p 6380 BGSAVE
redis-cli -p 6380 LASTSAVE

# 3. Verify RDB path
docker exec aegis-redis ls -lh /data/dump.rdb
```

## Fix

| Cause | Fix |
|-------|-----|
| RDB path wrong | Set `AEGIS_DR_REDIS_RDB_PATH` to the correct container path |
| BGSAVE timeout | Increase `AEGIS_DR_REDIS_BGSAVE_TIMEOUT_S` (default 120) |
| Redis OOM preventing save | See AEGIS-DR runbook `redis_oom` |

---

# AEGIS-DR-0003 — MinIO Upload Failed

**Phase**: 15  
**Severity**: High  
**Machine code**: `AEGIS-DR-0003`

## What happened

A backup file could not be uploaded to the MinIO DR bucket.

## Diagnosis

```bash
# 1. Check MinIO is healthy
curl -s http://localhost:9002/minio/health/live && echo "OK"

# 2. Check bucket exists
mc alias set local http://localhost:9002 aegis-dev-key aegis-dev-secret-please-change
mc ls local/aegis-dr/

# 3. Check disk space on MinIO volume
docker exec aegis-minio df -h /data
```

## Fix

| Cause | Fix |
|-------|-----|
| MinIO down | `docker compose up -d aegis-minio` |
| Bucket missing | `uv run aegis dr backup --target postgres` (auto-creates bucket) |
| Wrong credentials | Set `AEGIS_DR_MINIO_ACCESS_KEY` / `AEGIS_DR_MINIO_SECRET_KEY` |
| MinIO disk full | See AEGIS-DR runbook `disk_full` |

---

# AEGIS-DR-0010 — PostgreSQL Restore Failed

**Phase**: 15  
**Severity**: Critical  
**Machine code**: `AEGIS-DR-0010`

## What happened

`pg_restore` exited non-zero, timed out, or post-restore row-count
verification diverged beyond the 0.1% tolerance.

## Diagnosis

```bash
# 1. Check pg_restore is installed
which pg_restore

# 2. Test connectivity to target DB
psql "postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis" -c "SELECT 1"

# 3. Try a manual restore (from MinIO download)
mc cp local/aegis-dr/postgres/dt=<DATE>/<FILE>.dump /tmp/restore_test.dump
pg_restore --jobs=4 --no-owner \
  --dbname="postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis" \
  /tmp/restore_test.dump
```

## Fix

| Cause | Fix |
|-------|-----|
| `pg_restore` not installed | `sudo apt-get install -y postgresql-client` |
| Target DB does not exist | Run `uv run aegis db migrate` first |
| Dump file corrupt | Use an older backup — check `aegis dr backups` |
| Row count mismatch > 0.1% | Manual inspection — check `aegis signals tail` |

---

# AEGIS-DR-0020 — Restore Drill Timed Out

**Phase**: 15  
**Severity**: High  
**Machine code**: `AEGIS-DR-0020`

## What happened

The weekly restore drill did not complete within `DRILL_MAX_DURATION_S`
(default 1800s / 30 minutes). This means the RTO SLA **cannot be
demonstrated** for the current backup set.

## Diagnosis

```bash
# Run drill manually with verbose output
uv run aegis dr drill --dry-run
```

## Fix

1. Identify which target is slow (check drill logs for last completed step).
2. If Postgres restore is slow: reduce dump size (archive old signals to datalake, then drop from primary DB).
3. If download from MinIO is slow: check MinIO disk health (`docker exec aegis-minio df -h /data`).
4. Increase `DRILL_MAX_DURATION_S` as a temporary workaround while investigating.

---

# AEGIS-DR-0022 — No Backup Found

**Phase**: 15  
**Severity**: Critical  
**Machine code**: `AEGIS-DR-0022`

## What happened

A restore (or drill) was requested but no successful backup manifest was
found in the MinIO DR bucket.

## Diagnosis

```bash
# Check what's in the DR bucket
mc ls --recursive local/aegis-dr/ | head -30

# Run a manual backup
uv run aegis dr backup --target all

# Check orchestrator is running (it should auto-backup)
docker logs aegis-dashboard --tail 50 | grep "dr.backup"
```

## Fix

1. Run `uv run aegis dr backup --target all` to create a fresh baseline.
2. Verify the orchestrator background tasks are running (check dashboard `/dr/status`).
3. Ensure `AEGIS_DR_*` env vars are set correctly in `.env`.

---

# AEGIS-DR-0025 — RPO Breach

**Phase**: 15  
**Severity**: High  
**Machine code**: `AEGIS-DR-0025`

## What happened

The most recent successful backup is older than the RPO target (default 15 min).
If the system crashes now, more data than allowed would be lost.

## Diagnosis

```bash
uv run aegis dr status
```

## Fix

1. Run `uv run aegis dr backup --target postgres` immediately.
2. Check orchestrator health — it should be running backups automatically.
3. If the orchestrator died: `uv run aegis up` to restart all services.
4. Investigate why the last backup failed (check `AEGIS-DR-0001` through `0006`).
