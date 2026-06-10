# AEGIS Pulse — Disaster Recovery Runbook

## Quick Reference

| Failure Mode | RTO | RPO | Recovery Section |
|---|---|---|---|
| Postgres corruption / ransomware | 10 min | 15 min | [Scenario 1](#scenario-1-postgres-corruption) |
| Redis OOM / full restart | 2 min | 0 | [Scenario 2](#scenario-2-redis-oom--restart) |
| WSL2 disk full | 30 min | 24 h | [Scenario 3](#scenario-3-wsl2-disk-full) |
| Docker daemon crash | 5 min | 0 | [Scenario 4](#scenario-4-docker-daemon-crash) |
| Laptop stolen / hardware failure | 30 min | 15 min | [Scenario 5](#scenario-5-laptop-stolen--hardware-failure) |
| Model corruption / drift | 5 min | 1 h | [Scenario 6](#scenario-6-model-corruption--drift) |
| Upstream API ban wave | 30 min | N/A | [Scenario 7](#scenario-7-upstream-api-ban-wave) |

---

## Pre-requisites

Ensure these are set before a disaster:

```bash
# pgBackRest stanza (created once during initial setup)
pgbackrest --stanza=aegis-prod \
  --repo1-path=/var/lib/pgbackrest \
  stanza-create

# Verify backups are running
uv run aegis backup health

# List available backups
uv run aegis backup list
```

---

## Scenario 1: Postgres Corruption

**Symptoms**: `SELECT` queries fail; "relation not found"; page checksum errors;
container fails to start with "invalid page" in logs.

```bash
# 1. Identify the last clean backup.
uv run aegis backup list --system pgbackrest

# 2. Stop the database container.
docker compose stop postgres

# 3. Restore from the most recent good backup.
uv run aegis backup restore \
  --system pgbackrest \
  --backup-id 20260531T120000Z \
  --target aegis \
  --yes

# 4. Start Postgres and replay WAL to current point-in-time.
docker compose up -d postgres
sleep 10  # allow recovery to complete

# 5. Verify.
psql -U aegis_app -h localhost -p 5433 -d aegis \
  -c "SELECT COUNT(*) FROM signals;"

# 6. Run the full test suite to confirm health.
uv run python -m pytest tests/unit/ -q -p no:hypothesis

# 7. Check backup health and re-schedule if needed.
uv run aegis backup health
```

**Expected outcome**: All signals within 15 min of the failure are recovered.

---

## Scenario 2: Redis OOM / Restart

Redis is stateless for AEGIS — all durable data lives in Postgres and MinIO.
A Redis restart clears the in-memory cache and active streams.

```bash
# 1. Restart Redis.
docker compose restart redis

# 2. Verify.
redis-cli -p 6380 ping
# → PONG

# 3. Re-run any in-flight agent pipeline if needed.
uv run aegis analyze --limit 20

# 4. Stream state will auto-rebuild from Postgres on next scrape/analyze cycle.
```

**RPO = 0**: no durable data in Redis. Cache is rebuilt automatically.

---

## Scenario 3: WSL2 Disk Full

**Symptoms**: `No space left on device`; containers refuse to start;
Docker operations return I/O errors.

```bash
# --- Fast cleanup (try this first) ---

# 1. Check what is consuming space.
docker system df
df -h /

# 2. Remove stopped containers and dangling images.
docker system prune -f

# 3. Remove old images (keep those used in the last 72 h).
docker image prune -a --filter "until=72h" -f

# 4. Truncate large container logs.
find /var/lib/docker/containers -name "*.log" -exec truncate -s 0 {} \;

# 5. Verify free space.
df -h /

# --- Full restore from restic (if cleanup insufficient) ---

# On the same machine:
export RESTIC_PASSWORD="<your-password>"
restic -r <RESTIC_REPOSITORY> restore latest -t /tmp/restore-point
# Then selectively restore the files that were lost.

# WSL import from weekly snapshot (last resort):
# (Run from PowerShell on Windows host)
wsl --unregister Ubuntu-24.04
wsl --import Ubuntu-24.04 C:\WSL C:\Backups\wsl-Ubuntu-24.04-YYYYMMDD_HHMMSS.tar.gz
```

---

## Scenario 4: Docker Daemon Crash

```bash
# Restart Docker (inside WSL2).
sudo systemctl restart docker

# Verify.
docker info

# Bring the stack back up.
docker compose up -d

# Run health checks.
uv run aegis doctor
uv run aegis backup health
```

No data loss expected — volumes are persistent.

---

## Scenario 5: Laptop Stolen / Hardware Failure

Restore to a new machine.

```bash
# 1. Install WSL2 + Ubuntu-24.04 on the new machine.
#    See SETUP_FOR_NON_TECHNICAL.md for the full guide.

# 2. Install uv and Docker Desktop.
curl -LsSf https://astral.sh/uv/install.sh | sh
# → Install Docker Desktop from https://docs.docker.com/desktop/

# 3. Clone the repo.
git clone <your-remote> ~/code/aegis-pulse
cd ~/code/aegis-pulse

# 4. Restore encrypted secrets from your password manager.
#    Decrypt the age-encrypted secrets file:
# age --decrypt -i ~/.config/age/key.txt .env.sops.age > .env
#    Or re-create .env from memory / 1Password vault.

# 5. Restore filesystem from restic (optional — only needed if local files lost).
export RESTIC_PASSWORD="<your-password>"
restic -r b2:<BUCKET> restore latest -t ~

# 6. Restore Postgres from pgBackRest backup stored in MinIO.
#    First start MinIO alone:
docker compose up -d minio
sleep 5
#    Then trigger the pgBackRest restore:
uv run aegis backup restore \
  --system pgbackrest \
  --backup-id <latest-id> \
  --target aegis \
  --yes

# 7. Bring the full stack up.
uv run aegis up

# 8. Verify.
uv run aegis doctor
uv run aegis backup health
uv run python -m pytest tests/unit/ -q -p no:hypothesis

# 9. Run a daily analysis to confirm end-to-end pipeline.
uv run aegis daily
```

**Expected outcome**: Full system operational within 30 minutes.

---

## Scenario 6: Model Corruption / Drift

**Symptoms**: prediction confidence collapses; scores cluster at 0.5;
`aegis analyze` outputs `p_breakout` near 0 for every trend.

```bash
# 1. List model versions in the registry.
uv run aegis llm models --registry 2>/dev/null || \
  mc ls aegis/aegis-models/production/

# 2. Identify the last known-good version from git history.
git log --oneline -- src/aegis/predict/

# 3. Roll back the model symlink in MinIO.
mc cp aegis/aegis-models/production/model-v2-<previous>.onnx \
      aegis/aegis-models/production/model-v2-current.onnx

# 4. Restart the inference service.
docker compose restart predict

# 5. Monitor predictions for 5 minutes.
uv run aegis analyze --limit 5 --no-llm

# 6. If still diverging, retrain.
uv run aegis llm eval  # run golden-answer eval first
```

---

## Scenario 7: Upstream API Ban Wave

**Symptoms**: Multiple adapters return 403/429 simultaneously; swarm health
shows several adapters in DOWN state.

```bash
# 1. Check swarm agent health.
uv run aegis swarm agents

# 2. Identify banned adapters.
#    Banned adapters show COOLING or DOWN state.

# 3. Activate fallback RSS-only mode (no ecommerce adapters).
uv run aegis swarm run --dry-run   # confirm at least 5 healthy adapters

# 4. For FlareSolverr-dependent adapters (Flipkart, Myntra):
docker compose restart flaresolverr
# Wait 60 s for IP rotation (if behind a proxy pool).

# 5. For rate-limited adapters: reduce concurrency.
export AEGIS_SCRAPE_DEFAULT_CONCURRENCY=2
uv run aegis swarm run --limit 20

# 6. For persistent bans: rotate User-Agent pool.
#    Edit config/user_agents.yaml and restart the scrape workers.

# 7. Continue with non-banned sources only.
uv run aegis topic "your-topic" --no-llm
```

---

## Backup Operations Quick Reference

```bash
# Create incremental pgBackRest backup (manual trigger).
uv run aegis backup create --system pgbackrest --type incr

# Create full pgBackRest backup (weekly).
uv run aegis backup create --system pgbackrest --type full

# Create restic snapshot.
uv run aegis backup create --system restic --label pre-deploy

# List all backups.
uv run aegis backup list

# Prune old backups (dry-run first: inspect before running with --yes).
uv run aegis backup prune

# Check backup health (exits 1 if stale).
uv run aegis backup health

# Continuous health monitor (run in tmux/screen).
uv run aegis backup health-loop --interval 1800
```

---

## Weekly Restore Drill

Run the integration test suite to verify backup integrity:

```bash
docker compose up -d postgres redis minio
uv run python -m pytest tests/integration/test_disaster_recovery.py -v
docker compose down
```

Exit code 0 = backups are valid and restorable.

---

## Escalation

| Issue | First responder | Escalation | SLA |
|---|---|---|---|
| pgBackRest restore failed | System operator | DBA on call | 2 h |
| Model rollback needed | ML engineer | Data science lead | 30 min |
| WSL restore failed | Developer | IT / hardware vendor | 4 h |
| B2 access lost | Developer | Backblaze support | 1 h |
| Ransomware suspected | Developer | Incident commander | Immediate |

In a ransomware scenario: **isolate the machine from the network first**,
then contact the incident commander before attempting recovery.
