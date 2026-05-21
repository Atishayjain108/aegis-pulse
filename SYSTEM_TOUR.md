# AEGIS Pulse — Complete Architect's Tour

---

## 1. The Codebase Map (What are these files?)

Think of the project as a **factory assembly line** divided into six stations. Each station hands its output to the next; the Dashboard watches all of them simultaneously.

```
aegis-pulse/
├── src/aegis/                    ← The main brain (Phases 0–3 + Dashboard)
│   ├── __init__.py               ← Stitches Phase 4's code into the same namespace
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
│   │   └── (+ 7 others)         ← TikTok/Pinterest/Nitter are broken; YouTube/Reddit need API keys
│   │
│   ├── db/                       ← PHASE 1: Saves everything to the database
│   ├── cache/                    ← PHASE 1: Fast Redis layer (dedup, shared memory)
│   │
│   ├── agents/                   ← PHASE 2: The 10-agent intelligence council
│   │   ├── graph.py              ← Draws the wiring diagram connecting all 10 agents
│   │   ├── runner.py             ← The "start the council" function; also ships results to Phase 4
│   │   ├── supervisor.py         ← The final vote counter — tallies agents, picks a verdict
│   │   ├── schemas.py            ← Defines what a "TrendCandidate" and a "GraphResult" look like
│   │   ├── nodes/scout.py        ← Agent 1: First look — is this trend worth investigating?
│   │   ├── nodes/sentinel.py     ← Agent 2: Risk watchdog — is this trend about to collapse?
│   │   ├── nodes/compliance.py   ← Agent 3: Legal/policy checker — can we even touch this?
│   │   ├── nodes/red_team.py     ← Agent 4: Adversarial devil's advocate — tries to break the case
│   │   ├── nodes/hedge.py        ← Agent 5: Portfolio-level final veto
│   │   └── nodes/(5 others)      ← GeoArbitrage, Narrative, Historian, Sourcer, Auditor
│   │
│   ├── predict/                  ← PHASE 3: The ML prediction engine
│   │   ├── inference/runner.py   ← The single entry point — feeds features in, gets predictions out
│   │   ├── features/builder.py   ← Converts raw DB signals into numeric feature vectors
│   │   ├── models/               ← Heuristic model (always works) + optional neural models (PatchTST etc.)
│   │   ├── causal/               ← "What actually caused this trend?" attribution
│   │   ├── rl/                   ← Kelly Criterion position-sizing policy
│   │   └── serving/              ← FastAPI server exposing /predict endpoint on port 8100
│   │
│   ├── agents_phase3_glue/
│   │   └── bridge.py             ← Translator: converts Phase 3 ML output into Phase 2 agent language
│   │
│   └── dashboard/                ← DASHBOARD: Unified Command Center (port 8300)
│       ├── app.py                ← FastAPI backend — aggregates Postgres, Redis, Phase3, Phase4, Docker
│       ├── cli.py                ← `aegis dashboard serve` entry point
│       └── static/index.html     ← Dark-theme SPA: signal stats, agent intelligence, SSE live feed,
│                                    streaming ops console (runs any aegis CLI command live)
│
├── aegis-phase4/                 ← PHASE 4: The alert factory (separate sub-package)
│   └── src/aegis/execute/
│       ├── pipeline.py           ← The 7-stage alert assembly line
│       ├── workers/              ← IntakeWorker reads Redis; DrainWorker sends notifications
│       ├── killswitch/           ← Emergency stop — one Redis key halts all outbound alerts
│       ├── notifiers/            ← Discord / ntfy / Telegram senders
│       ├── store/repository.py   ← Saves alerts to the database
│       ├── api/                  ← FastAPI server on port 8200 (dashboard, SSE stream, /healthz)
│       └── bus/                  ← In-memory event bus — fans alerts out to live SSE connections
│
├── db/migrations/                ← SQL files that create all the database tables
├── docker-compose.yml            ← Describes all 11 Docker containers and how they connect
└── config/                       ← Prometheus + Grafana configs (pre-wired dashboards)
```

---

## 2. The Live Data Flow (What is actually happening?)

Here is the exact journey of one piece of data — say, a trending GitHub repo about an AI tool.

### Step 1 — Scrape (Phase 0)
You run `aegis scrape --source github-trending`. The `github_trending.py` adapter fetches GitHub's trending page, extracts the repo name, description, and star count, and wraps it in a `ProductSignal` object. That object is written to the `signals` table in TimescaleDB (port 5433). A SHA-256 content hash is computed first — if this exact signal already exists, it's skipped silently.

### Step 2 — Aggregate into a Trend Candidate (Phase 1 → Phase 2 boundary)
When you run `aegis analyze`, the CLI reads recent `signals` rows from the DB and groups them by topic into a `TrendCandidate`. A trend candidate is a bundle: trend ID, title, signal count, how many unique authors posted about it, velocity over 1h/6h/24h windows, sentiment score, commercial intent score, etc.

### Step 3 — The 10-Agent Council (Phase 2)
The `run_trend()` function in `runner.py` takes that `TrendCandidate` and runs it through a LangGraph pipeline — think of it as a decision tree where each node is an expert:

```
START
├── SCOUT       → "Is the velocity strong enough to bother? Score it."
├── GEO_ARBITRAGE → "Is there a geographic price gap to exploit?"
└── NARRATIVE   → "What story is the internet telling about this?"
        ↓  (all three run in parallel, then join)
   HISTORIAN    → "Have we seen this trend before? Check ChromaDB memory."
        ↓
   SOURCER      → "Can we actually source/supply this product?" (skipped if Scout scored too low)
        ↓
   AUDITOR      → "Is the supplier legit?" (skipped if Sourcer found nothing)
        ↓
   SENTINEL     → "Is this trend about to peak and crash?" (calls Phase 3 ML here)
        ↓
   COMPLIANCE   → "Any legal/policy red flags?" (BLOCK here = pipeline stops)
        ↓
   RED_TEAM     → "Devil's advocate — what could go wrong?"
        ↓
   HEDGE        → "Given our existing portfolio, does this still make sense?"
        ↓
   FINALIZE     → Tallies votes → emits one verdict: proceed / hold / block / escalate
```

SCOUT and SENTINEL both call into Phase 3 (the ML engine) via the bridge in `agents_phase3_glue/bridge.py`. The bridge translates ML predictions into the agent's own language without the agents needing to know anything about ML.

### Step 4 — ML Prediction (Phase 3)
When SCOUT or SENTINEL calls the bridge, `InferenceRunner.run()` fires:
1. Pulls the raw signals from DB for this trend, converts them to a 20-feature numeric vector (`FeatureWindow`).
2. Runs the **heuristic model** — always works, no GPU needed, sub-12ms. Produces probability estimates: `p_breakout` (will it explode?) and `p_decline` (will it crash?) at 4 time horizons: 1h, 6h, 24h, 72h.
3. Optionally runs neural models (PatchTST, TimesNet, etc.) if PyTorch is installed — they can only *reduce* confidence, never flip the verdict.
4. Runs causal attribution — "which of the 20 features is actually driving this prediction?"
5. Returns an `InferenceResult` back to the bridge, which translates it to an `AgentDecision`.

### Step 5 — Publish to Phase 4 (Phase 2 → Phase 4 handoff)
Once the council finishes and `run_trend()` returns a `GraphResult`, the runner maps the verdict vocabulary (`proceed → ENTER`, `hold → HOLD`, `block → BLOCK`, `escalate → HOLD`) and does an `XADD` — writes a JSON message to the Redis Stream named `aegis:phase2:graph_results`.

### Step 6 — Alert Pipeline (Phase 4)
The Phase 4 `IntakeWorker` (running in the `execute-drain` container) watches that Redis Stream via `XREADGROUP`. When it sees a new message:
1. **Compose** — builds an `Alert` object with title, verdict, score, confidence, priority.
2. **Kelly sizing** — computes a recommended position size (e.g. "buy 12 units") using fractional-Kelly. Advisory only — no real orders placed.
3. **Risk gates** — blocks the alert if expected margin < $1.00 or confidence < 40%.
4. **Dedup** — drops the alert if the same trend already fired an alert within the last hour.
5. **Outbox** — writes the alert to the `alerts` table and an `alert_outbox` row. This is now durable — even if the notifier crashes, the row survives.
6. **SSE fan-out** — pushes the alert to the in-memory event bus so anyone watching `http://localhost:8200/stream` sees it live.
7. **DrainWorker** — a separate loop reads `alert_outbox`, calls your configured notifiers (Discord, Telegram, ntfy), marks the row done.

---

## 3. "The Single Command" (How do I turn the key?)

The system already has everything needed. The single command is:

```bash
docker compose up -d
```

Run it from the repo root (`/home/atishayjain/code/aegis-pulse`).

**What happens when you press Enter:**

Docker spins up 11 containers in dependency order. You'll see output like this scroll past:
```
[+] Running 11/11
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
```

Postgres starts first (takes ~30s cold-start). The predict, execute, and dashboard services wait for Postgres and Redis to pass their health checks before starting. **Total cold-start time: approximately 90–120 seconds.**

Once the stack is up, open the Command Center in your browser:

```
http://localhost:8300
```

This is the unified dashboard — it shows live signal counts, agent verdicts, system health for all services, and a streaming ops console where you can run any `aegis` CLI command directly from the browser.

Then feed data in and run the intelligence cycle. **The easiest path** — one command that scrapes 7 sources, runs analysis, and sends alerts:

```bash
uv run aegis daily
```

Or run each step individually for more control:

```bash
# Scrape signals from 7 no-API-key sources (~3 minutes total)
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 50
uv run aegis scrape --source reddit-rss --subreddit Entrepreneur --limit 50
uv run aegis scrape --source hacker-news --limit 50
uv run aegis scrape --source github-trending --limit 30
uv run aegis scrape --source google-news --query "tech trends" --limit 30
uv run aegis scrape --source bing-news --query "market opportunity" --limit 30
uv run aegis scrape --source amazon --limit 80

# Run the full intelligence + alert cycle on what was just scraped
uv run aegis analyze --limit 20
```

`aegis analyze` is the command that triggers Phases 2, 3, and 4 in one shot — it reads signals from the DB, runs every trend through the 10-agent council + ML engine, and publishes results to Phase 4.

For a targeted deep-dive on a specific keyword (expands the topic into related queries, scrapes 6+ sources in parallel, deduplicates, runs full analysis):

```bash
uv run aegis topic "AI chips"
uv run aegis topic "bitcoin" --limit 50
uv run aegis topic "NVIDIA" --no-llm --json-out   # heuristic-only, no API keys needed
```

---

## 4. How to "See" the System (Where are my dials and gauges?)

### Web UIs (open in browser)

| What you want to see | URL | Login |
|---|---|---|
| **AEGIS Command Center** — unified dashboard for all phases | `http://localhost:8300` | none |
| **Grafana dashboards** — charts, throughput, latency | `http://localhost:3001` | `admin` / `aegis_dev_admin_pw` |
| **Phase 3 ML API** — raw prediction endpoint | `http://localhost:8100/healthz` | none |
| **Phase 4 live alert dashboard** — SSE stream as a page | `http://localhost:8200/dashboard/` | none |
| **Phase 4 API health** | `http://localhost:8200/healthz` | none |
| **Prometheus raw metrics** | `http://localhost:9091` | none |
| **Jaeger distributed traces** — see every agent hop | `http://localhost:16687` | none |
| **MinIO object store** — saved model snapshots | `http://localhost:9003` | `aegis-dev-key` / `aegis-dev-secret-please-change` |

**The Command Center at `:8300` is the best starting point.** It shows:
- Live signal ingestion counts per platform
- Most recent agent verdicts (ENTER / HOLD / BLOCK) with per-agent reasoning breakdown
- System health for all 11 services (green/red tiles)
- A streaming ops console — type any `aegis` command in the browser and see its live output

### Checking scraped data

```bash
# See the last 20 signals that were scraped
uv run aegis signals tail --limit 20

# Filter by platform
uv run aegis signals tail --platform hacker_news --limit 10
uv run aegis signals tail --platform github --limit 10

# Or query the DB directly
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis \
  -c "SELECT platform, title, created_at FROM signals ORDER BY created_at DESC LIMIT 20;"
```

### Checking alerts that were generated

```bash
# Tail recent alerts via CLI
uv run --package aegis-execute aegis-execute tail \
  --tenant 00000000-0000-0000-0000-000000000001 --limit 20

# Or watch the live SSE stream in your terminal
curl -N http://localhost:8200/stream
```

### Finding errors

Every component logs structured JSON. The fastest way to find errors:

```bash
# See all container logs, filtered to errors only
docker compose logs --since 10m 2>&1 | grep '"level":"error"'

# Watch a specific service live
docker compose logs -f aegis-dashboard
docker compose logs -f aegis-execute-drain
docker compose logs -f aegis-predict
docker compose logs -f aegis-postgres
```

The log field `"event"` tells you what happened. The field `"trend_id"` lets you trace one trend across all logs. The field `"correlation_id"` connects Phase 2 output to Phase 4 alerts.

---

## 5. What to Expect (The baseline behavior)

### Healthy system fingerprint

- `docker compose ps` shows all 11 containers as **healthy** (not just "running").
- `curl -s http://localhost:8300/health` returns `{"status":"ok"}` (Command Center).
- `curl -s http://localhost:8200/healthz` returns `{"status":"ok"}` (Phase 4 execute-api).
- `curl -s http://localhost:8100/healthz` returns `{"status":"ok"}` (Phase 3 predict).
- Grafana at `:3001` shows Prometheus metrics ticking (scrape interval = 15s).
- The Command Center at `:8300` shows all service tiles green.

### Throughput and timing

| Operation | Expected time |
|---|---|
| Cold Docker startup | 90–120 seconds |
| `aegis scrape github-trending --limit 30` | ~5 seconds |
| `aegis scrape hacker-news --limit 50` | ~3 seconds |
| `aegis scrape reddit-rss --limit 50` | ~8 seconds |
| `aegis scrape google-news --query "..." --limit 30` | ~4 seconds |
| `aegis scrape bing-news --query "..." --limit 30` | ~4 seconds |
| `aegis scrape amazon --limit 80` | ~15 seconds |
| `aegis daily` (all 7 sources + analysis) | 3–6 minutes |
| `aegis analyze --limit 20` (20 trends, no LLM) | 30–90 seconds |
| One trend through the ML inference pipeline | < 12ms (heuristic path) |
| Alert appearing in SSE stream after `analyze` | < 2 seconds |

### Normal warnings you can safely ignore

- `content_hash mismatch — signal skipped` — a duplicate was caught by the dedup hash. Expected on repeated scrapes.
- `ResourceWarning: ... Unix domain socket` — LangGraph leaves sockets open at teardown. Suppressed in test config; harmless in dev.
- `DeprecationWarning: google.protobuf` — a Python 3.12 compatibility issue in the optional `onnx` dependency. The system catches and suppresses it; the heuristic model takes over.
- `AEGIS_DISABLE_OLLAMA=1` in logs — Ollama is intentionally disabled in containers. The LLM fallback chain moves on to Groq/OpenRouter/Gemini; if none are configured, agents produce heuristic-only verdicts (fully functional).
- `runner.stream_publish_failed` — Redis was momentarily unavailable. Phase 4 won't get that one result, but the agent run itself still completes and is logged.

### What a full healthy cycle looks like

After `docker compose up -d` settles, run `uv run aegis daily` (or the manual scrape sequence + `aegis analyze --limit 20`). You should see:

- **~350 signals** in the `signals` table across 7 platforms (reddit-rss ×2, hacker-news, github-trending, google-news, bing-news, amazon).
- **~20 `GraphResult` entries** logged, each with a verdict (`proceed` / `hold` / `block`).
- **The Command Center at `:8300`** showing live signal counts, agent verdict cards, and all services green.
- **Phase 4 alerts** visible at `http://localhost:8200/dashboard/` — typically 8–15 ENTER/HOLD alerts per 20 trends analyzed (the rest are blocked by compliance, risk gates, or the killswitch dedup).
- **Prometheus at `:9091`** showing `aegis_alerts_total` counter incrementing.

The system is designed to produce **at least one result for every trend, always** — even if every optional component (LLM, neural models, Redis snapshot) fails, the heuristic floor guarantees a deterministic verdict comes out the other end.
