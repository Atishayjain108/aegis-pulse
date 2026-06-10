# ADR-0015: Disaster Recovery & Business Continuity

## Status

Accepted — 2026-05-31

## Context

AEGIS Pulse runs on a developer laptop with production-grade uptime requirements
for its training data and model artefacts.  A single disk failure or ransomware
attack could destroy months of scraped signals, trained models, and ChromaDB
semantic embeddings with no recovery path.

The system also spans multiple Docker containers, a WSL2 filesystem, MinIO object
storage, Redis streams, and an external scrape pipeline.  Any one of these can
fail independently, requiring different recovery procedures.

### Failure modes addressed

| Mode | Risk | Without DR |
|------|------|------------|
| Postgres corruption / ransomware | Medium | All signals, predictions, alerts lost |
| Redis OOM / restart | High | In-flight agent results lost (recoverable from Postgres) |
| WSL2 disk full | Medium | All containers stop; data intact but inaccessible |
| Docker daemon crash | Medium | Services unavailable; data intact |
| Laptop stolen / hardware failure | Low | Total loss without off-site backup |
| Upstream API ban wave | Medium | Scrape pipeline halted; historical data safe |

## Decision

### 1. Triple-layered backup strategy

```
Layer 1: pgBackRest (Postgres WAL archiving + incremental backups)
         → every 15 min to local MinIO; weekly full + daily diff + 15-min incr
         → RPO = 15 min, RTO = 10 min

Layer 2: restic (encrypted filesystem snapshots)
         → daily to local repo; synced to Backblaze B2 when RESTIC_B2_BUCKET set
         → RPO = 24 h, RTO = 20 min

Layer 3: WSL2 export (full distro snapshot)
         → weekly via bootstrap/wsl/backup_wsl_disk.sh
         → RPO = 7 days, RTO = 30 min (re-import + start stack)
```

### 2. Model registry versioning

Every trained model is stored with SHA-256 checksum in the MinIO model registry.
The last five production model versions are always retained and can be promoted
via `aegis llm models --registry`.  ONNX INT8 format is the disaster-recovery
artefact — it runs on any CPU without GPU drivers.

### 3. Data lake immutability

MinIO object lock is configured on the `aegis-backups` bucket with a 90-day
WORM (Write Once Read Many) retention.  This prevents ransomware from deleting
backups even if the MinIO admin credentials are compromised.  Merkle-tree
verification is applied at the Gold layer via the existing `DuckDB` integrity
checks.

### 4. RTO / RPO targets

| Component | RPO | RTO | Recovery mechanism |
|-----------|-----|-----|-------------------|
| Postgres | 15 min | 10 min | pgBackRest restore + WAL replay |
| MinIO | 0 | 1 min | Persistent volume survives container restart |
| Redis | 0 | 2 min | Docker restart; state rebuilt from Postgres |
| Models | 1 h | 5 min | Roll back to previous registry version |
| WSL filesystem | 24 h | 20 min | restic restore from B2 |
| WSL image | 7 days | 30 min | `wsl --import` from weekly export |

### 5. Weekly automated restore drill

`tests/integration/test_disaster_recovery.py` contains tests marked
`@pytest.mark.integration` that exercise the full backup → corrupt → restore
cycle against a live Postgres instance.  These run in CI nightly and are the
authoritative gate for DR readiness.

### 6. Implementation boundaries

- `src/aegis/backup/` — Python wrapper modules (pgBackRest, restic, health)
- `src/aegis/backup/cli.py` — `aegis backup` Click command group
- `bootstrap/wsl/backup_wsl_disk.sh` — WSL2 export script (PowerShell-free)
- `docs/DR_RUNBOOK.md` — step-by-step operator runbook

## Trade-offs

| + Benefit | - Cost |
|-----------|--------|
| RPO ≤ 15 min for Postgres | +500 GB storage over 90 days |
| Encrypted at rest (restic AES-256) | RESTIC_PASSWORD must be stored securely |
| Automated nightly drill | CI time (+5 min per nightly run) |
| Zero-copy restore via delta | Requires pgBackRest binary in Docker image |
| Off-site B2 backup | Requires B2 credentials + rclone install |

## Alternatives rejected

- **pg_dump only**: RPO = time since last manual dump; no continuous WAL capture.
  Rejected because 15-min RPO cannot be met.
- **Docker volume snapshots only**: No cross-machine restore without the exact
  Docker host.  Rejected for the laptop-stolen scenario.
- **MinIO replication only**: Covers object data but not Postgres WAL.  Kept as a
  complementary layer, not a primary DR mechanism.
