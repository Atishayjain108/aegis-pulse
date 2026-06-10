# AEGIS Pulse — Operations Runbook

> Last verified: Fri Jun  5 05:12:55 UTC 2026  
> System version: 0.3.0  
> Tests: 2300+ passing, 78%+ coverage  
> Status: All 13 services healthy, 7/7 data flow checks green

---

## What Is AEGIS Pulse?

AEGIS Pulse is an autonomous market intelligence engine. It watches 30+ data sources around the clock, uses a 10-node AI agent pipeline to score commercial opportunities, and tells you whether to act (ENTER), wait (HOLD), or skip (BLOCK) — with a plain-English explanation of why.

**You don't need to understand the code to use it daily.** This runbook covers everything you need.

---

## What It Does Every 15 Minutes (Automatically)

1. Scrapes Reddit, Hacker News, GitHub, Amazon, Google News, and 25 more sources
2. Removes duplicate signals (semantic deduplication)
3. Scores every signal for confidence (threshold: 0.85)
4. Clusters signals into trend candidates
5. Runs the 10-node AI agent pipeline: scout → sentinel → compliance → verdict
6. Stores the verdict + explanation in the database
7. Sends a notification (Discord / Telegram / ntfy, if configured)
8. Records the outcome for weekly self-improvement

---

## System Map

```
[30+ Adapters] → [TimescaleDB] → [Redis Stream]
                                        ↓
                            [10-node Agent Pipeline]
                            scout → historian → sourcer
                            → sentinel (ALWAYS runs)
                            → compliance (ALWAYS runs)
                            → finalize
                                        ↓
                            [Phase 3 ML Prediction]
                                        ↓
                            [Phase 4 Alert Pipeline]
                            → database → notifications → dashboard
                                        ↓
                            [Phase 9 Self-Improvement]
                            → weekly retrain → drift detection
```

### What Each Service Does

| Service | URL | What it is |
|---------|-----|-----------|
| Dashboard | http://localhost:8300 | Main command center — your daily interface |
| Execute API | http://localhost:8200 | Alert pipeline + kill switch |
| Predict API | http://localhost:8100 | ML inference engine |
| Grafana | http://localhost:3001 | System metrics and dashboards |
| Prometheus | http://localhost:9091 | Raw metrics (for debugging) |
| Jaeger | http://localhost:16687 | Request traces (for debugging) |
| MinIO | http://localhost:9003 | Object storage UI |
| Postgres | localhost:5433 | Main database (TimescaleDB) |
| Redis | localhost:6380 | Cache + inter-service streams |

---

## Morning Startup (Under 2 Minutes)

**Step 1 — Start everything**

```bash
cd ~/code/aegis-pulse
docker compose up -d
```

Wait about 15 seconds, then:

**Step 2 — Check all services are healthy**

```bash
bash scripts/aegis_status.sh
```

**Step 3 — Confirm data is flowing**

```bash
uv run python scripts/validate_data_flow.py
```

You should see:

```
============================================================
  AEGIS PULSE — DATA FLOW VALIDATION
  2026-06-05 04:53:59 UTC
============================================================

✅ 1. DB connection and signals exist
   2317 total signals, 71 in last 24h

✅ 2. Redis connection and stream exists
   Redis OK, stream has 21 entries

✅ 3. scrape_topic returns signals
✅ 4. Agent pipeline runs with sentinel+compliance
✅ 5. Phase 3 prediction runs under 500ms
✅ 6. Redis stream uses field name 'body'
✅ 7. Dashboard API responds

============================================================
  RESULT: 7/7 checks passed
============================================================
```

**Step 4 — Open the dashboard**

Go to http://localhost:8300

---

## The One Daily Command

```bash
uv run aegis daily
```

This does everything automatically: scrapes all sources, runs AI analysis, generates verdicts, stores results. Takes about 2-3 minutes.

---

## Running Scrapers

### Single source

```bash
# Hacker News (most reliable, always free)
uv run aegis scrape --source hacker-news --limit 50

# GitHub Trending repos
uv run aegis scrape --source github-trending --limit 30

# Google News (supports search query)
uv run aegis scrape --source google-news --query "dropshipping trends" --limit 30

# Reddit RSS (no API key needed)
uv run aegis scrape --source reddit-rss --subreddit Entrepreneur --limit 50
uv run aegis scrape --source reddit-rss --subreddit ecommerce --limit 50

# Bing News
uv run aegis scrape --source bing-news --query "print on demand" --limit 30

# Amazon Bestsellers
uv run aegis scrape --source amazon --limit 80
```

**Example output (hacker-news):**
```
adapter.run.end: seen=5 emitted=5 failed=0 elapsed_seconds=1.0
flushed 5 (total emitted: 5)
Done. Emitted 5 signals.
```

### All sources at once (Swarm)

```bash
# Run all 30+ adapters in 4 parallel waves
uv run aegis swarm run --limit 20

# Dry run — scrape but don't write to DB
uv run aegis swarm run --dry-run --limit 10

# Check which adapters are healthy
uv run aegis swarm agents
```

**Example swarm output:**
```
Wave 1  11 agents    62 signals    3841ms  (0 fail)
Wave 2  11 agents    55 signals    7543ms  (0 fail)
Wave 3  11 agents    23 signals   68489ms  (6 fail)
Wave 4   6 agents    28 signals   10344ms  (0 fail)

Market intelligence across 27 platforms. Top themes: AI, ecommerce, finance.
```

### Working adapters (verified 2026-06-05)

These return real signals with no API key:

| Source name | What it scrapes |
|------------|----------------|
| `hacker-news` | HN front page stories |
| `github-trending` | GitHub daily trending repos |
| `reddit-rss` | Any subreddit (pass `--subreddit`) |
| `amazon` | Amazon bestseller rankings |
| `google-news` | Google News (pass `--query`) |
| `bing-news` | Bing News (pass `--query`) |
| `google-trends` | Google Trends keywords |
| `techcrunch-rss` | TechCrunch articles |
| `reuters-rss` | Reuters headlines |
| `yahoo-finance-rss` | Yahoo Finance news |
| `reddit-finance` | r/personalfinance, r/investing |
| `reddit-ecommerce` | r/dropship, r/ecommerce |
| `economic-times` | Economic Times India |
| `moneycontrol` | Moneycontrol India |
| `nse-bse` | NSE/BSE market data |
| `nykaa` | Nykaa beauty trending |
| `meesho` | Meesho trending products |

**Broken adapters** (return 0 signals gracefully, no crash):
- `tiktok` — requires TikTok Ads auth
- `pinterest` — 403 blocked
- `nitter` — all public instances dead
- `instagram` — requires session cookie

---

## Topic Intelligence (Most Powerful Mode)

Give it one keyword, it expands into 8-12 search queries, scrapes all sources in parallel, deduplicates, and returns enriched trends.

```bash
# Basic research (no LLM needed, instant results)
uv run aegis topic "dropshipping" --limit 30 --no-llm

# With AI enrichment (needs Ollama or a cloud API key)
uv run aegis topic "print on demand" --limit 30

# Machine-readable JSON output
uv run aegis topic "trending gadgets" --limit 20 --no-llm --json-out

# Scrape only, skip analysis
uv run aegis topic "viral products" --no-analyze --limit 40
```

**Example output:**
```
Verdict   : BLOCK
Score     : 0.350   Confidence: 0.839   Priority: P3_HOUSEKEEPING
Halt      : vetoed_by_red_team   Duration: 977ms
Blocked by: red_team

Agent            Verdict     Score    Conf  Rationale
---------------- ---------- ------  ------  --------------------------------
compliance       proceed     1.000   0.700  No compliance signals detected.
geo_arbitrage    proceed     0.667   0.920  arbitrage_score=0
red_team         block       0.500   1.000  F3:no_24h_sustain, F7:scout_below
narrative        hold        0.470   0.980  sentiment=0.30 novelty=0.50
scout            hold        0.466   1.000  velocity_class=flat breakout=0.48
sentinel         block       0.312   1.000  saturation=0.31 volume_maturity=0
historian        proceed     0.000   0.200  analogues=0/0
```

---

## Running the Agent Pipeline

```bash
# Analyze last 30 signals (fast, no LLM key needed)
uv run aegis analyze --limit 30 --no-llm

# With LLM (richer reasoning text, needs Ollama or API key)
uv run aegis analyze --limit 20

# Filter by source
uv run aegis analyze --limit 30 --platform hacker_news --no-llm

# Machine-readable output
uv run aegis analyze --limit 20 --no-llm --json-out
```

**Example output:**
```
==============================================================
  AEGIS Pulse — Phase 2 Analysis
==============================================================
  Verdict   : BLOCK
  Score     : 0.256   Confidence: 0.599
  Priority  : P3_HOUSEKEEPING
  Halt      : scout_below_threshold
  Agents    : 7 ran   Duration: 714ms
  Blocked by: scout, red_team

  Agent            Verdict     Score    Conf  Rationale
  ---------------- ---------- ------  ------  --------------------------------
  compliance       proceed     1.000   0.700  No compliance signals detected.
  red_team         block       0.500   0.680  F3:no_24h_sustain, F7:scout_below
  narrative        hold        0.450   0.607  sentiment=0.30 novelty=0.50
  geo_arbitrage    hold        0.333   0.600  arbitrage_score=0
  scout            block       0.300   0.656  velocity_class=flat breakout=0.47
  sentinel         block       0.038   0.614  saturation=0.19 volume_maturity=0
  historian        proceed     0.000   0.200  analogues=0/0
==============================================================
```

### Understanding Verdicts

| Verdict | Meaning | What to do |
|---------|---------|-----------|
| **ENTER** | Strong signal, low risk, supplier found | Consider placing an order |
| **HOLD** | Moderate signal or missing supplier | Watch for a few days |
| **BLOCK** | High risk, compliance issue, or bot-coordinated | Do not act |

Every verdict includes:
- **explanation** — plain-English reason
- **primary_drivers** — top 3 features that drove the verdict
- **halt_reason** — which agent stopped the pipeline

### Pipeline Invariants (Verified 2026-06-05)

- SENTINEL always runs on every signal — no exceptions
- COMPLIANCE always runs after sentinel — no exceptions
- Same input always produces same output (deterministic in `--no-llm` mode)
- Every verdict has a causal explanation

---

## Best Daily Workflows

### Quick market scan (15 minutes)
```bash
uv run aegis swarm run --limit 15
uv run aegis analyze --limit 50 --no-llm
# Open http://localhost:8300 → Alerts tab
```

### Deep topic research (30 minutes)
```bash
uv run aegis topic "dropshipping 2026" --limit 30 --no-llm
uv run aegis topic "print on demand trends" --limit 30 --no-llm
uv run aegis topic "viral products" --limit 30 --no-llm
uv run aegis analyze --limit 100 --no-llm
```

### Geospatial arbitrage scan
```bash
# Find price gaps between markets
uv run aegis geo analyze "PROD-001" "Cotton T-Shirt" --category apparel --top-n 5

# Check live exchange rates (ECB, free)
uv run aegis geo fx

# Check import tariff for a product
uv run aegis geo tariff 610910 IN --value 100   # T-shirts → India

# Check shipping cost
uv run aegis geo shipping CN IN --weight 0.5
```

**Example geo output:**
```
  Geo Arbitrage: Cotton T-Shirt (TSHIRT-001)
  Pairs evaluated  : all 56 origin→destination combinations
  Viable (margin ≥ 5%): 3

  ORIGIN DEST   DEST PRICE     LANDED    MARGIN    SCORE  CARRIER
  ───────────────────────────────────────────────────────────────
  CN     US     $    24.99 $    12.82     48.7%    24.73  EMS/Postal
  CN     EU     $    29.99 $    13.96     53.5%    21.56  EMS/Postal
  IN     EU     $    29.99 $    18.64     37.9%    15.27  EMS/Postal

  TOP OPPORTUNITY
    CN → US  |  Buy CNY 54.19 ($8.00)  |  Sell $24.99  |  Margin 48.7%
```

### Full autonomous mode
```bash
# Start the scheduler (runs all jobs on a timer)
uv run python src/aegis/scheduler/autonomous.py
```

This runs automatically:
- Scrape 3 topics every 15 minutes
- Analyze signals every hour
- Drift check every 6 hours
- Health report every 30 minutes

---

## All API Endpoints

### Dashboard API (port 8300)

**GET /api/health** — Is the dashboard alive?
```bash
curl -s http://localhost:8300/api/health
```
```json
{"status": "ok"}
```

**GET /api/stats** — Aggregate system numbers
```bash
curl -s http://localhost:8300/api/stats | python3 -m json.tool
```
```json
{
    "total_signals": 2613,
    "signals_24h": 367,
    "signals_1h": 296,
    "total_alerts": 9,
    "alerts_24h": 7,
    "working_adapters": 5,
    "prediction_outcomes": 11,
    "avg_signal_confidence": 0.861,
    "pipeline_latency_s": 52.5,
    "verdict_distribution": {"HOLD": 2, "BLOCK": 3, "ENTER": 4}
}
```

**GET /api/signals/recent** — Latest scraped signals
```bash
curl -s http://localhost:8300/api/signals/recent | python3 -m json.tool | head -20
```

**GET /api/signals/platforms** — Breakdown by source
```bash
curl -s http://localhost:8300/api/signals/platforms | python3 -m json.tool
```
```json
[
    {"platform": "reddit", "count": 788, "avg_confidence": 0.9},
    {"platform": "hacker_news", "count": 634, "avg_confidence": 0.85},
    {"platform": "amazon", "count": 470, "avg_confidence": 0.8}
]
```

**GET /api/signals/velocity** — Hourly signal count (last 24h)
```bash
curl -s http://localhost:8300/api/signals/velocity | python3 -m json.tool | head -15
```

**GET /api/alerts/recent** — Last 20 alerts with explanations
```bash
curl -s http://localhost:8300/api/alerts/recent | python3 -m json.tool | head -30
```
Fields per alert: `trend_id`, `verdict`, `score`, `confidence`, `explanation`, `primary_drivers`, `created_at`

**GET /api/adapters/status** — All adapters with health status
```bash
curl -s http://localhost:8300/api/adapters/status | python3 -m json.tool | head -20
```
```json
[
    {"name": "bing_news", "status": "working", "signals_24h": 30},
    {"name": "google_news", "status": "working", "signals_24h": 25}
]
```

**GET /api/pipeline/live** — Real-time pipeline state
```bash
curl -s http://localhost:8300/api/pipeline/live | python3 -m json.tool
```
```json
{
    "stream_length": 23,
    "last_processed_at": "2026-06-05T04:56:37.606115+00:00",
    "killswitch_armed": true,
    "drain_running": false,
    "intake_running": false
}
```

**GET /api/execute/killswitch** — Kill switch state
```bash
curl -s http://localhost:8300/api/execute/killswitch | python3 -m json.tool
```
```json
{"tripped": false, "state": "ARMED"}
```

**POST /api/execute/killswitch/trip** — Halt all alert dispatch
```bash
curl -s -X POST http://localhost:8300/api/execute/killswitch/trip \
  -H "Content-Type: application/json" \
  -d '{"reason": "manual pause"}'
```

**POST /api/execute/killswitch/arm** — Resume alert dispatch
```bash
curl -s -X POST http://localhost:8300/api/execute/killswitch/arm \
  -H "Content-Type: application/json" \
  -d '{"reason": "all clear"}'
```

### Execute API (port 8200)

**GET /healthz**
```bash
curl -s http://localhost:8200/healthz
```
```json
{"status": "ok"}
```

**GET /stream** — SSE live stream of alerts
```bash
# Connect to live alert feed (Ctrl+C to stop)
curl -s http://localhost:8200/stream
```

### Predict API (port 8100)

**GET /healthz**
```bash
curl -s http://localhost:8100/healthz
```
```json
{"status": "ok", "ts": "2026-06-05T05:10:34.365861+00:00"}
```

---

## Kill Switch

The kill switch immediately halts all outbound alert dispatch (Discord, Telegram, ntfy) without stopping scraping or analysis.

```bash
# Check current state
AEGIS_REDIS_URL=redis://127.0.0.1:6380/0 \
  uv run --package aegis-execute aegis-execute killswitch state
# → ARMED  (normal)  or  TRIPPED  (halted)

# Halt all notifications
AEGIS_REDIS_URL=redis://127.0.0.1:6380/0 \
  uv run --package aegis-execute aegis-execute killswitch trip \
  --reason "manual pause"

# Resume notifications
AEGIS_REDIS_URL=redis://127.0.0.1:6380/0 \
  uv run --package aegis-execute aegis-execute killswitch arm \
  --reason "all clear"
```

Or via the dashboard API (simpler):
```bash
curl -s -X POST http://localhost:8300/api/execute/killswitch/trip \
  -H "Content-Type: application/json" -d '{"reason": "test"}'

curl -s -X POST http://localhost:8300/api/execute/killswitch/arm \
  -H "Content-Type: application/json" -d '{"reason": "done"}'
```

---

## Phase-by-Phase CLI Reference

### Compliance Check (Phase 8)

```bash
# Check if a product is safe to sell
uv run aegis comply check \
  --title "Cotton T-Shirt" --category apparel --price 15.00 \
  --jurisdiction US

# List trademark brands the system knows about
uv run aegis comply brands

# List loaded compliance rules
uv run aegis comply rules
```

**Example output:**
```
CLEAR   risk=0.000  confidence=0.70
trend_id : cli-check
```

### Geo Arbitrage (Phase 7)

```bash
# Find cross-market price gaps
uv run aegis geo analyze "SKU-001" "Product Name" --category apparel --top-n 5

# Live ECB exchange rates (no API key)
uv run aegis geo fx

# WTO tariff lookup (HS code, destination country, value in USD)
uv run aegis geo tariff 610910 IN --value 100   # T-shirts → India
uv run aegis geo tariff 851712 US --value 500   # smartphones → US
uv run aegis geo tariff 640411 EU --value 80    # shoes → EU

# Shipping cost (origin country, dest country, weight in kg)
uv run aegis geo shipping CN US --weight 1.0
uv run aegis geo shipping IN EU --weight 0.5

# List all 8 supported regions with market size data
uv run aegis geo regions
```

### Self-Evolution (Phase 9)

```bash
# See current model state
uv run aegis evolve status
```

```
────────────────────────────────────────────────────────────
  AEGIS Phase 9 — Evolve Status
────────────────────────────────────────────────────────────
  Champion AUC       : 0.5
  Last retrain       : None
  Drift score        : None
  RL policy weights  :
    cost_based              : 0.2500
    demand_based            : 0.3500
    inventory_based         : 0.2000
    competitor_based        : 0.2000
────────────────────────────────────────────────────────────
```

```bash
# Check for feature drift
uv run aegis evolve drift
# → Drift score : 0.0000  |  Is drifted  : False

# Count recorded trade outcomes (last 7 days)
uv run aegis evolve outcomes --days 7

# Trigger a manual model retrain
uv run aegis evolve retrain

# Record a trade outcome (feeds the learning loop)
uv run aegis evolve record \
  --plan-id "plan-001" --trend-id "trend-001" \
  --score 0.82 --confidence 0.91 \
  --roi 45.0 --pnl 450.0 --units 10 --status successful
```

### Data Lake (Phase 10)

```bash
# Check storage health
uv run aegis datalake doctor

# List registered tables
uv run aegis datalake list-tables

# Ingest signals from DB into the lake
uv run aegis datalake ingest-postgres-signals \
  --dsn postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis

# Build analytics layers
uv run aegis datalake build-silver --date today
uv run aegis datalake build-gold --date today

# Run a SQL query across all data
uv run aegis datalake query "SELECT platform, COUNT(*) FROM signals GROUP BY 1"
```

### LLM Gateway (Phase 11)

```bash
# Check all provider health (Ollama, Groq, OpenRouter, Gemini)
uv run aegis llm health

# Run a completion (falls back through the provider chain)
uv run aegis llm complete "Summarize AI chip trends in 3 bullets"

# List available models
uv run aegis llm models

# Pull an Ollama model
uv run aegis llm pull llama3.2:3b

# See estimated cost so far
uv run aegis llm cost
```

### Disaster Recovery (Phase 15)

```bash
# Check backup health and SLA status
uv run aegis dr status
uv run aegis dr health

# Run a backup
uv run aegis dr backup

# Test restore (dry run — safe, touches nothing)
uv run aegis dr drill --dry-run

# Print the recovery procedure for a failure scenario
uv run aegis dr runbook pg_corruption
uv run aegis dr runbook redis_oom
# Available: pg_corruption, redis_oom, disk_full, docker_dead,
#            laptop_stolen, network_outage, wsl_crash
```

---

## Database Queries

```bash
# Signals by platform (how much data you have per source)
docker compose exec -T postgres psql -U aegis_app -d aegis \
  -c "SELECT platform, COUNT(*) as count, MAX(created_at) as latest
      FROM signals GROUP BY platform ORDER BY count DESC"
```

```
    platform     | count |            latest
-----------------+-------+-------------------------------
 reddit          |   788 | 2026-06-05 04:56:26.813121+00
 hacker_news     |   634 | 2026-06-05 04:56:20.823454+00
 amazon          |   470 | 2026-05-29 03:39:23.283423+00
 google_news     |   454 | 2026-06-05 04:56:24.887041+00
 github_trending |   202 | 2026-06-05 04:56:26.831501+00
 bing_news       |    65 | 2026-06-05 04:56:26.371662+00
```

```bash
# Recent alerts with scores
docker compose exec -T postgres psql -U aegis_app -d aegis \
  -c "SELECT verdict, ROUND(score::numeric,3) as score,
             ROUND(confidence::numeric,3) as conf, created_at
      FROM alerts ORDER BY created_at DESC LIMIT 5"
```

```
 verdict | score | conf  |          created_at
---------+-------+-------+-------------------------------
 BLOCK   | 0.256 | 0.599 | 2026-06-05 04:57:09
 BLOCK   | 0.350 | 0.839 | 2026-06-05 04:56:59
 HOLD    | 0.366 | 0.850 | 2026-06-04 18:29:43
 ENTER   | 0.920 | 0.850 | 2026-06-04 14:47:09
 ENTER   | 0.880 | 0.820 | 2026-06-04 14:29:03
```

```bash
# Signals from the last hour
docker compose exec -T postgres psql -U aegis_app -d aegis \
  -c "SELECT platform, COUNT(*) FROM signals
      WHERE created_at > NOW() - INTERVAL '1 hour'
      GROUP BY platform"

# Redis stream length
docker compose exec -T redis redis-cli XLEN aegis:phase2:graph_results
```

---

## Daily Verification Script

```bash
bash scripts/daily_verify.sh
```

Runs 7 checks and tells you if anything needs attention:

```
=== AEGIS Pulse Daily Verification ===
[Data flow]        ✅ PASS
[Dashboard API]    ✅ PASS
[Execute API]      ✅ PASS
[Predict API]      ✅ PASS
[DB has signals]   ✅ PASS
[Redis stream]     ✅ PASS
[Ruff clean]       ✅ PASS

=== Results: 7 passed, 0 failed ===

✅ System healthy. Run: uv run aegis daily
```

---

## Troubleshooting

### 0 signals returned from a scraper

```bash
# Test the adapter directly
uv run python -c "
import asyncio
from aegis.scrape.sources.hacker_news import scrape
async def t():
    sigs = await scrape(limit=5)
    print(f'Got {len(sigs)} signals')
asyncio.run(t())
"

# Check if the container can reach external URLs
docker compose exec -T dashboard \
  curl -s https://hn.algolia.com/api/v1/search_by_date?tags=story | head -3
```

### Agent pipeline produces no verdicts

```bash
# Test the pipeline directly with a synthetic signal
uv run python -c "
import asyncio
from aegis.agents.runner import run_trend
from aegis.agents.schemas import TrendCandidate

async def t():
    tc = TrendCandidate(
        trend_id='debug-001', title='Debug Test',
        signal_count=50, unique_authors=15,
        platforms=['hacker_news'],
        velocity_1h=2.0, velocity_6h=1.0, velocity_24h=0.5,
        sentiment=0.6, commercial_intent=0.7,
        novelty=0.5, coordination_risk=0.1,
    )
    r = await run_trend(tc, use_llm=False)
    print(f'verdict={r.final_verdict} score={r.final_score:.3f}')
    print(f'agents: {list(r.decisions.keys())}')

asyncio.run(t())
"
```

### No alerts appearing in the database

```bash
# Check if the drain worker is running
docker compose logs --tail=30 execute-drain

# Check what's in the Redis stream
docker compose exec -T redis redis-cli -p 6380 \
  XRANGE aegis:phase2:graph_results - + COUNT 3

# Check the kill switch
docker compose exec -T redis redis-cli GET aegis:execute:killswitch
# Should return "ARMED" or nil (nil = armed)
```

### Dashboard not loading

```bash
docker compose restart dashboard
sleep 10
curl -s http://localhost:8300/api/stats
docker compose logs --tail=20 dashboard
```

### A service is crashlooping

```bash
# Check restart counts across all services
docker inspect $(docker ps -q --filter name=aegis) \
  --format '{{.Name}} restarts={{.RestartCount}}' 2>/dev/null | grep -v "restarts=0"

# Read logs for the affected service
docker compose logs --tail=50 <service-name>

# Restart a specific service
docker compose restart <service-name>
```

### Test coverage dropped below 78%

```bash
uv run python -m pytest tests/unit/ -p no:hypothesis \
  --cov=src/aegis --cov-report=term-missing 2>&1 | grep -E "TOTAL|FAIL"
```

---

## Lifecycle Commands

```bash
uv run aegis up               # start all services (detached)
uv run aegis up --build       # rebuild images first
uv run aegis down             # stop (data preserved)
uv run aegis down --volumes   # stop + wipe all data (irreversible)
uv run aegis tail             # follow all service logs
uv run aegis tail dashboard   # follow one service's logs
uv run aegis doctor           # health check
```

---

## Autonomous Operation

### Start the scheduler

```bash
# Foreground (development — you see all output)
uv run python src/aegis/scheduler/autonomous.py

# Background (production)
nohup uv run python src/aegis/scheduler/autonomous.py \
  > ~/.aegis/scheduler.log 2>&1 &
echo $! > ~/.aegis/scheduler.pid
```

### Job schedule

| Job | Interval | What it does |
|-----|----------|-------------|
| scrape_topics | Every 15 min | Scrapes 3 monitored topics |
| run_analysis | Every 1 hour | Agent pipeline on last 30 signals |
| drift_check | Every 6 hours | Detects feature drift |
| health_report | Every 30 min | Logs system health |

### Change what topics are monitored

Edit `src/aegis/scheduler/autonomous.py` → `TOPICS_TO_MONITOR` list.

---

## How the System Gets Smarter

Every trade you record feeds the learning loop:

1. `uv run aegis evolve record ...` → stored in `prediction_outcomes` table
2. Online learner (river) updates after every outcome — accuracy improves immediately
3. Weekly retrain (Sunday 2am UTC) — full model retraining with Optuna hyperparameter search
4. Drift detection (every 6h) — rolls back if new model is worse than champion
5. Champion promotion gate — new model must beat champion by 2% AUC to be promoted

```bash
# Check current accuracy
uv run python -c "
from aegis.predict.online_learner import OnlineLearner
l = OnlineLearner()
print(f'Outcomes learned: {l.n_learned}')
print(f'Current accuracy: {l.accuracy:.1%}')
"
```

---

## Performance Benchmarks (Verified 2026-06-05)

| Metric | Measured | Target |
|--------|----------|--------|
| Full pipeline (scrape → alert) | 52.5 seconds | < 120 seconds |
| ML prediction p99 | 3.1 ms | < 500 ms |
| Agent throughput | 7.5 verdicts/sec | — |
| Sentinel invariant (always runs) | 100% | 100% |
| Causal explanation coverage | 100% | 100% |
| Data flow checks | 7/7 | 7/7 |
| Test coverage | 78.25% | ≥ 78% |
| Signals in database | 2,613 | — |

---

## Environment Variables

Set these in `.env` at the project root.

| Variable | Default | What it controls |
|----------|---------|-----------------|
| `AEGIS_ENV` | `dev` | Environment name (dev/staging/prod/test) |
| `AEGIS_PG_DSN` | postgres://...@localhost:5433/aegis | Database connection |
| `AEGIS_REDIS_URL` | redis://localhost:6380/0 | Redis connection |
| `AEGIS_DEFAULT_TENANT_ID` | 00000000-...0001 | Default tenant UUID |
| `AEGIS_EXECUTE_MODE` | `advisory` | advisory/staging/live |
| `AEGIS_DISABLE_OLLAMA` | — | Set to `1` to skip local LLM |
| `AEGIS_GROQ_API_KEY` | — | Groq cloud LLM (free tier available) |
| `AEGIS_OPENROUTER_API_KEY` | — | OpenRouter LLM fallback |
| `AEGIS_GEMINI_API_KEY` | — | Gemini LLM fallback |
| `AEGIS_EXECUTE_DISCORD_WEBHOOK_URL` | — | Discord notifications |
| `AEGIS_EXECUTE_TELEGRAM_BOT_TOKEN` | — | Telegram notifications |
| `AEGIS_EXECUTE_NTFY_URL` | — | ntfy push notifications |
| `AEGIS_EVOLVE_MIN_OUTCOMES_FOR_RETRAIN` | `100` | Minimum outcomes before weekly retrain |
| `AEGIS_EVOLVE_DRIFT_THRESHOLD` | `0.15` | KS-distance score that triggers drift alert |

---

## Quick Reference Card

```
MORNING:   docker compose up -d && bash scripts/daily_verify.sh
SCRAPE:    uv run aegis daily
RESEARCH:  uv run aegis topic "your topic" --limit 30 --no-llm
ANALYZE:   uv run aegis analyze --limit 50 --no-llm
SWARM:     uv run aegis swarm run --limit 20
VALIDATE:  uv run python scripts/validate_data_flow.py

DASHBOARD: http://localhost:8300
GRAFANA:   http://localhost:3001  (admin / aegis_dev_admin_pw)
JAEGER:    http://localhost:16687

ALERTS:    curl -s http://localhost:8300/api/alerts/recent | python3 -m json.tool
PIPELINE:  curl -s http://localhost:8300/api/pipeline/live | python3 -m json.tool
KS STATE:  curl -s http://localhost:8300/api/execute/killswitch | python3 -m json.tool
KS TRIP:   curl -s -X POST http://localhost:8300/api/execute/killswitch/trip -H "Content-Type: application/json" -d '{"reason":"pause"}'
KS ARM:    curl -s -X POST http://localhost:8300/api/execute/killswitch/arm -H "Content-Type: application/json" -d '{"reason":"resume"}'

EVOLVE:    uv run aegis evolve status
STOP:      docker compose down
```
