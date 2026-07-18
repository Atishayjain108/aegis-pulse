
# AEGIS Pulse — Operations Runbook

Last verified: **2026-06-08**  
Maintained by: engineering  
Emergency contact: check `AEGIS_ALERT_*` env vars for configured notification channels

---

## Quick Reference

| Situation | Command |
|-----------|---------|
| Start core stack | `docker compose up -d` |
| Start with logging (Loki+Promtail) | `docker compose --profile logging up -d` |
| Health check all services | `uv run aegis doctor` |
| Tail all logs | `docker compose logs -f` |
| Stop (keep data) | `docker compose down` |
| Emergency kill all alerts | `uv run --package aegis-execute aegis-execute killswitch trip --reason "emergency"` |
| Re-enable alerts | `uv run --package aegis-execute aegis-execute killswitch arm --reason "all clear"` |
| Full data wipe + restart | `docker compose down --volumes && docker compose up -d` |
| Run daily intelligence cycle | `uv run aegis daily` |

---

## Service Profiles

The stack is split into profiles so optional heavy services don't block the core stack:

| Profile | Services | How to start |
|---------|----------|--------------|
| *(default)* | postgres, redis, minio, flaresolverr, predict, execute-api, execute-drain, prometheus, grafana, jaeger | `docker compose up -d` |
| `dashboard` | aegis-dashboard (Docker) | `docker compose --profile dashboard up -d dashboard` *(see note below)* |
| `logging` | loki, promtail | `docker compose --profile logging up -d` |
| `orchestration` | prefect | `docker compose --profile orchestration up -d` |
| `tracing` | langfuse | `docker compose --profile tracing up -d` |
| `llm-proxy` | litellm | `docker compose --profile llm-proxy up -d` |
| `local-llm` | ollama, ollama-init | `docker compose --profile local-llm up -d` |

> **Dashboard — recommended mode (host):** Run `uv run aegis dashboard serve` directly on the host.
> It reads `.env` live, picks up code changes instantly, and never has stale env baked in.
> Port 8300 must be free (stop Docker dashboard first if needed: `docker compose stop dashboard`).

**Docker naming convention**: service names (used with `docker compose`) differ from container names (used with raw `docker`):
- Service `dashboard` → container `aegis-dashboard`
- Service `execute-api` → container `aegis-execute-api`
- Service `postgres` → container `aegis-postgres`
- etc.

Use `docker compose restart dashboard` (service name). Use `docker restart aegis-dashboard` (container name). Mixing these up causes "service not found" errors.

---

## 1. Service Health Checks

### 1.1 All services at once

```bash
uv run aegis doctor
# or:
docker compose ps
```

All containers should show `healthy`. Any container stuck in `starting` after 3 minutes indicates a dependency or config problem.

### 1.2 Individual service health endpoints

```bash
curl -s http://localhost:8300/healthz      # Dashboard (Command Center)
curl -s http://localhost:8200/healthz      # Phase 4 execute-api
curl -s http://localhost:8200/readyz       # Phase 4 readiness (DB + Redis check)
curl -s http://localhost:8100/healthz      # Phase 3 predict
curl -s http://localhost:9091/-/healthy    # Prometheus
curl -s http://localhost:3001/api/health   # Grafana
curl -s http://localhost:3100/ready        # Loki (logging profile only)
curl -s http://localhost:14269/            # Jaeger
```

Expected response for all: HTTP 200 or `{"status":"ok"}`.

### 1.3 Database health

```bash
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT COUNT(*) FROM signals;"
```

If this hangs: check `docker compose logs postgres` for lock contention or OOM.

### 1.4 Redis health

```bash
redis-cli -h localhost -p 6380 PING                              # → PONG
redis-cli -h localhost -p 6380 INFO memory                       # check used_memory_human
redis-cli -h localhost -p 6380 XLEN aegis:phase2:graph_results   # should be ≤ 10000
```

### 1.5 MinIO health

```bash
curl -s http://localhost:9002/minio/health/live   # → 200 OK
```

### 1.6 Daily automated verification

```bash
# Runs 7 checks: data flow, API health, DB signals, Redis stream, ruff lint
bash scripts/daily_verify.sh
```

---

## 2. Starting and Stopping

### 2.1 Normal start (core stack)

```bash
docker compose up -d
# Wait ~90 seconds for Postgres + Redis health checks before services come up.
uv run aegis doctor   # confirm all healthy
```

### 2.2 Start with optional profiles

```bash
# With log shipping (Loki + Promtail):
docker compose --profile logging up -d

# With Prefect orchestration UI:
docker compose --profile orchestration up -d

# With Langfuse LLM tracing (create DB first — see §5.9):
docker compose --profile tracing up -d

# With LiteLLM proxy:
docker compose --profile llm-proxy up -d

# Everything at once:
docker compose --profile logging --profile orchestration --profile tracing up -d
```

### 2.3 Rebuild images after code changes

```bash
docker compose up -d --build
# Rebuild a single service:
docker compose up -d --build predict
docker compose up -d --build execute-api

# Dashboard does NOT need a rebuild — run it on the host instead:
#   uv run aegis dashboard serve   (live reloads from src/ automatically)
```

### 2.4 Restart a single service

```bash
# Use SERVICE names (not container names) with docker compose:
docker compose restart execute-drain
docker compose restart predict
docker compose restart execute-api
docker compose restart postgres
docker compose restart redis
docker compose restart grafana
docker compose restart prometheus
docker compose restart jaeger
docker compose restart loki       # logging profile only
docker compose restart promtail   # logging profile only
```

### 2.5 Stop (preserve all data)

```bash
docker compose down
```

### 2.6 Stop + wipe all data (DESTRUCTIVE)

```bash
# This deletes ALL data: database rows, Redis streams, MinIO objects, model artifacts.
# There is NO undo. Always run a backup first.
docker compose down --volumes
# or:
uv run aegis down --volumes
```

### 2.7 View logs

```bash
docker compose logs -f                    # all services, follow
docker compose logs dashboard --since 5m  # one service, last 5 minutes
docker compose logs execute-api -n 100    # last 100 lines
docker compose logs postgres --since 10m | tail -50
```

---

## 3. Phase-Specific Operations

### 3.1 Scraping (Phase 0)

```bash
# Best no-API-key adapters:
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 50
uv run aegis scrape --source reddit-rss --subreddit Entrepreneur --limit 50
uv run aegis scrape --source hacker-news --limit 50
uv run aegis scrape --source github-trending --limit 30
uv run aegis scrape --source google-news --query "AI chips" --limit 30
uv run aegis scrape --source bing-news --query "market opportunity" --limit 30
uv run aegis scrape --source amazon --limit 80
uv run aegis scrape --source google-trends --limit 20

# View stored signals:
uv run aegis signals tail --limit 20
uv run aegis signals tail --platform hacker_news --limit 10
```

### 3.2 Full topic deep-dive (Phase 0 + 2)

```bash
# One keyword → expand → scrape 6+ sources → dedup → AI analysis → verdict:
uv run aegis topic "AI chips"
uv run aegis topic "bitcoin" --limit 50
uv run aegis topic "NVIDIA" --no-llm --json-out   # heuristic-only, no LLM keys needed
uv run aegis topic "e-commerce" --no-analyze       # scrape only, skip agent pipeline
```

### 3.3 Phase 2 — Agent intelligence pipeline

```bash
# Analyze most recent signals with the 10-node LangGraph DAG:
uv run aegis analyze --limit 20
uv run aegis analyze --limit 30 --platform reddit
uv run aegis analyze --no-llm              # heuristic-only (no API keys needed)
uv run aegis analyze --json-out            # machine-readable output

# Detect semantic clusters in recent signals:
uv run aegis patterns
uv run aegis patterns --limit 500 --min-cluster-size 3
uv run aegis patterns --platform hacker_news

# Deduplicate stored signals:
uv run aegis dedup                              # dry-run (safe)
uv run aegis dedup --delete                     # delete duplicates
uv run aegis dedup --lookback-hours 720 --threshold 0.90

# Daily summary report:
uv run aegis report daily
uv run aegis report daily --date 2026-06-05
```

If analysis hangs > 5 minutes: check `AEGIS_DISABLE_OLLAMA=1` is set and at least one LLM provider key is configured, or use `--no-llm`.

### 3.4 Phase 3 — Prediction server

```bash
# Health check:
curl -s http://localhost:8100/healthz

# Test prediction via REST:
curl -s -X POST http://localhost:8100/predict \
  -H "Content-Type: application/json" \
  -d '{"trend_id":"test-1","signals":[]}'

# Batch prediction:
curl -s -X POST http://localhost:8100/predict/batch \
  -H "Content-Type: application/json" \
  -d '{"items":[{"trend_id":"t1","signals":[]},{"trend_id":"t2","signals":[]}]}'

# Metrics endpoint:
curl -s http://localhost:8100/metrics | grep aegis_predict

# Restart if unresponsive:
docker compose restart predict
```

### 3.5 Phase 4 — Alert pipeline & killswitch

```bash
# Health checks:
curl -s http://localhost:8200/healthz
curl -s http://localhost:8200/readyz

# Killswitch control:
uv run --package aegis-execute aegis-execute killswitch state
uv run --package aegis-execute aegis-execute killswitch trip --reason "maintenance window"
uv run --package aegis-execute aegis-execute killswitch arm  --reason "maintenance complete"

# Tail recent alerts:
uv run --package aegis-execute aegis-execute tail \
  --tenant 00000000-0000-0000-0000-000000000001 --limit 20

# Watch live SSE stream (Ctrl+C to stop):
curl -N http://localhost:8200/stream

# Smoke test (no infrastructure needed):
uv run --package aegis-execute aegis-execute compose-demo \
  --trend-id demo-1 --verdict ENTER --score 0.82 --confidence 0.75

# Check outbox for stuck alerts:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT status, COUNT(*) FROM alert_outbox GROUP BY 1;"

# Restart drain worker if alerts are stuck:
docker compose restart execute-drain
```

### 3.6 Phase 5 — Swarm Intelligence

```bash
# Full 30-adapter swarm harvest + DB persist:
uv run aegis swarm run

# Dry-run (scrape only, no DB write):
uv run aegis swarm run --dry-run

# Limit signals per adapter:
uv run aegis swarm run --limit 20

# View swarm agent health table:
uv run aegis swarm agents

# Daily run with swarm:
uv run aegis daily --swarm

# Single swarm adapter:
uv run aegis scrape --source flipkart --limit 50
uv run aegis scrape --source nse-bse --limit 30
```

### 3.7 Phase 6 — Capital Execution Engine

```bash
# Check engine status and daily drawdown position:
curl -s http://localhost:8200/capital/status | python3 -m json.tool

# Submit a plan manually (advisory mode — no capital at risk):
curl -s -X POST http://localhost:8200/capital/plan \
  -H "Content-Type: application/json" \
  -d '{"trend_id":"test-1","score":0.80,"confidence":0.75,"sku":"DEMO-001","unit_cost":9.99}'

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
**Circuit breaker**: if daily loss ≥ `AEGIS_CAPITAL_DAILY_LOSS_LIMIT_USD`, all new plans are rejected until next UTC midnight.

### 3.8 Phase 7 — Geospatial Intelligence

```bash
# Find cross-market arbitrage opportunities (no DB / no API key needed):
uv run aegis geo analyze "TSHIRT-001" "Classic Cotton T-Shirt" --category apparel --top-n 5
uv run aegis geo analyze "PHONE-001" "Budget Smartphone" --category smartphones --json-out

# Live FX rates from ECB (no API key):
uv run aegis geo fx
uv run aegis geo fx --json-out

# WTO MFN tariff lookup:
uv run aegis geo tariff 610910 IN --value 100      # cotton T-shirt → India
uv run aegis geo tariff 851712 US --value 500      # smartphone → US (0%)
uv run aegis geo tariff 640411 EU --value 80       # sneakers → EU

# Shipping cost quote:
uv run aegis geo shipping US IN                    # US → India, 0.5 kg
uv run aegis geo shipping CN US --weight 1.0       # China → US, 1 kg

# List supported regions:
uv run aegis geo regions
```

### 3.9 Phase 8 — Compliance Engine

```bash
# Full compliance risk assessment:
uv run aegis compliance assess "SKU-001" "Classic Cotton T-Shirt" \
  --category apparel --origin CN --dest US --price 15.00

# JSON output:
uv run aegis compliance assess "SKU-001" "Louis Vuitton Inspired Bag" \
  --category luxury --origin CN --dest US --json-out

# Trademark lookup (USPTO + EUIPO live APIs):
uv run aegis compliance trademark "Nike"
uv run aegis compliance trademark "Cotton T-Shirt" --description "athletic apparel"

# OFAC / FATF sanctions check:
uv run aegis compliance sanctions IR      # Iran — BLOCKED
uv run aegis compliance sanctions NG      # Nigeria — FATF grey list
uv run aegis compliance sanctions US      # USA — clear

# FTC advertising rule engine (instant, no API call):
uv run aegis compliance ftc \
  --title "Miracle Weight Loss Supplement" \
  --description "FDA-Approved! Guaranteed to cure cancer!"

# Batch assessment from JSON file:
uv run aegis compliance batch products.json
uv run aegis compliance batch products.json --json-out
```

### 3.10 Phase 9 — Autonomous Self-Evolution

```bash
# Aggregated evolve health snapshot:
uv run aegis evolve status
uv run aegis evolve status --json-out

# Trigger manual model retraining:
uv run aegis evolve retrain
uv run aegis evolve retrain --json-out

# Run drift check on recent prediction outcomes:
uv run aegis evolve drift
uv run aegis evolve drift --json-out

# Show current RL pricing policy weights:
uv run aegis evolve policy
uv run aegis evolve policy --json-out

# Count recent trade outcomes:
uv run aegis evolve outcomes
uv run aegis evolve outcomes --days 7

# Record a trade outcome from the CLI:
uv run aegis evolve record \
  --plan-id "plan-001" --trend-id "trend-abc" \
  --score 0.82 --confidence 0.91 \
  --roi 45.0 --pnl 450.0 --units 10 \
  --status successful
```

### 3.11 Phase 10 — Data Lake

```bash
# Health check:
uv run aegis datalake doctor --json-out

# Apply catalog schema migrations (run once after install):
uv run aegis datalake migrate

# Ingest Phase 1 signals → Bronze (last 7 days):
uv run aegis datalake ingest-postgres-signals \
  --dsn postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis

# Ingest Phase 3 predictions → Bronze:
uv run aegis datalake ingest-postgres-predictions \
  --dsn postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis

# Ingest Phase 4 alerts → Bronze:
uv run aegis datalake ingest-postgres-alerts \
  --dsn postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis

# Drain Phase 2 Redis stream → Bronze:
uv run aegis datalake ingest-redis

# Build Silver + Gold for today:
uv run aegis datalake build-silver --date today
uv run aegis datalake build-gold --date today

# Full daily run (Bronze ingest → Silver → Gold):
uv run aegis datalake daily \
  --dsn postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis

# Query the lake with DuckDB:
uv run aegis datalake query "SELECT platform, COUNT(*) FROM signals GROUP BY 1 ORDER BY 2 DESC"

# List registered tables:
uv run aegis datalake list-tables
uv run aegis datalake list-tables --layer bronze

# Retention management (dry-run by default):
uv run aegis datalake retention silver
uv run aegis datalake retention silver --apply
uv run aegis datalake retention bronze --apply

# Local filesystem mode (for testing, no MinIO needed):
uv run aegis datalake doctor --local-root /tmp/aegis-lake --json-out
```

### 3.12 Phase 11 — LLM Gateway

```bash
# Provider health check (latency + circuit state for all configured providers):
uv run aegis llm health
uv run aegis llm health --json-out

# One-shot completion (uses gateway fallback chain):
uv run aegis llm complete "Summarise the latest AI chip news in 3 bullets"
uv run aegis llm complete "..." --provider groq --model llama-3.3-70b-versatile

# Embedding:
uv run aegis llm embed "text to embed"

# List all registered models:
uv run aegis llm models
uv run aegis llm models --provider ollama

# Pull Ollama model:
uv run aegis llm pull llama3.2:3b
uv run aegis llm pull bge-m3

# Cost summary (estimated USD spend):
uv run aegis llm cost

# Nightly eval against golden-answer fixtures:
uv run aegis llm eval
```

**All providers failing**: check `docker compose logs ollama`, then verify `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY` in `.env`. Use `--no-llm` as immediate fallback.

### 3.13 Phase 14 — Observability

Requires the `logging` profile to be active.

```bash
# Start the logging stack first:
docker compose --profile logging up -d

# Verify Prometheus is scraping AEGIS metrics:
curl -s 'http://localhost:9091/api/v1/query?query=aegis_obs_ingest_signals_total' \
  | python3 -m json.tool | grep value

# Check Loki is receiving logs:
curl -s 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="docker"}' \
  --data-urlencode 'limit=1'

# Check Promtail is shipping logs:
docker compose logs promtail --since 5m | grep -i error

# Restart log stack if logs not appearing in Grafana:
docker compose restart loki
sleep 10
docker compose restart promtail
```

**OTel traces not appearing in Jaeger**: verify `OTEL_ENABLED=true` in the service env, and that `OTEL_EXPORTER_OTLP_ENDPOINT=http://aegis-jaeger:4317` (container name) inside Docker.

### 3.14 Phase 15 — Disaster Recovery

```bash
# SLA status (RPO 15 min / RTO 60 min targets):
cd aegis-phase15 && uv run aegis dr status

# Backup all targets:
cd aegis-phase15 && uv run aegis dr backup

# Backup specific target:
cd aegis-phase15 && uv run aegis dr backup --target postgres
cd aegis-phase15 && uv run aegis dr backup --target redis
cd aegis-phase15 && uv run aegis dr backup --target models
cd aegis-phase15 && uv run aegis dr backup --target restic

# Validate backup integrity (dry-run — SAFE, no data touched):
cd aegis-phase15 && uv run aegis dr drill --dry-run

# Full restore drill (creates aegis_drill DB — NOT prod):
cd aegis-phase15 && uv run aegis dr drill

# Monitor backup health continuously:
cd aegis-phase15 && uv run aegis dr health --watch

# Print failure-mode runbook:
cd aegis-phase15 && uv run aegis dr runbook pg_corruption
cd aegis-phase15 && uv run aegis dr runbook redis_oom
cd aegis-phase15 && uv run aegis dr runbook disk_full
# Available: pg_corruption, redis_oom, disk_full, docker_dead, laptop_stolen, network_outage, wsl_crash
```

### 3.15 One daily command (runs everything)

```bash
uv run aegis daily
# or with swarm:
uv run aegis daily --swarm
```

---

## 4. Stack Lifecycle

```bash
uv run aegis up               # start all services (detached)
uv run aegis up --build       # rebuild Docker images first
uv run aegis down             # stop (data preserved)
uv run aegis down --volumes   # stop + WIPE all data (irreversible)
uv run aegis reset            # same as down --volumes (asks for confirmation)
uv run aegis tail             # follow logs for all services
uv run aegis tail predict     # follow logs for one service
uv run aegis doctor           # health check all components
uv run aegis status           # show Docker service status
uv run aegis support-bundle   # collect diagnostic zip for debugging
```

---

## 5. Failure Scenarios

### 5.1 Docker image pull fails (TLS timeout, WSL2)

**Symptoms**: `docker compose up` fails with `TLS handshake timeout` on Loki/Promtail pull.

```bash
# These are optional services — start the core stack without them:
docker compose up -d
# Then start logging profile separately when network is stable:
docker compose --profile logging up -d

# To manually pre-pull problematic images and retry:
docker pull grafana/loki:3.1.0
docker pull grafana/promtail:3.1.0
docker compose --profile logging up -d

# WSL2 DNS fix if Docker Hub is intermittently unreachable:
sudo sh -c 'echo "nameserver 8.8.8.8\nnameserver 8.8.4.4" > /etc/resolv.conf'
sudo service docker restart
```

### 5.2 Postgres unresponsive / corrupted

**Symptoms**: services show `unhealthy`; `psql` hangs or returns `Connection refused`.

```bash
# Check what's happening:
docker compose logs postgres --since 10m | tail -50

# If OOM or lock contention, restart:
docker compose restart postgres
# Wait 30s, then verify:
curl -s http://localhost:8200/readyz

# If data is corrupted, restore from backup:
cd aegis-phase15
uv run aegis dr runbook pg_corruption   # read the procedure first
uv run aegis dr restore --target postgres --dry-run
# If dry-run looks correct:
uv run aegis dr restore --target postgres
```

### 5.3 Redis OOM / all data lost

**Symptoms**: `XREADGROUP` errors; agent pipeline hangs; Phase 4 `IntakeWorker` logs `connection refused`.

```bash
docker compose logs redis --since 10m | tail -30

# Check memory usage:
redis-cli -h localhost -p 6380 INFO memory

# Restart Redis (stream state recoverable — Phase 2 republishes on next analyze run):
docker compose restart redis

# If RDB corrupt:
cd aegis-phase15
uv run aegis dr runbook redis_oom
uv run aegis dr restore --target redis --dry-run
```

### 5.4 MinIO object store unreachable

**Symptoms**: Phase 10 lake writes fail; Phase 12 WORM audit uploads fail.

```bash
docker compose logs minio --since 10m
curl -s http://localhost:9002/minio/health/live

# Restart MinIO:
docker compose restart minio

# If data volumes corrupted (very rare):
docker exec aegis-minio mc ls local/   # list buckets before stopping
```

### 5.5 Dashboard not loading

**Symptoms**: `http://localhost:8300` returns "connection refused" or blank page.

**Recommended: run on host (no Docker rebuild, live code changes):**
```bash
# Ensure Docker dashboard is stopped first (frees port 8300):
docker compose stop dashboard

# Start host-mode dashboard (reads .env directly):
uv run aegis dashboard serve
# → http://127.0.0.1:8300
```

**If using Docker dashboard mode (`--profile dashboard`):**
```bash
docker compose logs dashboard --since 5m | tail -30

# Common causes:
# a) Postgres or Redis not yet healthy → wait or restart deps
# b) Port 8300 already in use by host-mode dashboard → kill it first
#    lsof -i :8300   then   kill <pid>
# c) Stale AEGIS_ENV in container → force-recreate:
docker compose --profile dashboard up -d --force-recreate dashboard
```

> Note: `AEGIS_ENV` is now hardcoded to `dev` in docker-compose.yml so the
> `"development"` ValidationError cannot recur on fresh containers.

### 5.6 Alerts not being delivered

**Symptoms**: ENTER verdicts in stream, no Discord/ntfy/Telegram messages.

```bash
# Check if killswitch is tripped:
uv run --package aegis-execute aegis-execute killswitch state

# Check outbox for stuck rows:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT id, status, attempt_count, last_error FROM alert_outbox ORDER BY created_at DESC LIMIT 10;"

# Restart the drain worker:
docker compose restart execute-drain

# Verify notifier config:
grep -E "DISCORD|NTFY|TELEGRAM" .env
```

**Stuck outbox rows**: if `alert_outbox` has rows in `claimed` state for > 10 minutes, the drain worker crashed. Restart: `docker compose restart execute-drain`.

### 5.7 LLM gateway — all providers failing

**Symptoms**: `aegis analyze` runs but all verdicts are heuristic-only; `aegis llm health` shows all providers DOWN.

```bash
uv run aegis llm health --json-out

# Verify at least one provider key is set:
grep -E "GROQ_API_KEY|OPENROUTER_API_KEY|GEMINI_API_KEY" .env

# Test Ollama (if running):
curl -s http://localhost:11434/api/tags | python3 -m json.tool

# Check circuit breaker state:
docker compose logs dashboard --since 5m | grep "circuit"

# Fallback (always works, no API keys needed):
uv run aegis analyze --limit 20 --no-llm
```

### 5.8 Loki / Promtail not collecting logs

**Symptoms**: Grafana Explore → Loki shows "No data".

```bash
# Ensure logging profile is running:
docker compose --profile logging ps

# Check Promtail can reach Loki:
docker compose logs promtail --since 5m

# Check Loki is healthy:
curl -s http://localhost:3100/ready
docker compose logs loki --since 5m | grep -v "^level=info"

# Verify docker.sock mount (needed for Promtail container discovery):
docker compose config | grep -A3 promtail | grep sock

# Restart log stack:
docker compose restart loki
sleep 10
docker compose restart promtail
```

**Note**: Loki service is named `loki` (not `aegis-loki`) in docker-compose. Container name is `aegis-loki`.

### 5.9 Capital execution circuit breaker tripped

**Symptoms**: `/capital/plan` returns `{"status":"rejected","reason":"daily_loss_limit_exceeded"}`.

```bash
# Check circuit breaker state:
curl -s http://localhost:8200/capital/status | python3 -m json.tool | grep -E "mode|daily_loss|circuit"

# View today's PnL:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT SUM(realised_pnl_usd) FROM settlements WHERE settle_date = CURRENT_DATE;"

# Circuit breaker auto-resets at next UTC midnight.
# Emergency override (advisory mode disables all capital risk):
# Edit .env: AEGIS_EXECUTE_MODE=advisory
docker compose restart execute-api
```

### 5.10 Disk full

**Symptoms**: Postgres write errors; MinIO returns 500; Docker logs stop.

```bash
df -h ~   # WSL2

# Free space:
uv run aegis datalake retention bronze --apply
uv run aegis datalake retention silver --apply
docker system prune -f

# Check MinIO bucket sizes:
docker exec aegis-minio mc du local/

# Full runbook:
cd aegis-phase15 && uv run aegis dr runbook disk_full
```

---

## 6. Maintenance Procedures

### 6.1 Apply database migrations

```bash
# Check current migration version:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1;" 2>/dev/null || \
  echo "schema_version table not present — migrations apply at Postgres init time"

# Migrations run automatically at Postgres container first start
# (via /docker-entrypoint-initdb.d/migrations/).

# Datalake catalog migrations (SQLite, run after install):
uv run aegis datalake migrate
```

### 6.2 Adding a new LLM provider key

```bash
# 1. Add to .env:
echo "GROQ_API_KEY=gsk_..." >> .env

# 2. Restart services that use the gateway:
docker compose restart dashboard execute-api

# 3. Verify:
uv run aegis llm health
```

### 6.3 Rotating secrets

```bash
# Manually update .env and restart affected services:
docker compose restart execute-api execute-drain dashboard
```

### 6.4 Clearing Redis streams when overfull

```bash
# Check stream lengths:
redis-cli -h localhost -p 6380 XLEN aegis:phase2:graph_results
redis-cli -h localhost -p 6380 XLEN aegis:swarm:results

# Trim to last 1000 entries (safe — Phase 4 IntakeWorker uses XREADGROUP with ACK):
redis-cli -h localhost -p 6380 XTRIM aegis:phase2:graph_results MAXLEN 1000
redis-cli -h localhost -p 6380 XTRIM aegis:swarm:results MAXLEN 1000
```

### 6.5 Triggering a manual DR backup

```bash
cd aegis-phase15

# Backup everything:
uv run aegis dr backup

# Verify the backup appeared in MinIO:
docker exec aegis-minio mc ls local/aegis-dr/ --recursive | grep "$(date +%Y-%m-%d)"
```

### 6.6 Weekly restore drill

```bash
# Normally automated by DrOrchestrator. To run manually:
cd aegis-phase15

# Create the drill database (only needed once):
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/postgres \
  -c "CREATE DATABASE aegis_drill OWNER aegis_app;" 2>/dev/null || echo "already exists"

# Dry-run (validates manifest + checksum, no actual restore):
uv run aegis dr drill --dry-run

# Full drill (restores to aegis_drill, validates row counts):
uv run aegis dr drill
```

### 6.7 Scale Ollama context / model limits

```bash
# Edit docker-compose.yml:
# OLLAMA_NUM_CTX: "8192"          → increase for longer conversations
# OLLAMA_MAX_LOADED_MODELS: "1"   → increase if switching models frequently

docker compose up -d ollama   # apply new env
```

### 6.8 Pull a new Ollama model

```bash
# Via aegis CLI:
uv run aegis llm pull qwen2.5:14b

# Or direct API call:
curl -X POST http://localhost:11434/api/pull \
  -H "Content-Type: application/json" \
  -d '{"name":"qwen2.5:14b"}'

# Re-run the one-shot init if models were wiped:
docker compose run --rm ollama-init
```

### 6.9 Setting up Langfuse (LLM tracing)

```bash
# 1. Create the langfuse database (Postgres must be running):
#    On a fresh deploy the postgres init script handles this automatically.
#    For an already-running instance:
docker exec aegis-postgres psql -U aegis_app -d aegis \
  -c "CREATE DATABASE langfuse OWNER aegis_app;"

# 2. Start Langfuse:
docker compose --profile tracing up -d langfuse

# 3. Open http://localhost:3002 and sign in
#    Default org/project: aegis / aegis-pulse
#    API keys: pk-lf-aegis-dev / sk-lf-aegis-dev

# 4. Configure AEGIS to send traces:
#    Add to .env:
#    LANGFUSE_HOST=http://localhost:3002
#    LANGFUSE_PUBLIC_KEY=pk-lf-aegis-dev
#    LANGFUSE_SECRET_KEY=sk-lf-aegis-dev
```

### 6.10 Run the test suite

```bash
# Phases 0-14 unit tests (quick, no infra):
uv run python -m pytest tests/unit/ -q -p no:hypothesis

# With coverage report (floor: 78%):
uv run python -m pytest tests/unit/ -p no:hypothesis

# Phase 3 integration tests (requires running postgres + redis):
uv run python -m pytest tests/integration/predict/ -v -p no:hypothesis

# Phase 4 unit tests:
uv run python -m pytest aegis-phase4/tests/ -q -p no:hypothesis

# Phase 5 Hardening unit tests (run separately):
uv run --package aegis-harden python -m pytest aegis-harden/tests/ -q -p no:hypothesis

# Phase 15 DR unit tests (standalone module):
cd aegis-phase15 && uv sync --extra dev && .venv/bin/python -m pytest tests/ -q -p no:hypothesis

# Ruff linting (zero violations expected):
uv run ruff check src/ aegis-phase4/src/ tests/
```

---

## 7. Observability Runbook

### 7.1 Key metrics to watch (Grafana Mission Control — http://localhost:3001)

| Metric | Normal range | Alert threshold |
|--------|-------------|----------------|
| `aegis_obs_ingest_signals_total` (rate) | 5–50/min during scrapes | 0 for > 30 min |
| `aegis_obs_agent_task_duration_ms` (p99) | < 5000ms | > 30000ms |
| `aegis_obs_llm_cost_usd` (cumulative) | < $1/day (dev) | > $5/day |
| `aegis_obs_alert_delivered_total` (rate) | 1–20/hour | 0 for > 2 hours after analyze |
| `aegis_obs_model_inference_latency_ms` (p99) | < 12ms (heuristic) | > 500ms |
| `aegis_obs_database_query_duration_ms` (p99) | < 100ms | > 1000ms |
| `aegis_obs_cache_hit_ratio` | > 0.7 | < 0.3 |

### 7.2 Log queries (Grafana Explore → Loki → LogQL)

```logql
# All errors across all services:
{container_name=~"aegis-.*"} |= "error" | json | level = "error"

# Agent pipeline errors:
{container_name="aegis-dashboard"} |= "aegis.agents" | json | level = "error"

# Phase 4 alert failures:
{container_name="aegis-execute-drain"} | json | level = "error"

# LLM provider failures:
{container_name=~"aegis-.*"} |= "AllProvidersFailed" | json

# Killswitch events:
{container_name="aegis-execute-api"} |= "killswitch" | json

# Phase 15 backup failures:
{container_name=~"aegis-.*"} |= "AEGIS-DR-" | json
```

### 7.3 Tracing a slow request (Jaeger — http://localhost:16687)

1. Open `http://localhost:16687`.
2. Select Service: `aegis-dashboard` (or `aegis-predict`, `aegis-execute-api`).
3. Set time range → "Find Traces".
4. Click a slow trace to see the span waterfall.
5. The `trend_id` and `correlation_id` span attributes link a Jaeger trace to a structlog log line.

### 7.4 Prometheus queries (http://localhost:9091)

```promql
# Signal ingest rate over last 5 minutes:
rate(aegis_obs_ingest_signals_total[5m])

# Agent pipeline p99 latency:
histogram_quantile(0.99, rate(aegis_obs_agent_task_duration_ms_bucket[5m]))

# LLM cumulative cost today:
aegis_obs_llm_cost_usd

# DB query p99:
histogram_quantile(0.99, rate(aegis_obs_database_query_duration_ms_bucket[5m]))
```

---

## 8. Environment Variables Quick Reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `AEGIS_ENV` | `dev` | Must be `dev`/`staging`/`prod`/`test` — NOT `development` |
| `AEGIS_PG_DSN` | `postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis` | Primary Postgres DSN |
| `AEGIS_REDIS_URL` | `redis://localhost:6380/0` | Redis connection URL (port **6380** on host) |
| `AEGIS_DISABLE_OLLAMA` | `0` | Set to `1` to skip Ollama in tests/CI |
| `OTEL_ENABLED` | `true` | Set to `false` to disable OTel tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Jaeger OTLP gRPC endpoint |
| `SENTRY_DSN` | (empty) | Sentry project DSN; empty = Sentry disabled |
| `AEGIS_EXECUTE_MODE` | `advisory` | `advisory`/`staging`/`live` — `live` only in production |
| `AEGIS_EXECUTE_HMAC_KEY` | `dev-hmac-key-not-for-prod` | Must match between execute-api and execute-drain |
| `AEGIS_DEFAULT_TENANT_ID` | `00000000-0000-0000-0000-000000000001` | Default tenant UUID |
| `AEGIS_EXECUTE_VYAPAR_WEBHOOK_URL` | (empty) | B2B Vyapar webhook (empty = disabled) |
| `AEGIS_CAPITAL_MAX_RISK_USD` | `500.0` | Maximum single-plan capital exposure |
| `AEGIS_CAPITAL_DAILY_LOSS_LIMIT_USD` | `200.0` | Daily drawdown circuit-breaker threshold |
| `AEGIS_CAPITAL_KELLY_FRACTION` | `0.25` | Fractional-Kelly position sizing multiplier |
| `AEGIS_TELEGRAM_BOT_TOKEN` | (empty) | Telegram bot token for P0/P1 approval workflow |
| `AEGIS_TELEGRAM_APPROVAL_CHAT_ID` | (empty) | Chat/group ID for approval notifications |
| `AEGIS_PRINTFUL_API_KEY` | (empty) | Printful POD fulfillment (empty = degraded) |
| `AEGIS_CJ_API_KEY` | (empty) | CJ Dropshipping API key (empty = degraded) |
| `AEGIS_SHOPIFY_SHOP_DOMAIN` | (empty) | Shopify store domain |
| `AEGIS_SHOPIFY_ACCESS_TOKEN` | (empty) | Shopify Admin API access token |
| `AEGIS_SEC_VAULT_TOKEN` | `dev-root-token` | HashiCorp Vault auth token |
| `AEGIS_BACKUP_PGBACKREST_STANZA` | `aegis-prod` | pgBackRest stanza name |
| `AEGIS_BACKUP_RESTIC_REPOSITORY` | `local:/var/lib/restic` | restic repo URL |
| `AEGIS_BACKUP_RESTIC_PASSWORD` | (empty) | restic encryption password (must set before init) |
| `AEGIS_DR_PG_DSN` | (matches AEGIS_PG_DSN) | DR module Postgres DSN |
| `AEGIS_DR_DRILL_PG_DSN` | `...@localhost:5433/aegis_drill` | Must differ from prod DSN |
| `AEGIS_DR_RPO_TARGET_S` | `900` | RPO target in seconds (15 min) |
| `AEGIS_DR_RTO_TARGET_S` | `3600` | RTO target in seconds (60 min) |
| `GROQ_API_KEY` | (empty) | Groq LLM provider key |
| `OPENROUTER_API_KEY` | (empty) | OpenRouter LLM provider key |
| `GEMINI_API_KEY` | (empty) | Gemini LLM provider key |
| `DOCKER_GID` | `1001` | Docker group GID for dashboard docker.sock access |
| `LANGFUSE_HOST` | (empty) | Langfuse server URL (set if using tracing profile) |

---

## 9. Web UIs

| URL | Service | Notes |
|-----|---------|-------|
| http://localhost:8300 | AEGIS Command Center (dashboard) | Main UI: signals, agents, ops console |
| http://localhost:8200/dashboard/ | Phase 4 Execute dashboard | Alerts, killswitch, SSE |
| http://localhost:3001 | Grafana | AEGIS Mission Control (admin / aegis_dev_admin_pw) |
| http://localhost:9091 | Prometheus | Raw metrics |
| http://localhost:16687 | Jaeger | Distributed traces (OTel) |
| http://localhost:3100 | Loki | Log API (query via Grafana; logging profile) |
| http://localhost:9003 | MinIO console | Object store (aegis-dev-key / aegis-dev-secret-please-change) |
| http://localhost:11434 | Ollama | Local LLM runtime |
| http://localhost:4200 | Prefect | Orchestration UI (orchestration profile) |
| http://localhost:3002 | Langfuse | LLM tracing (tracing profile) |

---

## 10. Alert Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| P0 — Capital circuit | Daily loss > `AEGIS_CAPITAL_DAILY_LOSS_LIMIT_USD` | Check `/capital/status`, switch to `advisory` mode |
| P0 — Data loss | Postgres down > 15 min AND last backup > RPO | `aegis dr runbook pg_corruption` |
| P0 — Silent | No alerts generated for > 2 hours when signals exist | Check killswitch; `docker compose restart execute-drain` |
| P1 — Degraded | All LLM providers down (heuristic-only mode) | Check provider keys; restart gateway services |
| P1 — Lake stale | No Bronze ingest for > 24 hours | `uv run aegis datalake ingest-postgres-signals --dsn ...` |
| P2 — Observability | Loki not receiving logs for > 30 min | `docker compose restart loki && docker compose restart promtail` |
| P2 — Backup stale | Last backup > `HEALTH_STALE_BACKUP_CRIT_S` (3600s) | `cd aegis-phase15 && uv run aegis dr backup --target postgres` |
| P3 — Performance | p99 agent latency > 30s | Check for blocking I/O; `docker compose restart dashboard` |
