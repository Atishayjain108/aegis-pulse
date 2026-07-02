# PROJECT OMEGA — AEGIS OPERATOR MODE

> Live operation runbook. Everything here is grounded in the **actual** repository
> (`docker-compose.yml`, `src/aegis/scheduler/autonomous.py`, `db/migrations/`,
> `src/aegis/cli/main.py`, `src/aegis/dashboard/app.py`). Where a capability does
> not yet exist in code it is marked **`UNVERIFIED`** / **`INSUFFICIENT_EVIDENCE`**
> and listed as a build task — never presented as if it already works.

Date authored: 2026-06-18. DB DSN: `postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis`.

---

## 0. THE ONE-SHOT "EVERYTHING UP" SEQUENCE

Run these in order from the repo root (`/home/atishayjain/code/aegis-pulse`). This
brings up **backend infra + workers + scheduler + dashboard + observability + local LLM**.

```bash
# 0. Dependencies (one-time / after pulls)
uv sync --all-packages --all-extras

# 1. Bring up the FULL stack — every profile enabled
#    Default services + autonomous scheduler + dashboard + logging + tracing + local LLM
docker compose \
  --profile autonomous \
  --profile dashboard \
  --profile logging \
  --profile tracing \
  --profile local-llm \
  up -d

# 2. Wait for Postgres, then apply ALL migrations (0001 → 0023)
docker compose exec postgres pg_isready -U aegis_app -d aegis
uv run alembic upgrade head        # == `uv run aegis migrate`

# 3. Confirm every container is healthy
docker compose ps
uv run aegis status

# 4. Confirm the scheduler is alive (14 jobs) — see its logs
docker compose logs --tail=40 autonomous

# 5. Open the operator dashboard
#    http://localhost:8300
```

> **Profiles that exist** (from `docker-compose.yml`): `autonomous`, `dashboard`,
> `logging` (loki+promtail), `orchestration` (prefect), `local-llm` (ollama+ollama-init),
> `llm-proxy` (litellm), `tracing` (jaeger). Default (no profile) = postgres, redis,
> minio, flaresolverr, predict, execute-api, execute-drain, prometheus, grafana, langfuse.

If you prefer to run the **dashboard on the host** (recommended per project memory —
the Docker dashboard profile exists but host mode is the supported path):

```bash
# instead of --profile dashboard, run it on the host:
uv run aegis dashboard serve     # → http://localhost:8300
```

### Web UIs after startup

| URL | Component |
|-----|-----------|
| http://localhost:8300 | AEGIS Command Center (operator dashboard) |
| http://localhost:8200/dashboard/ | Phase 4 execute dashboard / killswitch |
| http://localhost:8100/healthz | Phase 3 predict server |
| http://localhost:3001 | Grafana (admin / aegis_dev_admin_pw) |
| http://localhost:9091 | Prometheus |
| http://localhost:16687 | Jaeger traces |
| http://localhost:3100 | Loki (query via Grafana Explore) |
| http://localhost:9003 | MinIO console |

---

## PART 1 — FULL STARTUP AUDIT

### 1.1 Startup sequence (dependency order)

1. `postgres` (5433) → `redis` (6380) → `minio` (9002/9003) — stateful core
2. `flaresolverr` (8191) — scrape bypass
3. `predict` (8100) — Phase 3 inference
4. `execute-api` (8200) + `execute-drain` — Phase 4 alerts/outbox
5. `autonomous` — APScheduler loop (Phase A/B/C learning + scrape/analyze)
6. `dashboard` (8300) — operator UI
7. Observability: `prometheus`, `grafana`, `jaeger`, `loki`, `promtail`, `langfuse`
8. `ollama` (11434) + `ollama-init` (one-shot model pull)

### 1.2 Health checks (exact commands)

```bash
docker compose ps                                   # container health column
uv run aegis status                                 # cross-service roll-up
uv run aegis doctor                                 # config + connectivity
curl -s http://localhost:8100/healthz               # predict
curl -s http://localhost:8200/healthz               # execute-api
curl -s http://localhost:8200/readyz                # execute-api ready
curl -s http://localhost:8300/healthz               # dashboard
curl -s http://localhost:8300/api/system | jq .     # full cross-phase snapshot
curl -s http://localhost:8300/api/health/streams|jq # Redis stream lag/health
docker compose exec postgres pg_isready -U aegis_app -d aegis
docker compose exec redis redis-cli -p 6379 ping    # NOTE: 6379 INSIDE container
```

### 1.3 Verification checks (is data actually flowing?)

```bash
# Signals landing in DB
docker compose exec postgres psql -U aegis_app -d aegis -c \
  "SELECT platform, count(*) FROM signals GROUP BY 1 ORDER BY 2 DESC;"

# Phase 2 results on the stream
docker compose exec redis redis-cli -p 6379 XLEN aegis:phase2:graph_results

# Self-supervised learning ledgers (Phase A/B)
docker compose exec postgres psql -U aegis_app -d aegis -c \
  "SELECT count(*) FROM signal_outcomes;"          # forward learning loop
docker compose exec postgres psql -U aegis_app -d aegis -c \
  "SELECT count(*) FROM calibration_map;"          # calibration fitted?
```

### 1.4 Failure checks

```bash
docker compose ps | grep -v healthy                 # anything not healthy
docker compose logs --tail=100 autonomous | grep -iE "error|fatal"
docker compose logs --tail=100 predict   | grep -iE "error|traceback"
docker compose logs --tail=100 execute-drain | grep -iE "error"
uv run aegis support-bundle                         # diagnostic zip
```

### 1.5 Data checks

```bash
# Row counts across every key ledger
docker compose exec postgres psql -U aegis_app -d aegis -c "
SELECT 'signals' t, count(*) FROM signals UNION ALL
SELECT 'signal_outcomes', count(*) FROM signal_outcomes UNION ALL
SELECT 'predictions', count(*) FROM predictions UNION ALL
SELECT 'alerts', count(*) FROM alerts UNION ALL
SELECT 'trust_scores', count(*) FROM trust_scores ORDER BY 1;"
```

### 1.6 Scheduler checks

The scheduler (`src/aegis/scheduler/autonomous.py`) registers **14 jobs**:

| Job id | Trigger | What it does |
|--------|---------|--------------|
| `scrape` | every 15 min | scrape `AEGIS_AUTONOMOUS_TOPICS` → DB |
| `analyze` | every 1 h | run agent pipeline on recent signals |
| `settle_claims` | every 1 h | settle self-supervised signal claims (Phase A) |
| `emit_claims` | every 1 h | emit calibrated falsifiable claims |
| `drift` | every 6 h | drift check |
| `shadow` | every 6 h | shadow model evaluation |
| `health` | every 30 min | health report |
| `health_check` | every 5 min | self-healing health check |
| `refit_calibration` | daily 01:30 UTC | isotonic calibration refit |
| `datalake` | daily 02:00 UTC | lake refresh |
| `weights` | daily 03:30 UTC | nightly agent weight update |
| `retrain` | Sun 02:00 UTC | weekly retrain |
| `thresholds` | Sun 03:00 UTC | weekly threshold adaptation |
| `knowledge` | Sun 04:00 UTC | weekly knowledge refresh + self-audit |

```bash
# Confirm running + see scheduled jobs
docker compose logs --tail=60 autonomous | grep -E "scheduler\.(start|scrape|analyze|settle|emit|health)"

# Run the loop on the host instead (foreground, for debugging):
uv run aegis autonomous run
```

> ⚠️ The scheduler is **disabled by default** — it only runs under the
> `autonomous` compose profile (or the host command above). Phase B/C ledgers stay
> dormant until it is running. This is by design.

---

## PART 2 — OPERATOR DASHBOARD ROADMAP

The dashboard (`src/aegis/dashboard/`) already serves many endpoints (see table).
Goal: reorganize the SPA into the **9 health surfaces** below. Endpoints marked
**EXISTS** are live; **BUILD** must be added.

| Section | Backing endpoint(s) | Status |
|---------|--------------------|--------|
| **System Health** | `/api/system`, `/api/docker/status`, `/api/health/streams` | EXISTS |
| **Data Health** | `/api/signals/stats`, `/api/signals/platforms`, `/api/signals/velocity` | EXISTS |
| **Signal Health** | `/api/signals/recent`, `/api/search`, `/api/swarm/agents` | EXISTS |
| **Trust Health** | `/api/trust/*` | **BUILD** — wire `aegis trust` output to a route |
| **Calibration Health** | `/api/calibration/*` (ECE, Brier skill, base rate) | **BUILD** |
| **Opportunity Intelligence** | `/api/geo/recent`, `/api/capital/recent`, `/api/memory/opportunities` | partial — geo/capital EXIST, memory **BUILD** |
| **Execution Intelligence** | `/api/execute/alerts/stats`, `/api/execute/killswitch`, `/api/predictions/stats` | EXISTS |
| **Failure Intelligence** | `/api/anomalies/recent`, `/api/memory/failures` | partial — anomalies EXIST, memory **BUILD** |
| **Memory Intelligence** | `/api/memory/*` (opportunity/failure/entity/source) | **BUILD** |

**Build tasks (new routes in `src/aegis/dashboard/app.py`):**
1. `GET /api/trust/scores` → query `trust_scores` table (model + source trust).
2. `GET /api/calibration/report` → reads `calibration_map` + computes ECE/Brier skill (reuse `aegis trust` logic).
3. `GET /api/memory/opportunities|failures|entities|sources` → query Phase C tables (migrations 0018-0020).
4. Front-end: 9 nav sections in `static/index.html`, each polling its endpoint; red badge when any health metric breaches threshold.

> Every metric on the dashboard must show **N (sample size)** and a `MEASURED` /
> `INSUFFICIENT_EVIDENCE` badge. Per Phase E findings, confidence is currently only
> ADVISORY (Brier skill ≈ −0.014) — do **not** render it as a tradeable signal.

---

## PART 3 — SIGNAL EXPANSION ROADMAP

### Current source inventory
- **~30 working adapters** in `_ADAPTER_REGISTRY` (`cli/main.py`); 4 quarantined
  (`tiktok`, `pinterest`, `nitter` dead; `instagram` auth-walled).
- Working free-tier: reddit-rss, hacker-news, github-trending/public, amazon,
  google-news, bing-news, google-trends(+india), devto, producthunt, npm-trends,
  medium, techcrunch, wired, bbc, reuters, ndtv-profit, mint, business-standard,
  economic-times, moneycontrol, yahoo-finance, investing-com, flipkart, meesho,
  myntra, indiamart, ajio, nykaa, snapdeal, amazon-in, nse-bse, screener-in.

### Recommended new sources

| Pri | Source | Signal quality | Maint cost | Volume | Expected entities | Expected opportunities |
|-----|--------|----------------|-----------|--------|-------------------|------------------------|
| **P0** | StackOverflow tags RSS | High (dev demand) | Low (RSS) | Med | libraries, tools | dev-tool arbitrage |
| **P0** | Etsy trending (HTML) | High (commerce) | Med (anti-bot) | High | products, niches | POD/dropship products |
| **P0** | Google Shopping trends (RSS/HTML) | High (price) | Med | High | SKUs, prices | cross-market price gaps |
| **P1** | Hacker News "Show HN" filter | High (launches) | Low | Med | startups, products | early demand |
| **P1** | YouTube RSS (more channels) | Med | Low | High | creators, topics | creator-graph demand |
| **P1** | AliExpress hot products (HTML) | High (sourcing) | High (anti-bot) | High | SKUs, suppliers | sourcing margin |
| **P2** | Twitter/X via syndication | Med (noisy) | High | High | trends | velocity |
| **P2** | Telegram public channels | Med | Med | Med | niche communities | early signals |
| **P2** | Substack/RSS roundups | Low-Med | Low | Med | topics | narrative |

**P0 first** — all RSS/HTML, no paid API, fit the existing adapter pattern
(`src/aegis/scrape/sources/<name>.py` + registry entry + swarm wave). Each new
adapter: subclass the base adapter, return `Signal` rows, add to `_ADAPTER_REGISTRY`
and a swarm wave in `swarm.py`.

---

## PART 4 — PRODUCT VISIBILITY (IMAGES)

**Current state: `INSUFFICIENT_EVIDENCE`** — the `signals` schema stores
text/engagement fields; there is **no verified image/thumbnail column** today.

### Build plan
1. **Migration `0024_product_media.sql`**: add to `signals` (or a side table
   `product_media`): `thumbnail_url TEXT`, `image_url TEXT`, `category TEXT`.
   (`title` and `source`/`platform` already exist.)
2. **Adapters**: e-commerce adapters (flipkart, meesho, amazon-in, nykaa, etsy,
   aliexpress) already parse product cards — extract `<img src>` / og:image into
   the new columns. RSS adapters: pull `media:thumbnail` / enclosure.
3. **Dashboard "Product Intelligence Cards"**: new `GET /api/products/recent`
   returning `{title, thumbnail, image, source, category, score}`; render a card
   grid in `static/index.html` (image, title, source badge, opportunity score, link).

Store per product opportunity: `title`, `thumbnail`, `image`, `source`, `category`
(+ existing `url`, `score`, `detected_at`).

---

## PART 5 — REALITY MONITORING (DAILY REPORTS)

Add a scheduler job `job_daily_report` (CronTrigger 06:00 UTC) that writes a
markdown/JSON digest to MinIO + a dashboard endpoint. Report contents (all
**MEASURED from DB**, with N):

- New signals (24 h) by platform
- New opportunities (geo/capital/memory ledgers)
- New failures (`failures` table / anomalies)
- New entities (Phase C entity ledger)
- New sources / adapters health changes
- Trust changes (delta in `trust_scores`)
- Calibration changes (ECE / Brier skill delta vs yesterday)

```bash
# Until the job exists, generate it manually:
uv run aegis report daily
uv run aegis trust report           # calibration + trust snapshot
uv run aegis memory audit           # Phase C self-audit (KNOWLEDGE_AUDIT.md)
```

> Existing CLI today: `aegis report daily` EXISTS. `aegis trust` / `aegis memory`
> CLIs EXIST (DSN-fix landed S230). The consolidated **daily digest job is BUILD**.

---

## PART 6 — DATA ACCELERATION (10× signals / outcomes / entities, zero synthetic)

> Hard rule (PROJECT OMEGA): **no synthetic data**. Growth comes from real scrape
> volume + real forward-settled outcomes, never fabricated rows.

### 10× signals
- **Files**: add P0/P1 adapters in `src/aegis/scrape/sources/`; register in
  `_ADAPTER_REGISTRY` (`cli/main.py`) and a wave in `src/aegis/scrape/swarm.py`.
- **Scheduler**: raise scrape frequency and topic breadth in `autonomous.py`
  (`job_scrape` is `IntervalTrigger(minutes=15)`).
- **Env vars**:
  ```bash
  AEGIS_AUTONOMOUS_TOPICS="AI chips,bitcoin,e-commerce,POD,dropshipping,NVIDIA,solar,EV,supplements,skincare,..."   # widen topic set
  # (lower interval requires editing IntervalTrigger(minutes=5) in autonomous.py)
  ```
- **Command**: `uv run aegis swarm run` (30+ adapters in one pass) — schedule it.

### 10× outcomes (the real bottleneck — Phase E found 100% backfilled, 0 live)
- **Files**: `src/aegis/evolve/outcomes.py` + `signal_outcomes` settler; ensure
  `job_emit_claims` + `job_settle_claims` run hourly (they do, under `autonomous`).
- **Migration**: `0015_signal_outcomes.sql` (already applied) is the ledger.
- **Action**: keep the scheduler running continuously for ≥ N≥200 live
  emit-then-settle cycles so outcomes are **forward** (not backfill). This is the
  single highest-leverage activation — without it, no subsystem earns authority.
- **Command**: `uv run aegis evolve backfill-claims` (seed) then let the loop run.

### 10× entities
- **Files**: Phase C memory (`src/aegis/memory/`); `job_knowledge_refresh`.
- **Migrations**: `0018`-`0020` (applied). Entity extraction scales with signal volume.
- **Command**: `uv run aegis memory ...` (8 subcommands); refresh job is weekly —
  consider raising to daily once signal volume is up.

### Migrations needed for Part 4/5
- `0024_product_media.sql` (Part 4) — **BUILD**
- daily-report storage uses existing MinIO bucket — no migration needed.

---

## PART 7 — LIVE OPERATING PROCEDURES

### Morning checklist (5 min)
```bash
docker compose ps | grep -v healthy            # 1. all green?
uv run aegis status                            # 2. cross-service
docker compose logs --since=12h autonomous | grep -iE "error|fatal"   # 3. scheduler errors
docker compose exec postgres psql -U aegis_app -d aegis -c \
  "SELECT count(*) FROM signals WHERE created_at > now()-interval '24h';"  # 4. fresh signals?
docker compose exec postgres psql -U aegis_app -d aegis -c \
  "SELECT count(*) FROM signal_outcomes WHERE settled_at > now()-interval '24h';"  # 5. outcomes settling?
curl -s http://localhost:8300/api/health/streams | jq   # 6. stream lag
# 7. Open dashboard → scan 9 health sections for red badges
```

### Weekly checklist
- Review `aegis trust report` — calibration trend (ECE, Brier skill). Confidence
  must stay flagged ADVISORY until Brier skill > 0 on **live** outcomes.
- Confirm Sunday jobs ran: `retrain` (02:00), `thresholds` (03:00), `knowledge` (04:00).
- `uv run aegis memory audit` → review `KNOWLEDGE_AUDIT.md`.
- Backup: `uv run aegis backup create` (or `aegis dr backup`); verify with `aegis dr status`.
- Adapter health: `uv run aegis swarm agents` — quarantine newly-dead sources.

### Monthly checklist
- Restore drill: `uv run aegis dr drill --dry-run` then a real drill into `aegis_drill`.
- Re-backtest decision authority (Phase E): has any subsystem reached N≥200 live
  outcomes with positive lift? Update `PHASE_E_DECISION_AUTHORITY.md`.
- Dependency/CVE review (orjson/pyjwt/python-dotenv pins — see CLAUDE.md).
- Disk/retention: `uv run aegis datalake retention silver` / prune backups.

### Failure response checklist
```bash
# Container down
docker compose ps; docker compose logs --tail=200 <service>
docker compose up -d --no-build <service>          # recreate (picks up .env)

# Scheduler dead → Phase B/C dormant
docker compose --profile autonomous up -d autonomous

# Killswitch tripped (no alerts going out)
uv run --package aegis-execute aegis-execute killswitch state
uv run --package aegis-execute aegis-execute killswitch arm --reason "all clear"

# DB connection mismatch (CLI vs Docker) — known gotcha
#   CLIs use resolve_dsn(); ensure AEGIS_PG_DSN matches docker (port 5433)

# Stream backlog / lag → check XLEN, consumer groups
docker compose exec redis redis-cli -p 6379 XINFO GROUPS aegis:phase2:graph_results

# Full diagnostic
uv run aegis support-bundle
```
Runbooks for specific disasters: `uv run aegis dr runbook <pg_corruption|redis_oom|disk_full|docker_dead|...>`.

---

## FINAL OUTPUT — IMPLEMENTATION ORDER

1. **Startup commands** — Section 0 (full-stack `docker compose --profile ... up -d` + `alembic upgrade head`). *Expected: 18+ containers healthy, dashboard at :8300, scheduler logging 14 jobs.*
2. **Keep the scheduler running** (Part 6, 10× outcomes) — the binding constraint. *Expected: `signal_outcomes` grows with `settled_at` timestamps from live, not backfill.*
3. **Trust + Calibration dashboard routes** (Part 2 BUILD #1-2). *Expected: Trust/Calibration sections render real ECE/Brier with N.*
4. **Memory dashboard routes** (Part 2 BUILD #3). *Expected: Opportunity/Failure/Memory sections populated from Phase C tables.*
5. **P0 signal adapters** (Part 3) — StackOverflow, Etsy, Google Shopping. *Expected: signal volume up materially, new entities.*
6. **Product media migration + cards** (Part 4, migration `0024`). *Expected: product cards with images on dashboard.*
7. **Daily report job** (Part 5). *Expected: 06:00 UTC digest in MinIO + dashboard.*
8. **Re-backtest authority monthly** (Part 7). *Expected: subsystems graduate from ADVISORY → authority only on proven live lift.*

### Expected end-state
- Every component (postgres/redis/minio → predict/execute/drain → autonomous scheduler → dashboard → prometheus/grafana/jaeger/loki → ollama) **up and inter-operating**.
- Dashboard surfaces all 9 health domains; every number carries N + a verification badge.
- Signals, outcomes, and entities growing from **real** ingestion — no synthetic rows.
- No subsystem claims decision authority it has not earned on live forward-settled data.

> Anything in this document marked **BUILD / UNVERIFIED / INSUFFICIENT_EVIDENCE** is
> a future task, not a current capability. Do not operate it as if it exists.
