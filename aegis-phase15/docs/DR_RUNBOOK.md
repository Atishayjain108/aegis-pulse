# AEGIS Pulse — Disaster Recovery Runbook

**Phase**: 15  
**Version**: 0.15.0  
**Last verified**: 2026-05-27  
**SLA**: RPO ≤ 15 min | RTO ≤ 60 min  

---

## Contents

1. [Quick Reference](#1-quick-reference)
2. [Architecture Overview](#2-architecture-overview)
3. [Failure Mode: pg_corruption](#3-pg-corruption)
4. [Failure Mode: redis_oom](#4-redis-oom)
5. [Failure Mode: disk_full](#5-disk-full)
6. [Failure Mode: docker_dead](#6-docker-dead)
7. [Failure Mode: laptop_stolen](#7-laptop-stolen)
8. [Failure Mode: network_outage](#8-network-outage)
9. [Failure Mode: wsl_crash](#9-wsl-crash)
10. [Restore Drill Procedure](#10-restore-drill-procedure)
11. [Secrets Escrow (Vault Shamir Unseal)](#11-secrets-escrow)
12. [Post-Incident Checklist](#12-post-incident-checklist)

---

## 1. Quick Reference

```bash
# Check DR status right now
uv run aegis dr status

# Run a dry-run restore drill
uv run aegis dr drill --dry-run

# Run a live backup of all targets
uv run aegis dr backup --target all

# Run a specific runbook in dry-run (safe)
uv run aegis dr runbook pg_corruption --dry-run

# Run a live runbook (CAUTION — modifies system)
uv run aegis dr runbook pg_corruption --no-dry-run
```

---

## 2. Architecture Overview

```
┌────────────────────────────────────────────────────────────┐
│                   AEGIS DR Layer (Phase 15)                │
│                                                            │
│  DrOrchestrator                                            │
│    ├─ PostgresBackup  ──────────────► MinIO aegis-dr/      │
│    │    interval: 15 min              └─ postgres/         │
│    │                                                       │
│    ├─ RedisBackup     ──────────────► MinIO aegis-dr/      │
│    │    interval: 5 min               └─ redis/            │
│    │                                                       │
│    ├─ ModelRegistryBackup ──────────► MinIO aegis-dr/      │
│    │    interval: 1 h                 └─ models/           │
│    │                                                       │
│    ├─ ResticBackup    ──────────────► MinIO restic repo    │
│    │    interval: 24 h                                     │
│    │                                                       │
│    ├─ DrHealthChecker ──────────────► Redis aegis:dr:*     │
│    │    interval: 60 s                Dashboard widget     │
│    │                                                       │
│    └─ RestoreDrill    ──────────────► MinIO drills/        │
│         interval: 7 days              Redis drill:latest   │
│                                                            │
│  FastAPI Router /dr/* ──────────────► Dashboard :8300      │
│  CLI: aegis dr [backup|restore|drill|status|runbook]       │
└────────────────────────────────────────────────────────────┘
```

**Backup targets and retention:**

| Target | Engine | Interval | Hot Retention |
|--------|--------|----------|---------------|
| PostgreSQL / TimescaleDB | pg_dump -Fc | 15 min | 7 days |
| Redis | BGSAVE + RDB upload | 5 min | 7 days |
| ML model registry | Filesystem scan + upload | 1 h | 5 versions |
| Full repo + ChromaDB | restic (optional) | 24 h | 7 daily |

---

## 3. PG Corruption

**Symptoms:**
- `invalid page in block` in Postgres logs
- `asyncpg.PostgresError` in Phase 2 agent logs
- `aegis analyze` fails with DB errors
- Dashboard shows red DB health widget

**Automated recovery (< 5 min):**

```bash
# 1. Run the automated runbook (dry-run to preview)
uv run aegis dr runbook pg_corruption --dry-run

# 2. If dry-run looks correct, execute
uv run aegis dr runbook pg_corruption --no-dry-run
```

**Manual recovery steps (if automation fails):**

```bash
# 1. Stop the Postgres container
docker stop aegis-postgres

# 2. Back up the corrupted data directory (for forensics)
docker cp aegis-postgres:/var/lib/postgresql/data /tmp/pg_corrupt_$(date +%Y%m%d_%H%M%S)

# 3. Create a fresh container with clean data volume
docker compose rm -f aegis-postgres
docker volume rm aegis-pulse_postgres_data
docker compose up -d aegis-postgres

# 4. Wait for Postgres to be ready
until docker exec aegis-postgres pg_isready -U aegis_app; do sleep 1; done

# 5. Run migrations
uv run aegis db migrate

# 6. Restore from latest MinIO backup
uv run aegis dr restore --target postgres --no-dry-run

# 7. Verify
uv run aegis signals tail --limit 5
```

**Expected RTO:** 30–45 minutes  
**Error code if automated restore fails:** `AEGIS-DR-0010`

---

## 4. Redis OOM

**Symptoms:**
- `OOM command not allowed` in Redis logs
- Phase 2 agent pipeline stalls (inter-agent streams fill)
- `aegis analyze` hangs

**Automated recovery:**

```bash
uv run aegis dr runbook redis_oom --no-dry-run
```

**Manual recovery steps:**

```bash
# 1. Connect to Redis
redis-cli -p 6380

# 2. Check memory usage
INFO memory

# 3. Free expired keys
MEMORY PURGE

# 4. Identify large keys
MEMORY USAGE <key>

# 5. If still OOM, flush non-critical data (DB 1 = test data)
SELECT 1
FLUSHDB ASYNC

# 6. Increase maxmemory (temporary — fix .wslconfig for permanent)
CONFIG SET maxmemory 2gb

# 7. Restart if still OOM
docker restart aegis-redis
```

**Root cause prevention:**  
Add `memory=16GB` (or higher) to `~/.wslconfig` and restart WSL.

**Expected RTO:** 2–5 minutes  

---

## 5. Disk Full

**Symptoms:**
- `IOError: No space left on device` anywhere in logs
- Docker containers crash on write
- MinIO writes fail

**Automated recovery:**

```bash
uv run aegis dr runbook disk_full --no-dry-run
```

**Manual recovery steps:**

```bash
# 1. Check disk usage
df -h /

# 2. Find biggest consumers
du -sh ~/.aegis/* | sort -rh | head -20
docker system df

# 3. Prune Docker (removes stopped containers, unused images, volumes)
docker system prune -f --volumes

# 4. Remove old MinIO DR backups (keeps last 7 days)
# This is automated by the retention policy:
uv run aegis datalake retention bronze --apply

# 5. If WSL disk is full, expand it (Windows PowerShell as Admin):
# wsl --manage Ubuntu-24.04 --set-sparse true
# Then in WSL:
# sudo resize2fs /dev/sdb

# 6. Monitor with watch
watch -n 5 df -h /
```

**Expected RTO:** 10–20 minutes (disk expansion: up to 30 min)

---

## 6. Docker Dead

**Symptoms:**
- `Cannot connect to Docker daemon`
- `docker ps` times out
- All containers unreachable
- Dashboard returns 503

**Automated recovery:**

```bash
uv run aegis dr runbook docker_dead --no-dry-run
```

**Manual recovery steps:**

```bash
# WSL side — restart Docker service
sudo service docker restart

# If that fails — restart Docker Desktop from Windows tray
# Right-click whale icon → Restart

# If Docker Desktop won't restart — reboot Windows
# After reboot:
cd ~/code/aegis-pulse
uv run aegis up

# Verify
uv run aegis doctor
```

**Data safety:** All data is in named Docker volumes — they survive container/daemon restarts.

**Expected RTO:** 5–15 minutes

---

## 7. Laptop Stolen

**⚠️ CRITICAL — Act within 15 minutes of discovery.**

```bash
# STEP 1: Rotate all secrets IMMEDIATELY (from another device)
# Log into your cloud provider console and:
# - Rotate MinIO access keys
# - Rotate all API keys in .env
# - Invalidate any OAuth tokens

# STEP 2: If using Vault
vault operator unseal  # use Shamir shares from escrow (see Section 11)
vault token revoke -mode=all

# STEP 3: Revoke GitHub SSH key
# github.com → Settings → SSH Keys → Delete stolen key's key

# STEP 4: Notify API providers
# - Groq: groq.com/settings → API Keys → Revoke
# - OpenRouter: openrouter.ai/settings → API Keys → Revoke
# - Telegram bot: /revoke command with BotFather

# STEP 5: Restore to new machine
# Follow SETUP_FOR_NON_TECHNICAL.md on the replacement machine
# Restore data from MinIO DR backup:
uv run aegis dr restore --target postgres --no-dry-run
```

**No automated runbook** for this scenario — human action required at each step.  
**Expected RTO:** 2–4 hours (hardware procurement may extend this)

---

## 8. Network Outage

**Symptoms:**
- Scrape adapters returning connection errors
- LLM providers unreachable (Phase 11 falls back to Ollama, then heuristic)
- MinIO backup uploads failing

**AEGIS behaviour during outage:**
- Phase 0 scrapers: fail gracefully, retry with backoff, log `scrape.error`
- Phase 2 agents: continue with cached signals and heuristic-only mode
- Phase 3 predictions: continue — all heuristic, zero API calls
- Phase 4 alerts: queue in Redis, drain when connectivity restores
- Phase 11 LLM: falls back to local Ollama; if offline, heuristic verdicts

**No action required unless outage > 1 hour.**  
After restoration:

```bash
# Resume scraping
uv run aegis daily

# Drain any backed-up alert outbox
uv run --package aegis-execute aegis-execute drain
```

---

## 9. WSL Crash

**Symptoms:**
- WSL session terminates unexpectedly
- Running processes killed
- Clock drift > 10 s on resume

**Recovery:**

```bash
# In Windows PowerShell:
wsl --shutdown
wsl -d Ubuntu-24.04

# Re-sync clock (WSL clock drifts during host sleep)
sudo hwclock -s

# Restart AEGIS services
cd ~/code/aegis-pulse
uv run aegis up

# Check all healthy
uv run aegis doctor
```

**Data safety:**  
All persistent data lives in Docker named volumes (Postgres WAL, Redis AOF, MinIO).  
WSL crashes do not corrupt Docker volumes unless `--volumes` was used.

---

## 10. Restore Drill Procedure

Run weekly. Takes ~10 minutes. Proves backup → restore works before you need it.

```bash
# Dry-run (safe — no DB changes)
uv run aegis dr drill --dry-run

# Full live drill (restores into aegis_drill DB)
uv run aegis dr drill --no-dry-run
```

**What the drill tests:**
1. Finds the most recent postgres and redis backup manifests in MinIO.
2. Downloads and verifies checksums.
3. For `--no-dry-run`: restores postgres dump into `aegis_drill` database.
4. Runs row-count verification against the manifest.
5. Measures actual RPO (age of backup used) and RTO (wall-clock restore time).
6. Writes result to MinIO `drills/<drill_id>.json`.
7. Publishes to Redis `aegis:dr:drill:latest` for the dashboard.
8. Exits non-zero if drill fails → CI job breaks.

**Expected output (passing):**

```
▶ Running restore drill (dry-run) …
  ✓ Drill outcome: PASS
     RTO actual: 12.3s  (target ≤ 3600s, met=True)
     RPO actual: 420s   (target ≤ 900s, met=True)
     ✓ postgres: status=success, verified=True, duration=11.8s
     ✓ redis: status=success, verified=True, duration=0.4s
```

---

## 11. Secrets Escrow

**Vault Shamir unseal (if using HashiCorp Vault):**

1. The Vault is sealed after a restart — it will not serve secrets until unsealed.
2. Generate Shamir key shares at init time:
   ```bash
   vault operator init -key-shares=3 -key-threshold=2
   ```
3. **Print the 3 unseal keys and the root token.** Seal them in physical envelopes.
4. To unseal (run twice with two different shares):
   ```bash
   vault operator unseal <share-1>
   vault operator unseal <share-2>
   ```
5. Store unseal shares in physically separate locations (not all on the laptop).

**Without Vault:**  
Rotate the `.env` SOPS-encrypted file. The SOPS age private key is stored separately from the repo. Back it up to a password manager (Bitwarden/1Password free tier).

---

## 12. Post-Incident Checklist

After any production incident:

- [ ] Root cause identified and documented in `docs/adr/NNNN-<incident>.md`
- [ ] All affected secrets rotated
- [ ] Data integrity verified (row counts, model checksums)
- [ ] Backup schedule confirmed healthy via `aegis dr status`
- [ ] Monitoring alerts reviewed and tuned if they missed the incident
- [ ] Drill run to verify restored state is functional
- [ ] Runbook updated if the automated recovery was insufficient
- [ ] `docs/COSTS.md` updated if incident required paid resource usage
- [ ] Postmortem sent to yourself (lessons learned, timeline, impact)

---

*Generated by AEGIS Pulse Phase 15 — DR Runbook Generator.*  
*Do not edit this file manually — update the source and regenerate.*
