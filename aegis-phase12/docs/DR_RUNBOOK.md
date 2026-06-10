# AEGIS Pulse — Disaster Recovery Runbook

**Version**: 1.0  
**Owner**: AEGIS Engineering  
**RPO Target**: ≤ 15 minutes  
**RTO Target**: ≤ 60 minutes  
**Last updated**: 2026-05-19

---

## Overview

This runbook covers recovery procedures for every failure mode that can affect
the AEGIS Pulse platform on a developer laptop (WSL2) running the full Docker stack.

---

## Failure Mode 1: PostgreSQL Data Corruption

### Symptoms
- Services log `asyncpg.PostgresError` or connection refused
- `docker logs aegis-postgres` shows recovery errors
- `aegis doctor` reports DB unhealthy

### Recovery

```bash
# Step 1: Stop all services
aegis down

# Step 2: Identify last healthy backup
docker run --rm -v aegis-postgres-data:/data alpine ls /data/pg_wal/

# Step 3: Restore from pgBackRest backup (if configured)
# pgBackRest incremental runs every 15 min — max data loss: 15 min
docker exec aegis-postgres pgbackrest restore --stanza=aegis --delta

# Step 4: Fallback — restore from MinIO backup
mc alias set local http://localhost:9002 aegis-dev-key aegis-dev-secret-please-change
mc ls local/aegis-backups/postgres/

# Download latest backup
mc get local/aegis-backups/postgres/latest.dump /tmp/aegis_restore.dump

# Step 5: Restore
docker exec -i aegis-postgres pg_restore \
  -U aegis_app -d aegis \
  --clean --if-exists \
  < /tmp/aegis_restore.dump

# Step 6: Verify
psql "$AEGIS_PG_DSN" -c "SELECT count(*) FROM signals;"

# Step 7: Restart services
aegis up
```

**RPO**: 15 minutes (last pgBackRest incremental)  
**RTO**: ~20 minutes

---

## Failure Mode 2: Redis Out of Memory (OOM)

### Symptoms
- `aegis analyze` hangs or returns empty results
- Redis logs show `OOM command not allowed`
- Phase 4 DrainWorker stops processing

### Recovery

```bash
# Step 1: Check Redis memory
redis-cli -u "$AEGIS_REDIS_URL" INFO memory | grep used_memory_human

# Step 2: Identify large keys
redis-cli -u "$AEGIS_REDIS_URL" --bigkeys

# Step 3: Clear non-critical caches (rate-limit buckets, signal cache)
redis-cli -u "$AEGIS_REDIS_URL" KEYS "aegis:ratelimit:*" | xargs redis-cli -u "$AEGIS_REDIS_URL" DEL
redis-cli -u "$AEGIS_REDIS_URL" KEYS "aegis:cache:*" | xargs redis-cli -u "$AEGIS_REDIS_URL" DEL

# Step 4: If critical streams are corrupted, drain and restart
redis-cli -u "$AEGIS_REDIS_URL" XLEN aegis:phase2:graph_results
# If too large: trim to last 1000 entries
redis-cli -u "$AEGIS_REDIS_URL" XTRIM aegis:phase2:graph_results MAXLEN 1000

# Step 5: Restart Redis with increased memory limit
# Edit docker-compose.yml: add "command: redis-server --maxmemory 2gb"
docker compose restart aegis-redis
```

**RPO**: 0 (Redis is a cache; source of truth is PostgreSQL)  
**RTO**: ~5 minutes

---

## Failure Mode 3: WSL2 Disk Full

### Symptoms
- Docker writes fail with "no space left on device"
- `df -h` shows 100% usage on the WSL ext4 vhdx
- Containers crash on startup

### Recovery

```bash
# Step 1: Identify large directories
du -sh ~/code/aegis-pulse/* | sort -rh | head -20
docker system df

# Step 2: Clean Docker
docker system prune -f                          # remove dangling images/containers
docker volume prune -f                          # CAUTION: removes unused volumes
docker image prune -a --filter "until=168h"    # remove images older than 1 week

# Step 3: Clean Python caches
find ~/code/aegis-pulse -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find ~/code/aegis-pulse -name "*.pyc" -delete 2>/dev/null

# Step 4: Archive old MinIO data
# Move cold data (>30 days) to Backblaze B2 (free 10GB tier)

# Step 5: Expand the WSL vhdx (Windows side)
# In PowerShell (as Admin):
# wsl --shutdown
# diskpart
#   select vdisk file="C:\Users\<user>\AppData\Local\Packages\<Ubuntu>\LocalState\ext4.vhdx"
#   expand vdisk maximum=200000  (200GB)
#   exit
# wsl
# sudo resize2fs /dev/sdb  (or whichever device WSL uses)
```

**RPO**: 0 (data in PostgreSQL is unaffected by disk pressure in isolation)  
**RTO**: ~10 minutes

---

## Failure Mode 4: Docker Daemon Dead

### Symptoms
- `docker ps` hangs or returns "Cannot connect to the Docker daemon"
- All services inaccessible
- WSL shows Docker Desktop not running

### Recovery

```bash
# Step 1: Restart Docker Desktop (Windows)
# System tray → Docker Desktop → Restart

# Step 2: If Docker Desktop won't start, reset WSL integration
# Docker Desktop → Settings → Resources → WSL Integration → Re-enable Ubuntu-24.04

# Step 3: If above fails, restart WSL
# PowerShell (Admin): wsl --shutdown && wsl

# Step 4: Verify
docker ps

# Step 5: Restart AEGIS stack
aegis up
aegis status  # verify all services healthy
```

**RPO**: 0 (data on named volumes is preserved through daemon restarts)  
**RTO**: ~5 minutes

---

## Failure Mode 5: Vault Sealed / Lost Token

### Symptoms
- All services log `AEGIS-SEC-0007` (Vault sealed) or `AEGIS-SEC-0002` (permission denied)
- `vault status` shows `Sealed: true`

### Recovery (dev mode)

```bash
# Dev mode Vault auto-unseals on restart
docker compose restart aegis-vault
# Wait for vault-init sidecar to re-run
docker logs aegis-vault-init --follow
```

### Recovery (production Raft)

```bash
# Retrieve unseal keys from secure escrow (printed + sealed envelope per DR procedure)
# Minimum 3 of 5 Shamir key shares required

vault operator unseal <key-share-1>
vault operator unseal <key-share-2>
vault operator unseal <key-share-3>

vault status  # should show Sealed: false
```

**CRITICAL**: Unseal key shares are stored:
1. Printed copy in a sealed envelope in the company safe
2. Encrypted copy in Backblaze B2 (access requires separate credentials)
3. 1Password / Bitwarden team vault (admin access only)

**RPO**: 0 (Vault data is on a named Docker volume + Raft snapshots)  
**RTO**: ~10 minutes (dev mode: ~2 minutes)

---

## Failure Mode 6: Laptop Stolen / Lost

### Symptoms
- Physical hardware unavailable
- WSL ext4 vhdx may be accessible to attacker

### Immediate actions (within 15 minutes)

1. **Revoke all Vault tokens** via the Vault UI on any other device
2. **Rotate all API keys** (Groq, OpenRouter, Gemini, Telegram, Discord) via provider dashboards
3. **Rotate PostgreSQL passwords** if accessible from outside the laptop
4. **Invalidate all JWTs** by rotating `AEGIS_SEC_JWT_SECRET`
5. **Report** to relevant authorities if sensitive data was at risk

### Recovery on new hardware

```bash
# Step 1: Set up new WSL2 environment
bash bootstrap/wsl/00_all.sh  # from cloned repo

# Step 2: Restore PostgreSQL from MinIO backup
# (follow Failure Mode 1 recovery)

# Step 3: Restore ChromaDB + model registry from restic backup
restic -r s3:s3.us-west-000.backblazeb2.com/aegis-backup restore latest \
  --target ~/code/aegis-pulse

# Step 4: Re-generate age key (old one is compromised)
age-keygen -o ~/.config/sops/age/keys.txt
# Re-encrypt all .env.sops.yaml files with the new key

# Step 5: Re-generate TLS certs
bash bootstrap/security/03_mkcert_tls.sh

# Step 6: Restart full stack
aegis up
aegis doctor
```

**RTO**: ~90 minutes on new hardware (above the 60-minute SLA — acceptable for this scenario)

---

## Failure Mode 7: Audit Log Integrity Failure

See the Security IR Runbook: `docs/IR_RUNBOOK.md → Playbook 6`.

---

## Weekly Restore Drill (Automated)

```bash
# Run this every Sunday at 02:00 via cron / GitHub Actions
scripts/security/dr_restore_drill.sh

# The drill:
# 1. Spins up a temporary Postgres container
# 2. Restores the latest backup
# 3. Runs smoke queries
# 4. Exits 0 on success, 1 on failure (triggers alerting)
# 5. Logs result to audit log
```

---

## Recovery SLA Summary

| Failure Mode | RPO | RTO | Responsible Party |
|---|---|---|---|
| PostgreSQL corruption | 15 min | 20 min | Engineering |
| Redis OOM | 0 | 5 min | Engineering |
| WSL disk full | 0 | 10 min | Engineering |
| Docker daemon dead | 0 | 5 min | Engineering |
| Vault sealed (dev) | 0 | 2 min | Engineering |
| Vault sealed (prod) | 0 | 10 min | Engineering |
| Laptop stolen | 15 min | 90 min | Engineering + Security |
| Audit log tampered | 0 | 15 min | Security |

---

## Backup Verification Checklist

Run monthly (or after any infrastructure change):

- [ ] `pgbackrest info` shows recent successful backup
- [ ] MinIO bucket `aegis-backups` has entries newer than 24h
- [ ] restic snapshots list shows recent entries
- [ ] DR restore drill (`scripts/security/dr_restore_drill.sh`) exits 0
- [ ] Vault unseal key shares are accessible and accounted for
- [ ] age private key backed up to restic
