# AEGIS Pulse — Operations Runbook

Last verified: **2026-06-01**  
Maintained by: engineering  
Emergency contact: check `AEGIS_ALERT_*` env vars for configured notification channels

---

## Quick Reference

| Situation | Command |
|-----------|---------|
| Start all services | `docker compose up -d` |
| Health check all services | `uv run aegis doctor` |
| Tail all logs | `docker compose logs -f` |
| Stop (keep data) | `docker compose down` |
| Emergency kill all alerts | `uv run --package aegis-execute aegis-execute killswitch trip --reason "emergency"` |
| Re-enable alerts | `uv run --package aegis-execute aegis-execute killswitch arm --reason "all clear"` |
| Full data wipe + restart | `docker compose down --volumes && docker compose up -d` |

---

## 1. Service Health Checks

### 1.1 All services at once

```bash
uv run aegis doctor
# Or manually:
docker compose ps
```

All containers should show `healthy`. Any container stuck in `starting` after 3 minutes indicates a dependency or config problem.

### 1.2 Individual service health endpoints

```bash
curl -s http://localhost:8300/health      # Dashboard (Command Center)
curl -s http://localhost:8200/healthz     # Phase 4 execute-api
curl -s http://localhost:8200/readyz      # Phase 4 readiness (includes DB + Redis check)
curl -s http://localhost:8100/healthz     # Phase 3 predict
curl -s http://localhost:3100/ready       # Loki (Phase 14)
```

Expected response for all: `{"status":"ok"}` or `200 OK`.

### 1.3 Database health

```bash
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT COUNT(*) FROM signals;"
```

If this hangs: check `docker compose logs postgres` for lock contention or OOM.

### 1.4 Redis health

```bash
redis-cli -h localhost -p 6380 PING          # → PONG
redis-cli -h localhost -p 6380 INFO memory   # check used_memory_human
redis-cli -h localhost -p 6380 XLEN aegis:phase2:graph_results  # should be ≤ 10000
```

### 1.5 MinIO health

```bash
curl -s http://localhost:9002/minio/health/live   # → 200 OK
```

---

## 2. Starting and Stopping

### 2.1 Normal start

```bash
docker compose up -d
# Wait ~90 seconds for Postgres + Redis health checks before services come up.
uv run aegis doctor  # confirm all healthy
```

### 2.2 Rebuild images after code changes

```bash
docker compose up -d --build
```

### 2.3 Restart a single service

```bash
docker compose restart aegis-dashboard
docker compose restart aegis-execute-drain
docker compose restart aegis-predict
```

### 2.4 Stop (preserve all data)

```bash
docker compose down
```

### 2.5 Stop + wipe all data (DESTRUCTIVE)

```bash
# This deletes ALL data: database rows, Redis streams, MinIO objects, model artifacts.
# There is NO undo. Always run a backup first.
uv run aegis down --volumes
# or: docker compose down --volumes
```

---

## 3. Phase-Specific Operations

### 3.1 Phase 2 — Running the agent pipeline

```bash
# Analyze 20 most recent trends (no LLM keys needed):
uv run aegis analyze --limit 20 --no-llm

# Full LLM-enhanced analysis:
uv run aegis analyze --limit 20

# Topic deep-dive (scrape + analyze in one):
uv run aegis topic "AI chips"
uv run aegis topic "NVIDIA" --no-llm --json-out
```

If analysis hangs for > 5 minutes: check `AEGIS_DISABLE_OLLAMA=1` is set and that at least one LLM provider (`GROQ_API_KEY` / `OPENROUTER_API_KEY`) is configured, or use `--no-llm`.

### 3.2 Phase 3 — Prediction server

```bash
# Health check:
curl -s http://localhost:8100/healthz

# Test a prediction via the REST API:
curl -s -X POST http://localhost:8100/predict \
  -H "Content-Type: application/json" \
  -d '{"trend_id":"test-1","signals":[]}'

# Restart if unresponsive:
docker compose restart aegis-predict
```

### 3.3 Phase 4 — Alert pipeline

```bash
# Check killswitch state:
uv run --package aegis-execute aegis-execute killswitch state

# Trip the killswitch (halts all outbound notifications):
uv run --package aegis-execute aegis-execute killswitch trip --reason "maintenance window"

# Re-arm the killswitch:
uv run --package aegis-execute aegis-execute killswitch arm --reason "maintenance complete"

# Tail recent alerts:
uv run --package aegis-execute aegis-execute tail \
  --tenant 00000000-0000-0000-0000-000000000001 --limit 20

# Watch live SSE stream (Ctrl+C to stop):
curl -N http://localhost:8200/stream

# Check outbox drain state (pending / stuck alerts):
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT status, COUNT(*) FROM alert_outbox GROUP BY 1;"
```

**Stuck outbox rows**: if `alert_outbox` has rows in `claimed` state for > 10 minutes, the drain worker crashed. Restart: `docker compose restart aegis-execute-drain`.

### 3.4 Phase 10 — Data lake

```bash
# Daily full ingest + build:
uv run aegis datalake daily \
  --dsn postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis

# Check lake health:
uv run aegis datalake doctor --json-out

# Run a query:
uv run aegis datalake query "SELECT platform, COUNT(*) FROM signals GROUP BY 1 ORDER BY 2 DESC"

# Apply retention policy (dry-run first):
uv run aegis datalake retention silver
uv run aegis datalake retention silver --apply
```

### 3.5 Phase 11 — LLM gateway

```bash
# Check provider health + circuit-breaker state:
uv run aegis llm health
uv run aegis llm health --json-out

# Test a completion:
uv run aegis llm complete "What is the current state of AI chip supply chains?"

# Check cost usage:
uv run aegis llm cost

# Pull a fresh Ollama model (if Ollama is running):
uv run aegis llm pull llama3.2:3b
```

**All providers failing**: check `docker compose logs ollama` (local), then verify `GROQ_API_KEY`, `OPENROUTER_API_KEY`, and `GEMINI_API_KEY` are set in `.env`. Use `--no-llm` flag as fallback.

### 3.6 Phase 14 — Observability

```bash
# Verify Prometheus is scraping AEGIS metrics:
curl -s 'http://localhost:9091/api/v1/query?query=aegis_obs_ingest_signals_total' \
  | python3 -m json.tool | grep value

# Check Loki is receiving logs:
curl -s 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="docker"}' \
  --data-urlencode 'limit=1'

# Check Promtail is healthy:
docker compose logs promtail --since 5m | grep -i error

# Restart Loki stack if logs are not appearing in Grafana:
docker compose restart promtail loki
```

**OTel traces not appearing in Jaeger**: verify `OTEL_ENABLED=true` in the service's env. The OTLP gRPC receiver in Jaeger listens on port 4317 (internal to the Docker network); the service must reference `http://aegis-jaeger:4317` (container name) or set `OTEL_EXPORTER_OTLP_ENDPOINT` appropriately.

### 3.8 Phase 6 — Capital Execution Engine

```bash
# Check execution engine status and daily drawdown position:
curl -s http://localhost:8200/capital/status | python3 -m json.tool

# Submit a plan manually (advisory mode — no capital at risk):
curl -s -X POST http://localhost:8200/capital/plan \
  -H "Content-Type: application/json" \
  -d '{"trend_id":"test-1","score":0.80,"confidence":0.75,"sku":"DEMO-001","unit_cost":9.99}'

# Check pending Telegram approvals:
curl -s http://localhost:8200/capital/status | python3 -m json.tool | grep -A5 "pending_approvals"

# Trigger EOD settlement manually:
curl -s -X POST http://localhost:8200/capital/settle | python3 -m json.tool

# Query execution plans from DB:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT trend_id, status, quantity, total_capital_usd, created_at FROM execution_plans ORDER BY created_at DESC LIMIT 10;"

# Query daily settlement PnL:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT settle_date, realised_pnl_usd, plan_count FROM settlements ORDER BY settle_date DESC LIMIT 7;"
```

**Modes**: `advisory` (default, no capital), `staging` (paper trades), `live` (real orders — set `AEGIS_EXECUTE_MODE=live` only in production).  
**Circuit breaker**: if daily loss ≥ `AEGIS_CAPITAL_DAILY_LOSS_LIMIT_USD`, all new plans are rejected until the next UTC day.  
**Approval flow**: P0 plans (score ≥ 0.85) require Telegram approval before fulfillment. If `AEGIS_TELEGRAM_BOT_TOKEN` is not set, plans fall back to auto-approve in advisory mode.

### 3.7 Phase 15 — Disaster Recovery

```bash
# SLA status check:
cd aegis-phase15 && uv run aegis dr status

# Run a backup now (all targets):
cd aegis-phase15 && uv run aegis dr backup

# Backup specific target:
cd aegis-phase15 && uv run aegis dr backup --target postgres
cd aegis-phase15 && uv run aegis dr backup --target redis
cd aegis-phase15 && uv run aegis dr backup --target models

# Validate backup integrity (dry-run drill — SAFE, no data touched):
cd aegis-phase15 && uv run aegis dr drill --dry-run

# Full restore drill (creates aegis_drill DB — not prod):
cd aegis-phase15 && uv run aegis dr drill

# Monitor backup health continuously:
cd aegis-phase15 && uv run aegis dr health --watch
```

---

## 4. Failure Scenarios

### 4.1 Postgres unresponsive / corrupted

**Symptoms**: services show `unhealthy`; `psql` hangs or returns `Connection refused`.

```bash
# Step 1: check what's happening:
docker compose logs postgres --since 10m | tail -50

# Step 2: if OOM or lock contention, restart:
docker compose restart postgres
# Wait 30s, then:
curl -s http://localhost:8200/readyz   # should return {"status":"ok"}

# Step 3: if data is corrupted, restore from backup:
cd aegis-phase15
uv run aegis dr runbook pg_corruption   # read the procedure
# Then follow the runbook — restore to aegis_drill first, validate, then swap
uv run aegis dr restore --target postgres --dry-run
# If dry-run output looks correct:
uv run aegis dr restore --target postgres
```

### 4.2 Redis OOM / all data lost

**Symptoms**: `XREADGROUP` returns errors; agent pipeline hangs; Phase 4 `IntakeWorker` logs `connection refused`.

```bash
docker compose logs redis --since 10m | tail -30

# Check memory usage:
redis-cli -h localhost -p 6380 INFO memory

# If OOM: Redis may need a restart:
docker compose restart redis

# Stream state is recoverable — Phase 2 will republish on next analyze run.
# If Redis RDB is corrupt:
cd aegis-phase15
uv run aegis dr runbook redis_oom
uv run aegis dr restore --target redis --dry-run
```

### 4.3 MinIO object store unreachable

**Symptoms**: Phase 10 data lake writes fail; Phase 12 WORM audit uploads fail; model artifact saves fail.

```bash
docker compose logs minio --since 10m
curl -s http://localhost:9002/minio/health/live

# Restart MinIO:
docker compose restart minio

# If data volumes are corrupted (very rare):
# 1. Export any critical data before stopping:
docker exec aegis-minio mc ls local/  # list buckets
# 2. Check RUNBOOK for full MinIO recovery procedure
```

### 4.4 Dashboard not loading

**Symptoms**: `http://localhost:8300` returns 502 or blank page.

```bash
docker compose logs aegis-dashboard --since 5m | tail -30

# Common causes:
# a) Postgres or Redis not yet healthy → wait or restart deps
# b) Port conflict → check "lsof -i :8300"
# c) Config error → check AEGIS_ENV is "dev" (not "development")

docker compose restart aegis-dashboard
```

### 4.5 Alerts not being delivered

**Symptoms**: Phase 4 shows ENTER verdicts in the stream, but no Discord/ntfy/Telegram messages arrive.

```bash
# Check if killswitch is tripped:
uv run --package aegis-execute aegis-execute killswitch state

# Check outbox for stuck rows:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT id, status, attempt_count, last_error FROM alert_outbox ORDER BY created_at DESC LIMIT 10;"

# Restart the drain worker:
docker compose restart aegis-execute-drain

# Verify notifier config:
grep -E "DISCORD|NTFY|TELEGRAM" .env
```

### 4.6 LLM gateway — all providers failing

**Symptoms**: `aegis analyze` runs but all verdicts are heuristic-only; `aegis llm health` shows all providers DOWN.

```bash
uv run aegis llm health --json-out

# Check circuit breaker state in logs:
docker compose logs aegis-dashboard --since 5m | grep "circuit"

# Fallback procedure:
# 1. Verify at least one provider key is set:
grep -E "GROQ_API_KEY|OPENROUTER_API_KEY|GEMINI_API_KEY" .env

# 2. Test Ollama (if running):
curl -s http://localhost:11434/api/tags | python3 -m json.tool

# 3. Use heuristic-only mode as fallback (fully functional):
uv run aegis analyze --limit 20 --no-llm
```

### 4.7 Loki / Promtail not collecting logs

**Symptoms**: Grafana Explore → Loki shows "No data" or empty results.

```bash
# Check Promtail can reach Loki:
docker compose logs promtail --since 5m

# Check Loki is healthy:
curl -s http://localhost:3100/ready
docker compose logs loki --since 5m | grep -v "^level=info"

# Verify the docker.sock mount (needed for Promtail to discover containers):
docker compose config | grep -A3 promtail | grep sock

# Restart the log stack:
docker compose restart loki
sleep 10
docker compose restart promtail
```

Note: Loki service is named `loki` in docker-compose, not `aegis-loki`. Container name is `aegis-loki`.

### 4.9 Capital execution circuit breaker tripped

**Symptoms**: `/capital/plan` returns `{"status":"rejected","reason":"daily_loss_limit_exceeded"}` for all new plans; settlement shows large negative PnL.

```bash
# Check circuit breaker state:
curl -s http://localhost:8200/capital/status | python3 -m json.tool | grep -E "mode|daily_loss|circuit"

# View today's PnL:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT SUM(realised_pnl_usd) FROM settlements WHERE settle_date = CURRENT_DATE;"

# The circuit breaker auto-resets at the next UTC midnight.
# If you need to manually inspect failed plans:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT trend_id, status, total_capital_usd, fulfillment_errors FROM execution_plans WHERE created_at > NOW() - INTERVAL '24h' ORDER BY created_at DESC;"

# To override in an emergency (advisory mode disables all capital risk):
# Edit .env: AEGIS_EXECUTE_MODE=advisory
# Then: docker compose restart aegis-execute-api
```

### 4.8 Disk full

**Symptoms**: Postgres write errors; MinIO returns 500; Docker logs stop.

```bash
df -h /var/lib/docker  # WSL2: df -h ~

# Free space by removing old Parquet files from lake:
uv run aegis datalake retention bronze
uv run aegis datalake retention bronze --apply

# Remove old Docker artifacts:
docker system prune -f

# Check MinIO bucket sizes:
docker exec aegis-minio mc du local/

# Follow the full runbook:
cd aegis-phase15 && uv run aegis dr runbook disk_full
```

---

## 5. Maintenance Procedures

### 5.1 Adding a new LLM provider key

1. Add the key to `.env`: `GROQ_API_KEY=gsk_...`
2. Restart services that use the gateway:
   ```bash
   docker compose restart aegis-dashboard aegis-execute-api
   ```
3. Verify: `uv run aegis llm health`

### 5.2 Applying database migrations

```bash
# Check current migration version:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1;"

# Apply migrations manually:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -f db/migrations/0006_b2b_supply_chain.sql
  -f db/migrations/0007_capital_execution.sql

# Datalake catalog migrations (separate SQLite catalog):
uv run aegis datalake migrate
```

### 5.3 Rotating secrets

```bash
# Using Phase 12 secret rotation manager:
python3 -c "
import asyncio
from aegis.security import VaultClient
# ... rotation logic via aegis.security.SecretRotationManager
"

# Or manually update .env and restart affected services:
docker compose restart aegis-execute-api aegis-execute-drain aegis-dashboard
```

### 5.4 Clearing Redis streams when overfull

```bash
# Check stream lengths:
redis-cli -h localhost -p 6380 XLEN aegis:phase2:graph_results
redis-cli -h localhost -p 6380 XLEN aegis:swarm:results

# Trim to last 1000 entries (safe — Phase 4 IntakeWorker uses XREADGROUP with ACK):
redis-cli -h localhost -p 6380 XTRIM aegis:phase2:graph_results MAXLEN 1000
```

### 5.5 Triggering a manual DR backup

```bash
cd aegis-phase15

# Backup everything:
uv run aegis dr backup

# Verify the backup appeared in MinIO:
docker exec aegis-minio mc ls local/aegis-dr/ --recursive | grep "$(date +%Y-%m-%d)"
```

### 5.6 Verifying backup integrity (weekly drill)

```bash
# This is normally automated (DrOrchestrator schedules it weekly).
# To run manually:
cd aegis-phase15

# First: ensure drill database exists:
psql postgresql://postgres:postgres@localhost:5433/postgres \
  -c "CREATE DATABASE aegis_drill;" 2>/dev/null || echo "already exists"

# Dry-run (validates manifest + checksum, no actual restore):
uv run aegis dr drill --dry-run

# Full drill (restores to aegis_drill, validates row counts):
uv run aegis dr drill
```

### 5.7 Scaling Ollama context / model limits

Ollama is capped by default (8K context, 1 model). To adjust:

```bash
# Edit docker-compose.yml:
# OLLAMA_NUM_CTX: "8192"          → increase for longer conversations
# OLLAMA_MAX_LOADED_MODELS: "1"   → increase if switching models frequently

docker compose up -d ollama  # applies new env
```

---

## 6. Observability Runbook

### 6.1 Key metrics to watch (Grafana Mission Control)

| Metric | Normal range | Alert threshold |
|--------|-------------|----------------|
| `aegis_obs_ingest_signals_total` (rate) | 5–50/min during scrapes | 0 for > 30 min |
| `aegis_obs_agent_task_duration_ms` (p99) | < 5000ms | > 30000ms |
| `aegis_obs_llm_cost_usd` (cumulative) | < $1/day (dev) | > $5/day |
| `aegis_obs_alert_delivered_total` (rate) | 1–20/hour | 0 for > 2 hours after analyze |
| `aegis_obs_model_inference_latency_ms` (p99) | < 12ms (heuristic) | > 500ms |
| `aegis_obs_database_query_duration_ms` (p99) | < 100ms | > 1000ms |
| `aegis_obs_cache_hit_ratio` | > 0.7 | < 0.3 |

### 6.2 Log queries (Grafana Explore → Loki → LogQL)

```logql
# All errors across all services:
{container_name=~"aegis-.*"} |= "error" | json | level = "error"

# Agent pipeline errors:
{container_name="aegis-dashboard"} |= "aegis.agents" | json | level = "error"

# Phase 4 alert failures:
{container_name="aegis-execute-drain"} | json | level = "error"

# LLM provider failures:
{container_name=~"aegis-.*"} |= "AllProvidersFailed" | json

# Phase 15 backup failures:
{container_name=~"aegis-.*"} |= "AEGIS-DR-" | json

# Killswitch events:
{container_name="aegis-execute-api"} |= "killswitch" | json
```

### 6.3 Tracing a slow request (Jaeger)

1. Open `http://localhost:16687`.
2. Select Service: `aegis-dashboard` (or `aegis-predict`, `aegis-execute-api`).
3. Set time range and click "Find Traces".
4. Click a slow trace to see the span waterfall — each span shows which service + operation was slow.
5. The `trend_id` and `correlation_id` span attributes link a Jaeger trace to a structlog log line.

---

## 7. Environment Variables Quick Reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `AEGIS_ENV` | `dev` | Must be `dev`/`staging`/`prod`/`test` (not `development`) |
| `AEGIS_PG_DSN` | (see .env.example) | Primary Postgres DSN |
| `AEGIS_REDIS_URL` | `redis://localhost:6380/0` | Redis connection URL |
| `AEGIS_DISABLE_OLLAMA` | `0` | Set to `1` to skip Ollama in tests/CI |
| `OTEL_ENABLED` | `true` | Set to `false` to disable OTel tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Jaeger OTLP gRPC endpoint |
| `SENTRY_DSN` | (empty) | Sentry project DSN; empty = Sentry disabled |
| `SENTRY_ENV` | (mirrors AEGIS_ENV) | Sentry environment tag |
| `AEGIS_BACKUP_PGBACKREST_STANZA` | `aegis-prod` | pgBackRest stanza name |
| `AEGIS_BACKUP_RESTIC_REPOSITORY` | `local:/var/lib/restic` | restic repo URL |
| `AEGIS_BACKUP_RESTIC_PASSWORD` | (empty) | restic encryption password (set before init) |
| `AEGIS_DR_PG_DSN` | (matches AEGIS_PG_DSN) | DR module Postgres DSN |
| `AEGIS_DR_DRILL_PG_DSN` | `...@localhost:5433/aegis_drill` | Must differ from prod DSN |
| `AEGIS_DR_RPO_TARGET_S` | `900` | RPO target in seconds (15 min) |
| `AEGIS_DR_RTO_TARGET_S` | `3600` | RTO target in seconds (60 min) |
| `AEGIS_DR_NTFY_TOPIC` | (empty) | ntfy topic for DR failure alerts |
| `AEGIS_EXECUTE_MODE` | `advisory` | `advisory` / `staging` / `live` — set `live` only in production |
| `AEGIS_EXECUTE_VYAPAR_WEBHOOK_URL` | (empty) | B2B Vyapar webhook (empty = disabled) |
| `AEGIS_CAPITAL_MAX_RISK_USD` | `500.0` | Maximum single-plan capital exposure in USD |
| `AEGIS_CAPITAL_DAILY_LOSS_LIMIT_USD` | `200.0` | Daily drawdown circuit-breaker threshold in USD |
| `AEGIS_CAPITAL_KELLY_FRACTION` | `0.25` | Fractional-Kelly multiplier for position sizing |
| `AEGIS_TELEGRAM_BOT_TOKEN` | (empty) | Telegram bot token for P0/P1 approval workflow |
| `AEGIS_TELEGRAM_APPROVAL_CHAT_ID` | (empty) | Chat/group ID for approval notifications |
| `AEGIS_PRINTFUL_API_KEY` | (empty) | Printful POD fulfillment (empty = degraded, returns []) |
| `AEGIS_CJ_API_KEY` | (empty) | CJ Dropshipping API key (empty = degraded) |
| `AEGIS_SHOPIFY_SHOP_DOMAIN` | (empty) | Shopify store domain for draft-order fulfillment |
| `AEGIS_SHOPIFY_ACCESS_TOKEN` | (empty) | Shopify Admin API access token |
| `AEGIS_SEC_VAULT_TOKEN` | `dev-root-token` | HashiCorp Vault auth token |
| `GROQ_API_KEY` | (empty) | Groq LLM provider key |
| `OPENROUTER_API_KEY` | (empty) | OpenRouter LLM provider key |
| `GEMINI_API_KEY` | (empty) | Gemini LLM provider key |

---

## 8. Alert Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| P0 — Capital circuit | Daily loss > `AEGIS_CAPITAL_DAILY_LOSS_LIMIT_USD` | Check `/capital/status`, switch to `advisory` mode, investigate fulfillment errors |
| P0 — Data loss | Postgres down > 15 min AND last backup > RPO | `aegis dr runbook pg_corruption` |
| P0 — Silent | No alerts generated for > 2 hours when signals exist | Check killswitch state, restart execute-drain |
| P1 — Degraded | All LLM providers down (heuristic-only mode) | Check provider keys, restart gateway services |
| P1 — Lake stale | No Bronze ingest for > 24 hours | Run `aegis datalake ingest-postgres-signals` |
| P2 — Observability | Loki not receiving logs for > 30 min | Restart promtail, check docker.sock mount |
| P2 — Backup stale | Last backup > `HEALTH_STALE_BACKUP_CRIT_S` (3600s) | `aegis dr backup --target postgres` |
| P3 — Performance | p99 agent latency > 30s | Check for blocking I/O, restart dashboard |
