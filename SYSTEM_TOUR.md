# AEGIS Pulse — Complete Architect's Tour

---

## 1. The Codebase Map (What are these files?)

Think of the project as a **factory with multiple specialist departments**. Each department hands its output to the next; the Capital Execution engine (Phase 6) acts on the highest-confidence verdicts; the Dashboard watches all of them simultaneously; the Observability layer instruments everything; and the DR system keeps a continuous backup ready.

```
aegis-pulse/
├── src/aegis/                    ← The main brain (Phases 0–5 + 10–15 + Dashboard)
│   ├── __init__.py               ← Stitches workspace member code into the same namespace
│   ├── config.py                 ← One place for all settings (ports, keys, env vars)
│   ├── cli/main.py               ← Your control panel — every `aegis` terminal command lives here
│   │
│   ├── scrape/sources/           ← PHASE 0: The data collectors
│   │   ├── reddit_rss.py         ← Grabs posts from Reddit's RSS feed
│   │   ├── hacker_news.py        ← Grabs trending HN stories via Algolia API
│   │   ├── github_trending.py    ← Scrapes GitHub's trending repos page
│   │   ├── amazon.py             ← Grabs Amazon bestseller rankings
│   │   ├── google_news_rss.py    ← Google News RSS feed (supports --query)
│   │   ├── bing_news_rss.py      ← Bing News RSS feed (supports --query)
│   │   └── (+ 29 swarm adapters) ← Flipkart, NSE, Reuters, etc. (wave 1–4 scrape targets)
│   │
│   ├── db/                       ← PHASE 1: Saves everything to the database
│   ├── cache/                    ← PHASE 1: Fast Redis layer (dedup, shared memory)
│   │
│   ├── agents/                   ← PHASE 2: The 10-agent intelligence council
│   │   ├── graph.py              ← Draws the wiring diagram connecting all 10 agents
│   │   ├── runner.py             ← The "start the council" function; ships results to Phase 4
│   │   ├── nodes/scout.py        ← Agent 1: Is this trend worth investigating?
│   │   ├── nodes/sentinel.py     ← Agent 2: Risk watchdog — is it about to collapse?
│   │   ├── nodes/compliance.py   ← Agent 3: Legal/policy checker
│   │   ├── nodes/red_team.py     ← Agent 4: Devil's advocate
│   │   ├── nodes/hedge.py        ← Agent 5: Portfolio-level final veto
│   │   └── nodes/(5 others)      ← GeoArbitrage, Narrative, Historian, Sourcer, Auditor
│   │
│   ├── predict/                  ← PHASE 3: The ML prediction engine
│   │   ├── inference/runner.py   ← The single entry point — features in, predictions out
│   │   ├── features/builder.py   ← Converts raw DB signals into 20-feature numeric vectors
│   │   ├── models/               ← Heuristic model (always works) + optional neural models
│   │   └── serving/              ← FastAPI server on port 8100 (/predict, /healthz)
│   │
│   ├── agents_phase3_glue/
│   │   └── bridge.py             ← Translator: ML output → Phase 2 agent language
│   │
│   ├── datalake/                 ← PHASE 10: Bronze/Silver/Gold data lake
│   │   ├── bronze/               ← Raw ingest from Postgres, Redis streams
│   │   ├── silver/               ← Cleaned + conformed Parquet per UTC date
│   │   ├── gold/                 ← Business aggregates (platform stats, verdicts)
│   │   └── query/                ← DuckDB SQL engine over Parquet files
│   │
│   ├── llm/                      ← PHASE 11: Local LLM Orchestration
│   │   ├── gateway/              ← LLMGateway with circuit breaker + fallback chain
│   │   ├── providers/            ← Ollama → Groq → OpenRouter → Gemini → Anthropic
│   │   ├── guardrails/           ← PII scrubbing + toxic-pattern blocking
│   │   └── bridge/agents_bridge  ← Single call for agent nodes: complete_for_agent()
│   │
│   ├── observability/            ← PHASE 14: Full observability stack
│   │   ├── tracing.py            ← OpenTelemetry → Jaeger (gracefully no-ops if OTel absent)
│   │   ├── metrics.py            ← 24 Prometheus metrics for every phase (aegis_obs_* prefix)
│   │   └── sentry_init.py        ← Sentry error tracking (reads SENTRY_DSN, no-op if unset)
│   │
│   ├── backup/                   ← PHASE 15: Integrated backup subsystem
│   │   ├── pgbackrest_manager.py ← pgBackRest incremental Postgres backups
│   │   ├── restic_manager.py     ← restic encrypted filesystem snapshots
│   │   └── health.py             ← Staleness monitor + failure alerting
│   │
│   └── dashboard/                ← DASHBOARD: Unified Command Center (port 8300)
│       ├── app.py                ← FastAPI backend — aggregates all phases
│       └── static/index.html     ← Dark-theme SPA: charts, SSE live feed, ops console
│
├── aegis-phase4/                 ← PHASES 4 + 6: Alert factory + Capital Execution (uv workspace member)
│   └── src/aegis/execute/
│       ├── pipeline.py           ← 7-stage alert assembly line
│       ├── workers/              ← IntakeWorker reads Redis; DrainWorker sends notifications
│       ├── killswitch/           ← Emergency stop — one Redis key halts all outbound alerts
│       ├── notifiers/            ← Discord / ntfy / Telegram senders
│       ├── api/                  ← FastAPI server on port 8200 (/stream, /capital/*, /healthz)
│       ├── engine.py             ← PHASE 6: ExecutionEngine — 3-tier Kelly-sized dispatch
│       ├── pricing.py            ← PHASE 6: PricingStrategy — A/B test + RL online weight updates
│       ├── approval.py           ← PHASE 6: ApprovalBroker — Telegram inline-button P0/P1 sign-off
│       └── settlement.py         ← PHASE 6: SettlementManager — EOD PnL reconciliation + tax CSV
│
├── src/aegis/fulfillment/        ← PHASE 6: Fulfillment clients (regular subpackage)
│   ├── printful.py               ← Printful POD order creation + status polling
│   ├── cjdropshipping.py         ← CJ Dropshipping order + tracking
│   └── shopify.py                ← Shopify draft order → checkout → fulfilment
│
├── aegis-harden/                 ← PHASE 5 Hardening: bot-resilience (uv workspace member)
│   └── src/aegis/harden/
│       ├── fingerprint/          ← 8 JA3/JA4/H2 browser fingerprint profiles
│       ├── playbooks/            ← Per-source delay/proxy YAML configs
│       └── honeypot/             ← URL + DOM trap detection
│
├── aegis-phase12/                ← PHASE 12: Security & Secrets (uv workspace member)
│   └── src/aegis/security/       ← VaultClient, PIIScrubber, AuditLogger, RBACEnforcer
│
├── aegis-phase13/                ← PHASE 13: Test helpers (uv workspace member)
│   └── src/aegis/testing/        ← fake_redis, fake_pg_pool, fake_llm_gateway stubs
│
├── aegis-phase15/                ← PHASE 15: Full DR orchestrator (standalone, not in workspace)
│   └── src/aegis/dr/
│       ├── orchestrator.py       ← DrOrchestrator: runs all backup + drill jobs
│       ├── drill.py              ← Weekly restore drill: backup → restore → validate
│       ├── health.py             ← DrHealthChecker: RPO drift → SlaSnapshot
│       ├── backup/               ← postgres.py, redis.py, restic.py, models.py
│       └── cli.py                ← aegis dr backup|restore|drill|status|runbook|health
│
├── db/migrations/                ← SQL files that create all database tables
├── docker-compose.yml            ← 16 Docker containers and how they connect
├── config/
│   ├── grafana/                  ← Grafana provisioning (datasources + Mission Control dashboard)
│   ├── loki/loki.yaml            ← Phase 14 Loki log aggregation config
│   ├── promtail/config.yaml      ← Phase 14 Promtail log collector config
│   └── prometheus/               ← Prometheus scrape config
└── docs/
    ├── adr/                      ← Architecture Decision Records
    ├── slo/aegis-slos.md         ← SLO definitions with error budgets
    └── phase3/                   ← Phase 3 ML architecture docs
```

---

## 2. The Live Data Flow (What is actually happening?)

Here is the exact journey of one piece of data — say, a trending GitHub repo about an AI tool.

### Step 1 — Scrape (Phase 0)
You run `aegis scrape --source github-trending`. The `github_trending.py` adapter fetches GitHub's trending page, extracts the repo name, description, and star count, and wraps it in a `ProductSignal` object. That object is written to the `signals` table in TimescaleDB (port 5433). A SHA-256 content hash is computed first — if this exact signal already exists, it's skipped silently.

Every HTTP request first passes through `HardenShim.preflight()` (Phase 5 Hardening): this picks a browser fingerprint from 8 JA3/JA4 profiles, applies a per-source delay, and screens the URL for honeypot traps — all transparent to the adapter itself.

### Step 2 — Aggregate into a Trend Candidate (Phase 1 → Phase 2 boundary)
When you run `aegis analyze`, the CLI reads recent `signals` rows from the DB and groups them by topic into a `TrendCandidate`. A trend candidate is a bundle: trend ID, title, signal count, how many unique authors posted about it, velocity over 1h/6h/24h windows, sentiment score, commercial intent score, etc.

### Step 3 — The 10-Agent Council (Phase 2)
The `run_trend()` function in `runner.py` takes that `TrendCandidate` and runs it through a LangGraph pipeline:

```
START
├── SCOUT       → "Is the velocity strong enough to bother? Score it."
├── GEO_ARBITRAGE → "Is there a geographic price gap to exploit?"
└── NARRATIVE   → "What story is the internet telling about this?"
        ↓  (all three run in parallel, then join)
   HISTORIAN    → "Have we seen this trend before? Check ChromaDB memory."
        ↓
   SOURCER      → "Can we source/supply this product?" (skipped if Scout scored too low)
        ↓
   AUDITOR      → "Is the supplier legit?" (skipped if Sourcer found nothing)
        ↓
   SENTINEL     → "Is this trend about to peak and crash?" (always runs — calls Phase 3 ML)
        ↓
   COMPLIANCE   → "Any legal/policy red flags?" (BLOCK here = pipeline stops)
        ↓
   RED_TEAM     → "Devil's advocate — what could go wrong?"
        ↓
   HEDGE        → "Given our existing portfolio, does this still make sense?"
        ↓
   FINALIZE     → Tallies votes → emits one verdict: proceed / hold / block / escalate
```

SCOUT and SENTINEL both call into Phase 3 (the ML engine) via the bridge. The bridge translates ML predictions into the agent's own language without the agents needing to know anything about ML.

### Step 4 — ML Prediction (Phase 3)
When SCOUT or SENTINEL calls the bridge, `InferenceRunner.run()` fires:
1. Pulls the raw signals from DB, converts them to a 20-feature numeric vector (`FeatureWindow`).
2. Runs the **heuristic model** — always works, no GPU needed, sub-12ms. Produces `p_breakout` and `p_decline` at 4 time horizons: 1h, 6h, 24h, 72h.
3. Optionally runs neural models (PatchTST, TimesNet) if PyTorch is installed — they can only *reduce* confidence, never flip the verdict.
4. Returns an `InferenceResult` back to the bridge, which translates it to an `AgentDecision`.

### Step 5 — Publish to Phase 4 (Phase 2 → Phase 4 handoff)
Once the council finishes, `run_trend()` maps the verdict vocabulary (`proceed → ENTER`, `hold → HOLD`, `block → BLOCK`, `escalate → HOLD`) and does an `XADD` — writes a JSON message to the Redis Stream `aegis:phase2:graph_results` under the `"body"` key. The stream is capped at 10,000 entries to prevent Redis memory growth.

### Step 6 — Alert Pipeline (Phase 4)
The Phase 4 `IntakeWorker` watches that Redis Stream via `XREADGROUP`. When it sees a new message:
1. **Compose** — builds an `Alert` object with title, verdict, score, confidence, priority.
2. **Kelly sizing** — computes a recommended position size using fractional-Kelly. Advisory only.
3. **Risk gates** — blocks the alert if expected margin < $1.00 or confidence < 40%.
4. **Dedup** — drops the alert if the same trend already fired within the last hour.
5. **Outbox** — writes to the `alerts` table and an `alert_outbox` row. Durable — survives crashes.
6. **SSE fan-out** — pushes the alert to the in-memory event bus (anyone watching `:8200/stream`).
7. **DrainWorker** — reads `alert_outbox`, calls Discord/Telegram/ntfy, marks the row done.

### Step 6.5 — Capital Execution (Phase 6)

For ENTER verdicts that pass all gates, the `ExecutionEngine` in Phase 6 takes the alert and optionally converts it into a real fulfillment order — depending on the current mode:

- **Advisory** (default): a plan is computed and logged; zero capital is deployed. Safe for development and CI.
- **Staging**: a paper trade is simulated with mock fulfillment calls.
- **Live**: a real fulfillment order is placed via Printful (print-on-demand), CJ Dropshipping, or Shopify.

The engine calculates position size using fractional-Kelly: `quantity = floor(kelly_fraction × kelly_score × capital_max / unit_cost)`, capped at 10% of total capital. A **daily drawdown circuit breaker** rejects all new plans if cumulative realised losses exceed `AEGIS_CAPITAL_DAILY_LOSS_LIMIT_USD`.

High-priority plans (score ≥ 0.85 = P0, score ≥ 0.70 = P1) are sent to the `ApprovalBroker`, which posts an inline Telegram button. A human must tap **Approve** or **Reject** within the timeout window; unanswered P1s auto-reject, unanswered P0s escalate. Each day ends with `SettlementManager` computing realised/unrealised PnL, writing a `DailySettlement` record, and exporting a tax CSV.

### Step 7 — Observed, Logged, and Backed Up (Phases 14 & 15)
While all of this runs:
- **Phase 14 OTel tracing**: every FastAPI route and key agent operation emits a span to Jaeger via OTLP. You can trace one trend from scrape → agent → alert in Jaeger at `:16687`.
- **Phase 14 Prometheus metrics**: 24 counters/histograms tick in real time. Grafana Mission Control at `:3001` shows ingest rate, LLM cost, alert latency, and model drift.
- **Phase 14 Loki logs**: every structlog JSON line from every container is shipped by Promtail to Loki. Query them in Grafana Explore → Loki datasource.
- **Phase 15 backups**: Postgres is incrementally backed up every 15 minutes via pgBackRest; the model registry and Redis RDB are snapshotted hourly. A weekly automated drill restores the latest backup to a throw-away `aegis_drill` database and validates row counts.

---

## 3. "The Single Command" (How do I turn the key?)

The system already has everything needed. The single command is:

```bash
docker compose up -d
```

Run it from the repo root (`/home/atishayjain/code/aegis-pulse`).

**What happens when you press Enter:**

Docker spins up 16 containers in dependency order. You'll see output like this scroll past:
```
[+] Running 16/16
 ✔ Container aegis-postgres        Healthy
 ✔ Container aegis-redis           Healthy
 ✔ Container aegis-minio           Healthy
 ✔ Container aegis-flaresolverr    Started
 ✔ Container aegis-predict         Healthy
 ✔ Container aegis-execute-api     Healthy
 ✔ Container aegis-execute-drain   Started
 ✔ Container aegis-dashboard       Healthy
 ✔ Container aegis-prometheus      Healthy
 ✔ Container aegis-grafana         Healthy
 ✔ Container aegis-jaeger          Started
 ✔ Container aegis-loki            Healthy
 ✔ Container aegis-promtail        Started
 ✔ Container ollama                Started
 ✔ Container ollama-init           Started (exits 0 after model pull)
```

Postgres starts first (takes ~30s cold-start). The predict, execute, and dashboard services wait for Postgres and Redis to pass their health checks before starting. **Total cold-start time: approximately 90–120 seconds.**

Once the stack is up, open the Command Center:

```
http://localhost:8300
```

Then feed data in and run the intelligence cycle. **The easiest path:**

```bash
uv run aegis daily
```

Or run each step individually:

```bash
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 50
uv run aegis scrape --source hacker-news --limit 50
uv run aegis scrape --source github-trending --limit 30
uv run aegis scrape --source google-news --query "tech trends" --limit 30
uv run aegis scrape --source amazon --limit 80
uv run aegis analyze --limit 20
```

For a targeted deep-dive:

```bash
uv run aegis topic "AI chips"
uv run aegis topic "NVIDIA" --no-llm --json-out   # heuristic-only, no API keys needed
```

---

## 4. How to "See" the System (Where are my dials and gauges?)

### Web UIs

| What you want to see | URL | Login |
|---|---|---|
| **AEGIS Command Center** — live signals, agent verdicts, health, ops console | `http://localhost:8300` | none |
| **Grafana Mission Control** — ingest rate, LLM cost, alert latency, model drift | `http://localhost:3001` | `admin` / `aegis_dev_admin_pw` |
| **Grafana Explore → Loki** — query container logs from all 16 services | `http://localhost:3001` → Explore → Loki | same |
| **Phase 3 ML API** — raw prediction endpoint | `http://localhost:8100/healthz` | none |
| **Phase 4 live alert dashboard** — SSE stream rendered as a page | `http://localhost:8200/dashboard/` | none |
| **Prometheus raw metrics** | `http://localhost:9091` | none |
| **Jaeger distributed traces** — trace a trend across every service hop | `http://localhost:16687` | none |
| **MinIO object store** — Parquet files, model snapshots, DR backups | `http://localhost:9003` | `aegis-dev-key` / `aegis-dev-secret-please-change` |

### Checking scraped data

```bash
uv run aegis signals tail --limit 20
uv run aegis signals tail --platform hacker_news --limit 10

psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT platform, title, created_at FROM signals ORDER BY created_at DESC LIMIT 20;"
```

### Checking alerts

```bash
uv run --package aegis-execute aegis-execute tail \
  --tenant 00000000-0000-0000-0000-000000000001 --limit 20

curl -N http://localhost:8200/stream
```

### Checking observability (Phase 14)

```bash
# View all aegis_obs_* Prometheus metrics:
curl -s http://localhost:9091/api/v1/label/__name__/values | python3 -m json.tool | grep aegis_obs

# Query Loki from the CLI (requires lokitool or httpie):
curl -s 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={container_name="aegis-dashboard"}' \
  --data-urlencode 'limit=20'

# View OTel traces — open Jaeger in browser:
# http://localhost:16687 → Service: aegis-dashboard → Find Traces
```

### Checking capital execution (Phase 6)

```bash
# Engine status + drawdown position:
curl -s http://localhost:8200/capital/status | python3 -m json.tool

# Recent execution plans:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT trend_id, status, quantity, total_capital_usd FROM execution_plans ORDER BY created_at DESC LIMIT 5;"

# Today's settlement PnL:
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT settle_date, realised_pnl_usd, plan_count FROM settlements ORDER BY settle_date DESC LIMIT 3;"
```

### Checking DR health (Phase 15)

```bash
# Check SLA status (RPO drift):
cd aegis-phase15 && uv run aegis dr status

# Check backup health in the integrated module:
python3 -c "
from aegis.backup import BackupHealth, BackupSettings
from aegis.backup.pgbackrest_manager import BackupManager
print('Backup module loaded OK')
"

# Trigger a dry-run drill to validate backup integrity:
cd aegis-phase15 && uv run aegis dr drill --dry-run
```

### Finding errors

```bash
# See all container logs, filtered to errors only:
docker compose logs --since 10m 2>&1 | grep '"level":"error"'

# Watch a specific service live:
docker compose logs -f aegis-dashboard
docker compose logs -f aegis-execute-drain
docker compose logs -f aegis-predict

# Query errors in Loki (via Grafana Explore → LogQL):
# {container_name=~"aegis-.*"} |= "error"
```

---

## 5. What to Expect (The baseline behavior)

### Healthy system fingerprint

- `docker compose ps` shows all 16 containers as **healthy** (not just "running").
- `curl -s http://localhost:8300/health` returns `{"status":"ok"}` (Command Center).
- `curl -s http://localhost:8200/healthz` returns `{"status":"ok"}` (Phase 4 execute-api).
- `curl -s http://localhost:8100/healthz` returns `{"status":"ok"}` (Phase 3 predict).
- Grafana at `:3001` shows Prometheus metrics ticking (scrape interval = 15s).
- Grafana Mission Control shows `aegis_obs_ingest_signals_total` climbing after scrapes.
- Loki in Grafana Explore shows structured JSON logs from all running containers.
- Jaeger at `:16687` shows traces for dashboard API calls when `OTEL_ENABLED=true`.

### Throughput and timing

| Operation | Expected time |
|---|---|
| Cold Docker startup (all 16 containers) | 90–120 seconds |
| `aegis scrape github-trending --limit 30` | ~5 seconds |
| `aegis scrape hacker-news --limit 50` | ~3 seconds |
| `aegis scrape reddit-rss --limit 50` | ~8 seconds |
| `aegis scrape google-news --query "..." --limit 30` | ~4 seconds |
| `aegis scrape amazon --limit 80` | ~15 seconds |
| `aegis daily` (all 7 sources + analysis) | 3–6 minutes |
| `aegis analyze --limit 20` (20 trends, no LLM) | 30–90 seconds |
| One trend through the ML inference pipeline | < 12ms (heuristic path) |
| Alert appearing in SSE stream after `analyze` | < 2 seconds |
| Phase 6 advisory plan (no fulfillment calls) | < 50ms |
| Phase 6 Telegram approval wait (P0 plan) | human-dependent; timeout = configurable |
| Prometheus metrics scrape cycle | 15 seconds |
| Loki log ingestion latency (Promtail → Loki) | 5–15 seconds |
| pgBackRest incremental Postgres backup | 30–120 seconds |
| Phase 15 weekly restore drill | 5–30 minutes |

### Normal warnings you can safely ignore

- `content_hash mismatch — signal skipped` — duplicate caught by dedup hash. Expected on repeated scrapes.
- `ResourceWarning: ... Unix domain socket` — LangGraph leaves sockets open at teardown. Suppressed in test config; harmless in dev.
- `DeprecationWarning: google.protobuf` — a Python 3.12 compat issue in optional `onnx` dependency. The heuristic model takes over.
- `AEGIS_DISABLE_OLLAMA=1` in logs — Ollama intentionally disabled in containers. LLM fallback chain moves on to Groq/OpenRouter/Gemini.
- `runner.stream_publish_failed` — Redis was momentarily unavailable. That one result is lost but the agent run completes.
- `OTel exporter failed` — Jaeger OTLP endpoint unreachable. `_NoOpTracer` takes over; no service crash.
- `Promtail: connect: connection refused` — normal during the first 5–10 seconds while Loki warms up.

### What a full healthy cycle looks like

After `docker compose up -d` settles, run `uv run aegis daily`. You should see:

- **~350 signals** in the `signals` table across 7 platforms.
- **~20 `GraphResult` entries** logged, each with a verdict (`proceed` / `hold` / `block`).
- **The Command Center at `:8300`** showing live signal counts, agent verdict cards, all services green.
- **Grafana Mission Control** showing `aegis_obs_ingest_signals_total`, `aegis_obs_agent_task_duration_ms`, `aegis_obs_alert_delivered_total` all moving.
- **Phase 4 alerts** visible at `http://localhost:8200/dashboard/` — typically 8–15 ENTER/HOLD alerts per 20 trends.
- **Phase 6 plans** visible at `http://localhost:8200/capital/status` — in advisory mode all plans are logged with `status="advisory"` and zero capital deployed.
- **Jaeger** showing traces for each dashboard API call (service filter: `aegis-dashboard`).
- **Loki** showing JSON log lines from `aegis-dashboard`, `aegis-execute-drain`, `aegis-predict`.

The system is designed to produce **at least one result for every trend, always** — even if every optional component (LLM, neural models, Redis snapshot, OTel, Sentry) fails, the heuristic floor guarantees a deterministic verdict comes out the other end.

---

## 6. Disaster Recovery (How do I get back up if something breaks?)

### Backup schedule (Phase 15)

| Target | Frequency | Tool | Location |
|--------|-----------|------|---------|
| Postgres (signals, alerts, predictions) | Every 15 min | pgBackRest | MinIO `aegis-dr` bucket |
| Redis (in-memory streams, cache) | Every 5 min | BGSAVE + upload | MinIO `aegis-dr` bucket |
| Model registry (Phase 3 ML artifacts) | Every 60 min | MinIO copy | MinIO `aegis-dr/models/` |
| Filesystem (code, config) | Weekly | restic (encrypted) | MinIO `aegis-restic` bucket |

### Recovery commands

```bash
# Check current SLA status (are we within RPO/RTO targets?):
cd aegis-phase15 && uv run aegis dr status

# Trigger an on-demand backup:
cd aegis-phase15 && uv run aegis dr backup --target postgres

# Validate backup integrity without touching production (dry-run):
cd aegis-phase15 && uv run aegis dr drill --dry-run

# Full restore (DESTRUCTIVE — creates drill database, not prod):
cd aegis-phase15 && uv run aegis dr restore --target postgres --dry-run
# Remove --dry-run only after verifying the above output looks correct

# Consult a failure-mode runbook:
cd aegis-phase15 && uv run aegis dr runbook pg_corruption
cd aegis-phase15 && uv run aegis dr runbook docker_dead
```

### Failure-mode quick guide

| Scenario | First action |
|----------|-------------|
| Postgres corruption / unresponsive | `aegis dr runbook pg_corruption` → restore from MinIO backup |
| Redis OOM / data lost | `aegis dr runbook redis_oom` → restore RDB from MinIO |
| Disk full | `aegis dr runbook disk_full` → run retention command then clear old Parquet |
| Docker/WSL crash | `aegis dr runbook docker_dead` + `docker compose up -d` |
| Laptop stolen / total loss | `aegis dr runbook laptop_stolen` → fresh machine + restore from MinIO |

---

## 7. Daily Workflow Summary

```bash
# One command does everything:
uv run aegis daily

# Or for a specific topic:
uv run aegis topic "your topic here"

# Then open the dashboard to see results:
# http://localhost:8300

# Check Grafana Mission Control for system health:
# http://localhost:3001

# If anything looks wrong, check logs in Grafana → Explore → Loki:
# {container_name=~"aegis-.*"} |= "error"

# If a trend analysis result was lost, check Jaeger traces:
# http://localhost:16687
```
