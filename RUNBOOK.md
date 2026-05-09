# AEGIS Pulse — Complete Runbook

> Phase 0 → Phase 1 → Phase 2: full step-by-step guide  
> Every command, every file, every data flow, every UI explained.

---

## Table of Contents

1. [Project Architecture](#1-project-architecture)
2. [Prerequisites & One-Time Setup](#2-prerequisites--one-time-setup)
3. [Start the Stack](#3-start-the-stack)
4. [How to See the Database (Docker)](#4-how-to-see-the-database-docker)
5. [Phase 0 — Data Ingestion (Scrape)](#5-phase-0--data-ingestion-scrape)
6. [Phase 1 — Data Storage & Inspection](#6-phase-1--data-storage--inspection)
7. [Phase 2 — Agent Intelligence Pipeline](#7-phase-2--agent-intelligence-pipeline)
8. [Data Flow: Phase 0 → 1 → 2](#8-data-flow-phase-0--1--2)
9. [Seeing Graphs, Metrics & Traces](#9-seeing-graphs-metrics--traces)
10. [All CLI Commands Reference](#10-all-cli-commands-reference)
11. [File Map — What Every File Does](#11-file-map--what-every-file-does)
12. [What to Expect: Outputs Explained](#12-what-to-expect-outputs-explained)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Project Architecture

```
╔══════════════════════════════════════════════════════════════════╗
║                    AEGIS PULSE SYSTEM                           ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  ┌─────────────────────────────────────────────────────────┐   ║
║  │              PHASE 0 — Data Ingestion                   │   ║
║  │                                                         │   ║
║  │  Web Sources: Reddit, HackerNews, GitHub, TikTok,       │   ║
║  │               Amazon, Pinterest, Nitter, YouTube, etc.  │   ║
║  │       ↓                                                 │   ║
║  │  Scrape Adapters (src/aegis/scrape/sources/)            │   ║
║  │       ↓                                                 │   ║
║  │  ProductSignal (Pydantic model, deduped by hash)        │   ║
║  └────────────────────────┬────────────────────────────────┘   ║
║                           │ INSERT                              ║
║  ┌────────────────────────▼────────────────────────────────┐   ║
║  │              PHASE 1 — Persistence                      │   ║
║  │                                                         │   ║
║  │  Postgres/TimescaleDB                                   │   ║
║  │    signals (hypertable, 1-day chunks)                   │   ║
║  │    authors                                              │   ║
║  │    velocity_snapshots                                   │   ║
║  │    prediction_outcomes                                  │   ║
║  │                                                         │   ║
║  │  Redis ─── Cache, pub/sub, inter-agent streams          │   ║
║  │  MinIO ─── Raw objects, feature snapshots               │   ║
║  └────────────────────────┬────────────────────────────────┘   ║
║                           │ fetch + build TrendCandidate        ║
║  ┌────────────────────────▼────────────────────────────────┐   ║
║  │              PHASE 2 — Agent Intelligence               │   ║
║  │                                                         │   ║
║  │  LangGraph 10-node DAG                                  │   ║
║  │                                                         │   ║
║  │  scout → geo_arbitrage → narrative → historian          │   ║
║  │       → sourcer → auditor → sentinel → compliance       │   ║
║  │       → red_team → hedge → finalize                     │   ║
║  │                                                         │   ║
║  │  Output: GraphResult                                    │   ║
║  │    final_verdict: PROCEED / HOLD / BLOCK / ESCALATE     │   ║
║  │    final_score: 0.0–1.0                                 │   ║
║  │    final_priority: P0/P1/P2/P3                          │   ║
║  └─────────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════════════════════╝
```

### Services (Docker)

| Container | External Port | What it does |
|-----------|--------------|--------------|
| `aegis-postgres` | `5433` | TimescaleDB (Postgres 16 + TimescaleDB + pgvector) |
| `aegis-redis` | `6380` | Cache, rate-limit counters, inter-agent bus |
| `aegis-minio` | `9002` (API), `9003` (UI) | S3-compatible object store |
| `aegis-flaresolverr` | `8191` | Cloudflare challenge bypass for scraping |
| `aegis-prometheus` | `9091` | Metrics collection and alerting |
| `aegis-grafana` | `3001` | Dashboard UI |
| `aegis-jaeger` | `16687` | Distributed request tracing |

---

## 2. Prerequisites & One-Time Setup

### 2a. Install system tools

```bash
# On Ubuntu/WSL2:
sudo apt-get update && sudo apt-get install -y git curl

# Install uv (the package manager):
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # or restart terminal

# Verify:
uv --version
docker --version
docker compose version
```

### 2b. Clone and configure

```bash
git clone <your-repo-url> aegis-pulse
cd aegis-pulse

# Create your local environment file:
cp .env.example .env
```

The `.env` file you just created has all defaults pre-filled for the
Docker stack. **You do not need to change anything to get started.**

Optional — if you want LLM-enhanced analysis (free), add a Groq key:
```bash
# Get a free key at https://console.groq.com/
echo 'GROQ_API_KEY=gsk_your_key_here' >> .env
```

### 2c. Install Python dependencies

```bash
uv sync --all-extras
```

This creates `.venv/` with all packages including optional extras:
- `langgraph` — the agent graph runtime
- `chromadb` + `sentence-transformers` — semantic vector memory
- `boto3` — MinIO/S3 object store client

### 2d. Verify install

```bash
uv run aegis --version
# Should print: aegis, version 0.2.0
```

---

## 3. Start the Stack

### Start all 7 services

```bash
uv run aegis up
# or:
docker compose up -d
```

### Wait for health checks (30–60 seconds)

```bash
uv run aegis status
```

Expected output (all services `healthy`):
```
SERVICE                  STATE          HEALTH         PORTS
flaresolverr             running        healthy        8191->8191
grafana                  running        healthy        3001->3000
jaeger                   running        healthy        16687->16686,...
minio                    running        healthy        9002->9000,9003->9001
postgres                 running        healthy        5433->5432
prometheus               running        healthy        9091->9090
redis                    running        healthy        6380->6379
```

> **Why does Postgres say 5433?** Docker maps container port 5432 to host
> port 5433 to avoid conflicts if you already have Postgres running locally.
> The app's `.env` uses `localhost:5433` accordingly.

### Database is auto-initialized

The first time you start `aegis-postgres`, Docker runs
`db/migrations/0001_init.sql` automatically via the `initdb.d` volume mount.
This creates all tables, TimescaleDB hypertables, RLS policies, and the
default tenant. **You never need to run migrations manually for a fresh stack.**

For subsequent schema changes (new columns, etc.) run:
```bash
uv run aegis migrate
```

---

## 4. How to See the Database (Docker)

### Option A — psql in the container (no external tool needed)

```bash
# Connect as the app user:
docker exec -it aegis-postgres psql -U aegis_app -d aegis

# Inside psql:
\dt                          -- list all tables
\d signals                   -- describe the signals table
SET app.current_tenant = '00000000-0000-0000-0000-000000000001';
SELECT count(*) FROM signals;
SELECT platform, title, ts FROM signals ORDER BY ts DESC LIMIT 10;
\q                           -- quit
```

### Option B — DBeaver (recommended GUI)

1. Download DBeaver Community Edition: https://dbeaver.io/download/
2. New Connection → PostgreSQL
3. Fill in:
   - Host: `localhost`
   - Port: `5433`
   - Database: `aegis`
   - Username: `aegis_app`
   - Password: `aegis_app_dev_pw`
4. Click Test Connection → Finish
5. In the SQL Editor, always set the tenant context first:
   ```sql
   SET app.current_tenant = '00000000-0000-0000-0000-000000000001';
   SELECT * FROM signals ORDER BY ts DESC LIMIT 20;
   ```

> **Why do I need SET app.current_tenant?**  
> All tables use Row-Level Security (RLS). Without this SET, Postgres
> returns zero rows for any query. This is by design — the same DB schema
> supports multiple tenants in production.

### Option C — pgAdmin

Same connection details as DBeaver. Port `5433`, not `5432`.

### Useful queries to watch data arrive

```sql
-- Signal count by platform (run after scraping)
SET app.current_tenant = '00000000-0000-0000-0000-000000000001';
SELECT platform, count(*) AS n FROM signals GROUP BY platform ORDER BY n DESC;

-- Latest 20 signals:
SELECT platform, title, ts, source_confidence FROM signals ORDER BY ts DESC LIMIT 20;

-- Signals from the last hour:
SELECT platform, title, ts FROM signals WHERE ts > NOW() - INTERVAL '1 hour' ORDER BY ts DESC;

-- Author table:
SELECT handle, platform, follower_count, total_posts FROM authors ORDER BY follower_count DESC LIMIT 10;

-- Signal velocity (hourly aggregate — TimescaleDB continuous view):
SELECT bucket, platform, signal_count FROM signals_hourly ORDER BY bucket DESC LIMIT 24;
```

### Redis inspection

```bash
# Redis CLI (via Docker):
docker exec -it aegis-redis redis-cli

# Check all AEGIS keys:
KEYS aegis:*

# See inter-agent stream messages:
XRANGE aegis:agent-bus - + COUNT 10

# Exit:
quit
```

---

## 5. Phase 0 — Data Ingestion (Scrape)

Phase 0 fetches raw content from web sources and writes `ProductSignal`
records to the `signals` table.

### Run your first scrape

```bash
# Reddit (no API key needed):
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 20

# HackerNews (no API key):
uv run aegis scrape --source hacker-news --limit 30

# GitHub Trending (no API key):
uv run aegis scrape --source github-trending --limit 25

# TikTok trends (no API key, scrapes public pages):
uv run aegis scrape --source tiktok --limit 20

# Amazon best-sellers (no API key):
uv run aegis scrape --source amazon --limit 15

# Pinterest (no API key):
uv run aegis scrape --source pinterest --limit 20

# Nitter/Twitter mirror (no API key):
uv run aegis scrape --source nitter --limit 20

# Google Trends (no API key):
uv run aegis scrape --source google-trends --limit 10
```

### Sources that require API keys

```bash
# Reddit API (PRAW) — register at https://www.reddit.com/prefs/apps
# Add to .env: AEGIS_REDDIT_CLIENT_ID=... AEGIS_REDDIT_CLIENT_SECRET=...
uv run aegis scrape --source reddit --subreddit Entrepreneur --limit 50

# YouTube Data API v3 — get at https://console.cloud.google.com/
# Add to .env: AEGIS_YOUTUBE_API_KEY=...
uv run aegis scrape --source youtube --query "dropshipping products 2026" --limit 20
```

### What happens during a scrape (step by step)

```
1. CLI parses arguments → builds adapter config
2. Adapter.run() fetches from the source (HTTP, RSS, or Playwright)
3. Each item → ProductSignal (Pydantic validation, hash dedup)
4. Signals batched in memory (batch of 50)
5. insert_signals() called:
   a. Upsert authors (ON CONFLICT DO UPDATE)
   b. INSERT INTO signals ON CONFLICT DO NOTHING (dedup by platform+external_id)
6. Progress printed: "flushed N (total emitted: M)"
7. Final: "Done. Emitted N signals."
```

### Dry-run mode (no DB writes)

```bash
uv run aegis scrape --source reddit-rss --subreddit programming --limit 5 --dry-run
```

Prints JSON for each signal without writing to the DB. Useful for debugging.

### What `content_hash mismatch` warnings mean

```
WARNING: reddit_rss.parse.failed — content_hash mismatch: stored=... derived=...
```

This is a known pre-existing issue: certain Reddit RSS fields are
populated after the hash is computed. The signal is **safely skipped**,
not corrupted. You will see 1–3 of these per scrape run. Ignore them.

---

## 6. Phase 1 — Data Storage & Inspection

Phase 1 is the persistence layer. After scraping, data lives in Postgres.

### Verify signals landed

```bash
# Via CLI:
uv run aegis signals tail --limit 20

# With platform filter:
uv run aegis signals tail --limit 10 --platform reddit

# Via psql:
docker exec aegis-postgres psql -U aegis_app -d aegis \
  -c "SET app.current_tenant='00000000-0000-0000-0000-000000000001'; SELECT count(*) FROM signals;"
```

### Daily summary report

```bash
# Yesterday's signals:
uv run aegis report daily

# A specific date:
uv run aegis report daily --date 2026-05-04
```

### Database tables explained

| Table | Type | Purpose |
|-------|------|---------|
| `tenants` | regular | Multi-tenant root. One row per org. |
| `authors` | regular | Social media authors (upserted per signal). |
| `signals` | **hypertable** (1-day chunks) | Main signal store. Every scraped post/item. |
| `media` | **hypertable** (7-day chunks) | Attachments linked to signals. |
| `velocity_snapshots` | **hypertable** (1-day chunks) | Time-series trend velocity snapshots. |
| `prediction_outcomes` | **hypertable** (7-day chunks) | Feedback loop: actual vs predicted outcomes. |
| `alembic_version` | regular | Tracks which migrations have been applied. |
| `aegis_sql_revisions` | regular | Records raw SQL migration history. |

### TimescaleDB views (auto-updated)

```sql
-- Hourly signal counts per platform (last 24 hours):
SET app.current_tenant = '00000000-0000-0000-0000-000000000001';
SELECT bucket, platform, signal_count FROM signals_hourly
WHERE bucket > NOW() - INTERVAL '24 hours'
ORDER BY bucket DESC, signal_count DESC;

-- 6-hourly:
SELECT * FROM signals_6hourly ORDER BY bucket DESC LIMIT 20;

-- Daily:
SELECT * FROM signals_daily ORDER BY bucket DESC LIMIT 7;
```

---

## 7. Phase 2 — Agent Intelligence Pipeline

Phase 2 takes the signals from Phase 1 and runs them through a
10-node LangGraph agent pipeline to score and prioritize arbitrage opportunities.

### Run the pipeline (the easy way)

```bash
uv run aegis analyze
```

This automatically:
1. Fetches 20 most recent signals from DB
2. Builds a `TrendCandidate`
3. Runs all 10 agents
4. Prints a color-coded verdict table

### Options

```bash
# Use more signals (higher confidence):
uv run aegis analyze --limit 50

# Filter by platform:
uv run aegis analyze --platform reddit --limit 30

# Force heuristic-only path (fast, no LLM needed):
uv run aegis analyze --no-llm

# Get full JSON output for programmatic use:
uv run aegis analyze --json-out

# Custom trend ID and title:
uv run aegis analyze --trend-id "ml-trends-001" --title "ML Stack Trends"
```

### What the output looks like

```
==============================================================
  AEGIS Pulse — Phase 2 Analysis   [analyze-c645273a]
==============================================================
  Verdict   : HOLD
  Score     : 0.362   Confidence: 0.598
  Priority  : P3_HOUSEKEEPING
  Halt      : completed
  Agents    : 7 ran   Duration: 1649ms

  Agent            Verdict     Score    Conf  Rationale
  ---------------- ---------- ------  ------  ------------------------------
  compliance       proceed     1.000   0.850  no flags
  hedge            proceed     1.000   0.200  no active pool
  red_team         hold        0.750   0.760  falsifiers=1
  geo_arbitrage    proceed     0.667   0.640  arbitrage_score=0.33
  narrative        hold        0.450   0.773  sentiment_intensity=0.30
  scout            hold        0.443   0.580  velocity_class=hot
  historian        proceed     0.000   0.200  no analogues found
==============================================================
```

### Understanding the verdict

| Verdict | Score range | Meaning | Action |
|---------|------------|---------|--------|
| `PROCEED` | 0.7–1.0 | Strong arbitrage signal | Act now — review details |
| `HOLD` | 0.3–0.7 | Moderate signal | Monitor — re-analyze in 6h |
| `BLOCK` | 0.0–0.4 | Weak/risky signal | Do not act |
| `ESCALATE` | any | Edge case, human needed | Manual review required |

### Understanding `halt_reason`

| Halt reason | What happened |
|------------|---------------|
| `completed` | All 10 agents ran to completion — full verdict |
| `scout_below_threshold` | Scout blocked early — too little velocity to proceed |
| `blocked_by_compliance` | Compliance agent found TOS violation — hard stop |
| `vetoed_by_red_team` | Red team adversarial test failed — too many falsifiers |
| `vetoed_by_hedge` | Hedge agent found negative expected value — stop |
| `exception` | Internal error — check logs |
| `timeout` | Pipeline exceeded 120s wall clock — rare |

### Priority levels

| Level | Name | Meaning |
|-------|------|---------|
| P0 | `BREAKOUT` | Confirmed breakout — act in minutes |
| P1 | `EXIT` | Exit signal on existing position |
| P2 | `OPPORTUNITY` | Standard candidate — act today |
| P3 | `HOUSEKEEPING` | Background — monitor, no urgency |

### The 10 agents explained

| Agent | What it does |
|-------|-------------|
| **scout** | Gate: checks velocity (v1h/v6h/v24h). Blocks low-signal noise. |
| **geo_arbitrage** | Finds regional pricing gaps and sentiment differences. |
| **narrative** | Analyzes coherence: is the trend a real narrative or noise? |
| **historian** | Looks up analogous past trends in ChromaDB vector memory. |
| **sourcer** | Checks source diversity — are signals from many authors/platforms? |
| **auditor** | Data quality audit: completeness, freshness, confidence. |
| **sentinel** | Coordination/astroturfing detection — fake virality check. |
| **compliance** | TOS risk and legal exposure screening. |
| **red_team** | Adversarial: tries to falsify the thesis to stress-test it. |
| **hedge** | Risk-adjusted expected-value calculation across the position. |
| **finalize** | Supervisor: aggregates all decisions → final verdict + score. |

### Running the pipeline programmatically (Python API)

```python
import asyncio
from uuid import UUID

from aegis.db.pool import PgConfig, PgPool, set_shared_pool
from aegis.db.signals import fetch_recent_signals
from aegis.agents.schemas import TrendCandidate
from aegis.agents.runner import run_trend

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

async def main():
    # 1. Connect to Postgres and make it available to Phase 2 tools
    pool = PgPool(PgConfig(
        dsn="postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis"
    ))
    await pool.start()
    set_shared_pool(pool)       # Phase 2 tools pick this up automatically

    # 2. Fetch signals from Phase 1 DB
    rows = await fetch_recent_signals(pool, tenant_id=TENANT_ID, limit=20)
    signal_ids = [str(r["signal_id"]) for r in rows[:10]]

    # 3. Build a TrendCandidate (the Phase 2 input format)
    candidate = TrendCandidate(
        trend_id="my-trend-001",
        title="My Trend Title",
        summary="Brief description of the trend cluster.",
        velocity_1h=50.0,       # signals per hour
        velocity_6h=200.0,      # signals per 6h
        velocity_24h=600.0,     # signals per 24h
        sentiment=0.4,          # -1.0 to 1.0 (positive = bullish)
        commercial_intent=0.5,  # 0.0 to 1.0 (buy intent)
        novelty=0.6,            # 0.0 to 1.0 (how new is this)
        coordination_risk=0.05, # 0.0 to 1.0 (astroturf risk)
        signal_count=len(rows),
        unique_authors=min(len(rows), 15),
        platforms=["reddit"],
        sample_signal_ids=signal_ids,
        representative_text=rows[0].get("title", "") if rows else "",
    )

    # 4. Run the 10-node LangGraph pipeline
    result = await run_trend(candidate)

    # 5. Inspect the result
    print(f"Verdict:   {result.final_verdict}")
    print(f"Score:     {result.final_score:.3f}")
    print(f"Halt:      {result.halt_reason}")
    for dec in result.decisions:
        print(f"  {dec.agent}: {dec.verdict.value} ({dec.score:.2f})")

    await pool.close()

asyncio.run(main())
```

---

## 8. Data Flow: Phase 0 → 1 → 2

Here is exactly what happens at each boundary:

### Phase 0 → Phase 1: ProductSignal → signals table

```
ProductSignal (Pydantic model)
  .platform          → signals.platform (enum: reddit, tiktok, ...)
  .external_id       → signals.external_id (dedup key)
  .title             → signals.title
  .raw_text          → signals.raw_text
  .posted_at         → signals.posted_at
  .provenance.scraped_at → signals.scraped_at
  .engagement.views  → signals.views
  .engagement.likes  → signals.likes
  .engagement.comments → signals.comments
  .author.handle     → authors.handle (upserted first)
  .confidence.source_confidence → signals.source_confidence
  .confidence.completeness → signals.completeness
  .content_hash      → used for dedup (ON CONFLICT DO NOTHING)
```

The full insert SQL is in `src/aegis/db/signals.py:insert_signals()`.

### Phase 1 → Phase 2: DB rows → TrendCandidate

This conversion is done in `aegis analyze` or in your own code.
Key mapping:

```
DB rows (list of dicts from fetch_recent_signals)
  ↓
TrendCandidate
  .signal_count      ← len(rows)
  .unique_authors    ← count distinct author_ids
  .platforms         ← {r["platform"] for r in rows}
  .sample_signal_ids ← [r["signal_id"] for r in rows[:10]]
  .representative_text ← rows[0]["title"] (or joined titles)
  .velocity_*        ← computed from signal timestamps (or set manually)
  .sentiment         ← computed from raw_text if NLP available, else 0.3
  .commercial_intent ← from intent field counts
```

In production you would compute velocity from the `signals_hourly`
continuous aggregate view and sentiment from an NLP pass over `raw_text`.
For now, `aegis analyze` uses simple heuristics based on signal count.

### Phase 2 internals: GraphState flows through 10 nodes

```
initial_state(candidate)                   ← GraphState TypedDict created
         │
         ▼
[scout] velocity_classify tool → AgentDecision
         │ if scout.verdict == BLOCK → finalize immediately (scout_below_threshold)
         ▼
[geo_arbitrage] scoring → AgentDecision
[narrative] coherence analysis → AgentDecision
[historian] ChromaDB lookup → AgentDecision       ← optional, needs chromadb
[sourcer] diversity scoring → AgentDecision
[auditor] completeness check → AgentDecision
[sentinel] coordination detection → AgentDecision
[compliance] TOS check tool → AgentDecision       ← hard block if flagged
[red_team] adversarial falsifiers → AgentDecision
[hedge] expected value calc → AgentDecision
         │
         ▼
[finalize] supervisor.build_graph_result()
  → GraphResult(final_verdict, final_score, final_priority, decisions[])
```

All decisions are accumulated in `GraphState.decisions` (a list with
append-merge semantics). The finalize node reads all of them and computes
a weighted aggregation.

---

## 9. Seeing Graphs, Metrics & Traces

### Grafana Dashboards — http://localhost:3001

Login: `admin` / `aegis_dev_admin_pw`

What you'll find:
- **Scrape metrics**: signals per minute, adapter success rate, dedup rate
- **DB pool**: connection pool size, query latency histogram
- **Redis**: cache hit rate, memory usage
- **System**: CPU, memory, disk I/O per container

To see signal ingestion in real time:
1. Go to http://localhost:3001
2. Click "Dashboards" in the left sidebar
3. Open "AEGIS Pulse Overview" (if preconfigured) or create a new panel

**Create a custom signal count panel:**
1. Dashboards → New → New Panel
2. Data Source: `Prometheus`
3. Query: `rate(aegis_signals_inserted_total[5m])`
4. Panel title: "Signals/sec" → Save

### Prometheus Metrics — http://localhost:9091

Direct metrics UI. Useful for ad-hoc queries:
- `up` — which services are reachable
- `aegis_db_pool_size` — Postgres pool connections
- `aegis_db_query_duration_seconds` — query latency
- Search for `aegis_` to find all AEGIS-specific metrics

### Jaeger Traces — http://localhost:16687

Distributed request tracing. Shows how long each part of a request takes.
- Service: `aegis-pulse`
- Operations: `scrape.run`, `db.query`, `agent.run`
- Useful for diagnosing slow scrapes or slow agent nodes

### MinIO Console — http://localhost:9003

Login: `aegis-dev-key` / `aegis-dev-secret-please-change`

Buckets:
- `aegis-raw` — raw scraped objects
- `aegis-features` — feature vectors
- `aegis-models` — serialized model snapshots
- `aegis-backups` — agent pipeline output snapshots (if snapshot_manager configured)

---

## 10. All CLI Commands Reference

```bash
# Stack management
uv run aegis up                    # start all 7 Docker services
uv run aegis up --build            # rebuild and start
uv run aegis down                  # stop (keep data)
uv run aegis down --volumes        # DANGER: stop + wipe all data
uv run aegis status                # show container health
uv run aegis tail                  # stream logs (all services)
uv run aegis tail aegis-postgres   # stream Postgres logs only
uv run aegis reset                 # DANGER: full wipe + restart
uv run aegis migrate               # run Alembic migrations

# Scraping (Phase 0)
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 20
uv run aegis scrape --source hacker-news --limit 50
uv run aegis scrape --source github-trending --limit 30
uv run aegis scrape --source tiktok --limit 20
uv run aegis scrape --source amazon --limit 15
uv run aegis scrape --source nitter --limit 20
uv run aegis scrape --source pinterest --limit 20
uv run aegis scrape --source google-trends --limit 10
uv run aegis scrape --source reddit-rss --subreddit wallstreetbets --limit 50 --dry-run

# Signal inspection (Phase 1)
uv run aegis signals tail                        # last 20 signals
uv run aegis signals tail --limit 50            # last 50
uv run aegis signals tail --platform reddit     # filter by platform

# Reports
uv run aegis report daily                       # yesterday's summary
uv run aegis report daily --date 2026-05-04     # specific date

# Agent pipeline (Phase 2)
uv run aegis analyze                            # analyze 20 most recent signals
uv run aegis analyze --limit 50                 # more signals = better
uv run aegis analyze --platform reddit          # filter source platform
uv run aegis analyze --no-llm                   # heuristic path (no API key needed)
uv run aegis analyze --json-out                 # machine-readable full output
uv run aegis analyze --trend-id "my-id" --title "My Trend"

# Diagnostics
uv run aegis doctor                             # full health check
uv run aegis doctor --secrets                   # check only credentials
uv run aegis support-bundle                     # generate diagnostic zip
```

---

## 11. File Map — What Every File Does

### Root

```
aegis-pulse/
  .env                    YOUR local config (not committed). Copy from .env.example.
  .env.example            Template with all variables documented and default values.
  pyproject.toml          Package metadata, dependencies, test config, coverage config.
  docker-compose.yml      Defines all 7 services, volumes, networks, healthchecks.
  alembic.ini             Alembic migration tool config (DB URL read from AEGIS_PG_DSN).
  CLAUDE.md               Claude Code context — architecture reference for AI assistant.
  PHASE_2_OPERATIONS_MANUAL.md   Operations SOP for running the system.
  RUNBOOK.md              ← this file
```

### `db/`

```
db/migrations/
  0001_init.sql           The entire database schema:
                            - Extensions (timescaledb, vector, pgcrypto, citext)
                            - Enums (platform, tier, modality, intent, etc.)
                            - Tables (tenants, authors, signals, media, ...)
                            - TimescaleDB hypertables + chunk policy
                            - Continuous aggregates (hourly/6hourly/daily)
                            - RLS policies + current_tenant_id() function
                            - HNSW vector indexes (pgvector)
                            - 90-day data retention policy
```

### `alembic/`

```
alembic/env.py            Alembic environment. Reads AEGIS_PG_DSN from env.
                           Runs migrations as the migrate user (DDL-capable).
alembic/script.py.mako    Template for generated migration files.
alembic/versions/         Future incremental migrations go here.
```

### `src/aegis/`

```
__init__.py               Package root. Exports __version__ = "0.2.0".
config.py                 Pydantic-settings Settings class. Reads all AEGIS_* env vars.
constants.py              Immutable numeric constants (pool sizes, timeouts, etc.).
```

### `src/aegis/cli/`

```
main.py                   The `aegis` CLI entry point (Click). All commands defined here:
                             up, down, status, tail, migrate, scrape, signals, report,
                             analyze, reset, doctor, support-bundle.
```

### `src/aegis/core/`

```
logging.py                Structlog setup. JSON mode for prod, console for dev.
                           All aegis.* loggers go through here.
metrics.py                Prometheus metric definitions (counters, histograms, gauges).
                           reset_registry() used in tests to avoid duplicates.
resilience.py             Retry + circuit-breaker decorators for external calls.
```

### `src/aegis/db/`

```
pool.py                   PgPool: asyncpg connection pool with:
                             - Tenant RLS context injection on every checkout
                             - Statement timeout enforcement
                             - Prometheus metrics integration
                             - get_shared_pool()/set_shared_pool() for Phase 2 tools
signals.py                Two functions:
                             insert_signals(pool, signals, tenant_id)  ← Phase 0→1
                             fetch_recent_signals(pool, tenant_id, limit)  ← Phase 1→2
authors.py                Author upsert helpers (called by insert_signals).
```

### `src/aegis/cache/`

```
redis_cache.py            RedisCache class. Wraps redis.asyncio with:
                             - Namespace prefixing (all keys: `aegis:namespace:...`)
                             - TTL-based cache operations
                             - connect()/close() lifecycle
```

### `src/aegis/schemas/`

```
signal.py                 ProductSignal — the Phase 0 data model. Every scrape adapter
                           produces these. Validated by Pydantic v2. Contains:
                             - Platform enum ref
                             - EngagementMetrics (views/likes/comments/shares/saves)
                             - ProvenanceInfo (scraped_at, scraper_version, method)
                             - ConfidenceScores (source_confidence, completeness)
                             - AuthorProfile (handle, follower_count, etc.)
enums.py                  Platform, SourceTier, ContentModality, Intent enums.
```

### `src/aegis/scrape/`

```
base.py                   BaseAdapter abstract class. All adapters inherit from this.
                           Defines the run(**params) → AsyncIterator[ProductSignal] protocol.
cloudflare.py             FlareSolverr integration for Cloudflare-protected sites.
proxies.py                Proxy pool manager (reads from config/proxies.yaml if present).
stealth.py                Browser stealth helpers: random user agents, timing jitter.

sources/
  reddit.py               Reddit PRAW adapter (needs API key).
  reddit_rss.py           Reddit public JSON adapter (no key needed). Most reliable.
  hacker_news.py          HN Algolia API adapter (free, no key).
  github_trending.py      GitHub trending scraper (HTML parsing, no key).
  tiktok.py               TikTok public trends (Playwright-based).
  youtube.py              YouTube Data API v3 adapter (needs API key).
  instagram.py            Instagram public data (high TOS risk — use with care).
  amazon.py               Amazon best-sellers (Playwright-based).
  pinterest.py            Pinterest public boards (HTTP scraping).
  nitter.py               Twitter/X via Nitter mirrors (no key, public).
  google_trends.py        Google Trends via pytrends (no key).
```

### `src/aegis/agents/`

```
schemas.py                Core Pydantic v2 models for Phase 2:
                             TrendCandidate  — pipeline input
                             AgentDecision   — per-agent output
                             AgentMessage    — Redis Streams envelope
                             GraphResult     — full pipeline output

state.py                  GraphState TypedDict — the shared state object passed through
                           all 10 nodes. Uses annotated reducers (_merge_decisions, etc.)
                           for parallel-safe state updates.

graph.py                  build_graph() — assembles the LangGraph DAG:
                             10 agent nodes + conditional edges + finalize node
                             Compiled with .compile() for performance caching.

runner.py                 run_trend(candidate) — the public API:
                             - Lazily compiles graph (cached)
                             - Enforces 120s timeout
                             - Never raises — errors become halt_reason="exception"
                             - Optional MinIO snapshot on completion

supervisor.py             build_graph_result() — aggregates all AgentDecision objects
                           into a single GraphResult using weighted scoring.

nodes/
  base.py                 BaseAgentNode — shared logic for all agents:
                             - Tool execution with timing
                             - LLM prompt rendering (Jinja2)
                             - Heuristic fallback when no LLM available
  scout.py                Velocity threshold gating.
  geo_arbitrage.py        Regional pricing/sentiment arbitrage scoring.
  narrative.py            Narrative coherence and framing analysis.
  historian.py            ChromaDB analogue lookup (past trend comparison).
  sourcer.py              Source diversity and tier quality scoring.
  auditor.py              Data completeness and freshness audit.
  sentinel.py             Coordination/astroturf detection.
  compliance.py           TOS risk and legal exposure check.
  red_team.py             Adversarial falsifier generation.
  hedge.py                Risk-adjusted expected-value calculation.

llm/
  router.py               LLMRouter — tries providers in order:
                             Ollama → Groq → OpenRouter → Gemini
                             Per-provider circuit breakers + retry.
  prompts.py              PromptRegistry — loads .jinja2 files, renders with candidate data.
  guardrails.py           Output validation: ensures LLM output is parseable JSON.
  providers/
    base.py               BaseLLMProvider abstract class.
    ollama.py             Local Ollama provider (http://localhost:11434).
    groq.py               Groq cloud provider (GROQ_API_KEY).
    openrouter.py         OpenRouter proxy provider (OPENROUTER_API_KEY).
    gemini.py             Google Gemini provider (GEMINI_API_KEY).

memory/
  chroma_store.py         ChromaMemoryStore — stores/retrieves past trend analogues
                           using sentence-transformers embeddings + ChromaDB.
  shared_memory.py        SharedWorkingMemory — Redis-backed working memory shared
                           between agents in the same pipeline run.
  snapshots.py            SnapshotManager — writes GraphResult JSON to MinIO.

messaging/
  streams.py              AgentBus — Redis Streams publisher/consumer for inter-agent
                           event broadcasting.
  hmac_sign.py            HMAC-SHA256 message signing and verification.

tools/
  base.py                 ToolResult dataclass — standardized tool output (ok, data, error).
  velocity.py             velocity_classify — classifies velocity as quiet/growing/hot/breakout.
  compliance_check.py     compliance_check — checks against TOS violation patterns.
  monte_carlo.py          monte_carlo_margin — Monte Carlo simulation for margin estimation.
  historical.py           historical_lookup — retrieves price/volume history for comparables.
  signal_query.py         signal_query — queries DB via get_shared_pool() for recent signals.

prompts/
  scout.jinja2            Prompt template for the scout agent.
  geo_arbitrage.jinja2    ...and so on for each agent (10 total).
  (+ 8 more .jinja2 files)
```

### `config/`

```
prometheus/
  prometheus.yml          Scrape config: tells Prometheus where to pull metrics from.
                           Scrapes aegis-pulse:9464 (the app's Prometheus port).
grafana/
  (dashboard JSON files)  Pre-built Grafana dashboards loaded on first start.
```

### `bootstrap/`

```
aegis-doctor              Bash script: comprehensive health check (Docker, ports, DB, etc.)
wsl/                      WSL2 setup scripts
windows/                  Windows setup scripts
```

### `tests/`

```
conftest.py               Shared fixtures: env isolation, Prometheus reset,
                           Docker container fixtures (pg_container, redis_container).
unit/
  test_config.py          Settings validation tests.
  test_db_pool.py         PgPool tests (mock asyncpg).
  test_signals.py         insert_signals / fetch_recent_signals tests.
  test_scrape_*.py        Adapter unit tests (all mocked HTTP).
  test_cache.py           Redis cache tests.
  test_cli.py             CLI command tests (subprocess or Click test runner).
  agents/
    conftest.py           Agent-specific fixtures: LLM router reset, trend_factory.
    test_schemas.py       TrendCandidate, AgentDecision, GraphResult validation.
    test_graph.py         LangGraph build and traverse tests.
    test_runner.py        run_trend() tests (mocked graph).
    test_nodes_*.py       Per-node unit tests (10 files, one per node).
    test_tools_*.py       Tool unit tests.
    test_llm_router.py    Router fallback chain tests.
    test_messaging.py     HMAC signing + Redis Streams tests.
integration/
  (future integration tests with real containers)
```

---

## 12. What to Expect: Outputs Explained

### During `aegis scrape`

```
$ uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 20

(JSON log lines from structlog — normal)
{"event": "pgpool.connecting", ...}
{"event": "pgpool.connected", ...}
{"event": "adapter.run.start", "adapter": "reddit-rss", ...}
{"event": "HTTP Request: GET https://www.reddit.com/... 200 OK", ...}
WARNING: content_hash mismatch ... (safe to ignore, 1-3 per run)
{"event": "adapter.run.end", "seen": 20, "emitted": 17, ...}
flushed 10 (total emitted: 17)        ← batch flush
Done. Emitted 17 signals.              ← final count
```

**`seen`**: items fetched from source  
**`emitted`**: items that passed validation and were inserted  
**Difference**: deduplication (already in DB) + hash mismatch skips

### During `aegis analyze`

```
Running Phase 2 pipeline on 20 signals (LLM-assisted)…
{"event": "graph.built", "nodes": [...], "use_llm": true, ...}
{"event": "tool.call", "tool": "velocity_classify", "ok": true, ...}
{"event": "llm.provider_error", "provider": "ollama", ...}   ← expected if no Ollama
{"event": "supervisor.finalize", "verdict": "hold", "score": 0.362, ...}
{"event": "runner.completed", "duration_ms": 1649.0, ...}

(color table printed)
```

**`llm.provider_error`** on Ollama is **expected** if you don't have Ollama running locally.
The system automatically falls back to the heuristic path. Add `GROQ_API_KEY=...` to `.env`
to get LLM-enhanced reasoning without running Ollama.

### After running tests

```
$ uv run pytest tests/unit/ -q

466 passed in 126.57s (0:02:06)
Coverage: 79.37%
```

466 tests, ~2 minutes. All should pass. If any fail, check:
1. `AEGIS_DISABLE_OLLAMA=1` is in your environment (set automatically in tests)
2. No running servers on port 5432 or 6379 (tests use mocks, not real servers)

---

## 13. Troubleshooting

### "No signals found in DB. Run `aegis scrape` first."
Scrape has not been run yet, or it wrote 0 signals.
```bash
uv run aegis scrape --source reddit-rss --subreddit technology --limit 10
uv run aegis signals tail
```

### "llm.no_providers_available — agents will use heuristic-only path"
No LLM API keys configured. This is fine — the pipeline still works.
To add an LLM:
```bash
echo 'GROQ_API_KEY=gsk_yourkey' >> .env
# Then run again — no restart needed
uv run aegis analyze
```

### "scout_below_threshold" halt
The signal cluster has low velocity. The scout agent blocked early.
This means the content you scraped is not trending fast enough to be
an arbitrage candidate. Try:
```bash
# Scrape a more actively-trending subreddit:
uv run aegis scrape --source reddit-rss --subreddit wallstreetbets --limit 30
uv run aegis analyze --limit 30
```

### Containers not starting / "Cannot connect to Docker"
```bash
# Check Docker is running:
docker info

# Check ports not in use:
sudo ss -tlnp | grep -E '5433|6380|9002|9003|3001|9091|16687'

# Restart the stack:
docker compose down
docker compose up -d
```

### Postgres authentication error
The default user is `aegis_app` with password `aegis_app_dev_pw`.
If you're getting auth errors, verify your `.env` matches:
```
AEGIS_PG_DSN=postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis
```

### Coverage below 78%
```bash
uv run pytest tests/unit/ --cov=aegis --cov-report=term-missing -q
# Look at "Missing" column for uncovered lines
```

### "ResourceWarning: unclosed socket" in tests
This is LangGraph leaving Unix domain sockets open on teardown. It is
harmless and suppressed by default via `filterwarnings = ["ignore::ResourceWarning"]`
in `pyproject.toml`. If you see it as a test failure, verify that line exists.

---

## Complete First-Run Walkthrough (Copy-Paste)

```bash
# 1. One-time setup
git clone <repo> aegis-pulse && cd aegis-pulse
cp .env.example .env
uv sync --all-extras

# 2. Start infrastructure
docker compose up -d

# 3. Wait for healthy (run a few times until all show "healthy"):
uv run aegis status

# 4. Phase 0 — Scrape signals from three sources
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 20
uv run aegis scrape --source hacker-news --limit 30
uv run aegis scrape --source github-trending --limit 20

# 5. Phase 1 — Verify data in DB
uv run aegis signals tail --limit 20

# 6. Phase 2 — Run agent intelligence pipeline
uv run aegis analyze --limit 50

# 7. See daily report
uv run aegis report daily --date 2026-05-04

# 8. Open monitoring UIs
# Grafana:    http://localhost:3001   (admin / aegis_dev_admin_pw)
# Prometheus: http://localhost:9091
# Jaeger:     http://localhost:16687
# MinIO:      http://localhost:9003   (aegis-dev-key / aegis-dev-secret-please-change)

# 9. Run tests
uv run pytest tests/unit/ -q

# 10. See DB in terminal
docker exec -it aegis-postgres psql -U aegis_app -d aegis \
  -c "SET app.current_tenant='00000000-0000-0000-0000-000000000001'; SELECT platform, count(*) FROM signals GROUP BY platform;"
```

That's it. AEGIS Pulse is fully operational.
