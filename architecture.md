# AEGIS Pulse — System Architecture

## Overview

AEGIS Pulse is an autonomous market arbitrage intelligence engine. It scrapes signals from 35+ platforms, stores them in TimescaleDB, and runs a 10-node LangGraph multi-agent pipeline to score and prioritise arbitrage opportunities.

---

## High-Level Data Flow

```
[Signal Sources: 35+ adapters]
         │
         ▼
[Phase 0 — Scrape Layer]
   topic.py / swarm.py
   dedup, normalizer, confidence gate
         │
         ▼
[Phase 1 — Persistence Layer]
   TimescaleDB (signals, authors)
   Redis (cache + inter-agent streams)
   MinIO (raw / features / models / backups)
         │
         ▼
[Phase 2 — Multi-Agent Intelligence]
   10-node LangGraph DAG
   LLM: Ollama → Groq → OpenRouter → Gemini
   ChromaDB memory + Redis shared memory
         │
         ├──────────────────────────────────┐
         ▼                                  ▼
[Phase 3 — Predictive Apex]        [Phase 4 — Execute & Alert]
   Heuristic floor + Neural          7-stage alert pipeline
   ML models (PatchTST, HGT)         SSE streaming
   ONNX export / FastAPI :8100        Killswitch / notifiers
   RL policy (fractional-Kelly)       FastAPI :8200
         │                                  │
         └──────────────┬───────────────────┘
                        ▼
              [Phase 5 — Autonomous Scale]
                SwarmOrchestrator (4 waves, 35 adapters)
                OLS velocity + PCA denoising
                Confidence gate (threshold 0.85)
                Topic intelligence router
                        │
                        ▼
              [Dashboard — Command Center :8300]
                SSE live feed, ops console
                ChartJS, system health, signal stats
```

---

## Phase Breakdown

### Phase 0 — Scrape Layer (`src/aegis/scrape/`)

| Component | Purpose |
|-----------|---------|
| `sources/` | 35+ adapter files; each produces a list of raw dicts |
| `topic.py` | `expand_topic()` + `scrape_topic()` — keyword → multi-source harvest |
| `patterns.py` | `detect_patterns()` — signal clustering by semantic theme |
| `dedup.py` | Two-layer semantic dedup (token + sequence similarity) |
| `normalizer.py` | Z-score + percentile + tier-weighted score normalisation |
| `confidence.py` | `score_batch()` — advisory quality gate (default threshold 0.85) |
| `analytics.py` | OLS velocity slope + PCA denoising on TF-IDF vectors |
| `sentiment.py` | Keyword-based finance/e-commerce sentiment classifier |
| `schema_guard.py` | Schema drift detection + signal validation |
| `ecommerce_utils.py` | FlareSolverr bypass + User-Agent rotation |
| `governor.py` | `ConcurrencyGovernor` — semaphores + token buckets (5 global, 2 FlareSolverr) |

### Phase 1 — Persistence (`src/aegis/db/`, `src/aegis/cache/`)

| Component | Purpose |
|-----------|---------|
| `db/pool.py` | `get_shared_pool()` / `set_shared_pool()` — single asyncpg pool at startup |
| `db/signals.py` | `fetch_recent_signals()`, insert, dedup sweeper |
| `cache/redis.py` | `RedisCache` — TTL-backed key/value + pub/sub |
| MinIO | Object store: raw signals, feature windows, model artifacts, backups |

**TimescaleDB tables**: `signals`, `authors`, `predictions`, `prediction_audit`, `model_manifest`, `backtest_results`, `alerts`, `alert_outbox`, `alert_deliveries`, `execution_intents`, `killswitch_audit`

All tables use Row-Level Security. Always `SET app.current_tenant = '<uuid>'` before queries.

### Phase 2 — Multi-Agent Intelligence (`src/aegis/agents/`)

```
GraphState (TypedDict, annotated reducers)
         │
         ▼
10-node LangGraph DAG
  ┌─ scout      (initial signal scan → TrendCandidate list)
  ├─ sentinel   (risk / regime check)
  ├─ analyst    (quantitative scoring)
  ├─ validator  (schema + compliance gate)
  ├─ strategist (position sizing)
  ├─ overseer   (cross-agent consensus)
  ├─ archivist  (ChromaDB memory store)
  ├─ herald     (result formatter)
  ├─ auditor    (audit log writer)
  └─ supervisor (finalize: aggregate decisions → GraphResult)
```

**LLM fallback chain**: Ollama (local) → Groq → OpenRouter → Gemini  
**Key models**: `TrendCandidate`, `AgentDecision`, `GraphResult` — all Pydantic v2 frozen  
**Stream**: publishes enriched JSON to `aegis:phase2:graph_results` Redis stream under `"body"` key

### Phase 3 — Predictive Apex (`src/aegis/predict/`)

```
InferenceRunner (single entry point)
   │
   ├─ features/builder.py   → FeatureWindow (FEATURE_DIM=20)
   ├─ models/heuristic.py   → deterministic floor verdict
   ├─ models/neural.py      → PatchTST / Autoformer / TimesNet / HGT (optional)
   ├─ causal/attributor.py  → DeterministicAttributor + 5 counterfactuals
   ├─ rl/policy.py          → HeuristicPolicy (fractional-Kelly + stop-loss)
   └─ backtest/             → WalkForwardBacktester (purged k-fold + adversarial noise)
```

**Heuristic-first doctrine**: neural models can only reduce confidence [0.5–1.0], never flip a verdict. Guarantees zero-API-key test pass, <12ms p99 latency floor.

**Phase 3 ↔ Phase 2 bridge** (`src/aegis/agents_phase3_glue/bridge.py`): maps `InferenceResult → AgentDecision` without importing LangGraph. SCOUT uses `p_breakout` at 24h; SENTINEL uses `p_decline` at 6h.

### Phase 4 — Execution & Alert System (`aegis-phase4/`)

```
IntakeWorker  ──(Redis Streams XREADGROUP)──▶  bridge/phase2.py
                                                   │
                                                   ▼
                                             7-stage pipeline
                                               policy.deduper   (HMAC idempotency)
                                               policy.composer  (alert envelope)
                                               policy.confidence_gate
                                               store.alert_repo (DB write)
                                               outbox drainer   (claim → deliver)
                                               notifiers        (Discord/ntfy/Telegram)
                                               SSE EventBus     (:8200/stream)
```

**Killswitch**: `aegis:execute:killswitch` Redis key. When tripped, all outbound dispatch halts.  
**Merge window**: `MERGE_WINDOW_S = 30.0` — Phase 2 + Phase 3 results merged within 30s.  
**Verdict mapping**: `proceed→ENTER`, `hold→HOLD`, `block→BLOCK`, `escalate→HOLD`

### Phase 5 — Autonomous Scale & Swarm (`src/aegis/scrape/swarm*.py`)

```
SwarmOrchestrator
  ├─ Wave 1: Low-risk RSS/news adapters (TechCrunch, BBC, Reuters, Mint, …)
  ├─ Wave 2: Social + mid-tier (Reddit finance/ecommerce, YouTube RSS, ProductHunt, …)
  ├─ Wave 3: E-commerce (Flipkart, Myntra via FlareSolverr; Meesho, IndiaMart, …)
  └─ Wave 4: Financial markets (NSE/BSE, Screener.in, MoneyControl, Economic Times, …)
       │
  SwarmAgentPool (health tracking per adapter)
  SwarmAnalyzer  (normalise + synthesise across waves)
  SwarmPersistence (DB write + Redis publish: aegis:swarm:latest, aegis:swarm:results)
  SwarmSynthesizer (final SwarmResult model)
```

**Zero paid APIs**: all 35 adapters use free RSS, public HTML, or public JSON.  
**India-first ordering**: Flipkart, NSE, MoneyControl prioritised in wave ordering.

### Dashboard — Command Center (`src/aegis/dashboard/`)

| Component | Purpose |
|-----------|---------|
| `app.py` | FastAPI backend — REST + SSE aggregating all phases |
| `static/index.html` | Dark-theme SPA: ChartJS charts, SSE live feed, ops console |
| `cli.py` | `aegis dashboard serve` entry point |

Ops console security: `POST /api/ops/run` only accepts first-token in `{"aegis", "uv", "python", "docker"}`.

---

## Infrastructure

| Service | Port | Purpose |
|---------|------|---------|
| aegis-postgres | 5433 | TimescaleDB + pgvector |
| aegis-redis | 6380 | Cache + inter-agent streams |
| aegis-minio | 9002/9003 | Object store |
| aegis-flaresolverr | 8191 | Cloudflare bypass for e-commerce |
| aegis-predict | 8100 | Phase 3 FastAPI inference |
| aegis-execute-api | 8200 | Phase 4 FastAPI alerts + SSE |
| aegis-execute-drain | — | Phase 4 outbox drain worker |
| aegis-dashboard | 8300 | Command Center web UI |
| aegis-prometheus | 9091 | Metrics |
| aegis-grafana | 3001 | Dashboards |
| aegis-jaeger | 16687 | Distributed traces |

---

## Key Cross-Cutting Decisions

| Concern | Decision |
|---------|---------|
| Logging | `structlog` everywhere in `aegis.agents.*` and `aegis.agents_phase3_glue.*`; never stdlib logging |
| Resilience | `predict.resilience` — functional `resilient_call(op, name, timeout_s)` for ML ops; `core.resilience` — decorator `@resilient_call(policy)` for scraper I/O |
| Content hash | Unified computation via `aegis.scrape.dedup` to prevent digest divergence across adapters |
| Swarm config | `swarm_*` fields nested under `settings.scrape.*`; always use `getattr(settings.scrape, "field", default)` |
| Stream field | Redis stream entry field is `"body"` (never `"payload"`) — Phase 2 runner, Phase 4 IntakeWorker, and Dashboard all agree |
| Tenancy | All DB tables use RLS on `app.current_tenant`. Default dev UUID: `00000000-0000-0000-0000-000000000001` |
| Frozen models | `TrendCandidate`, `AgentDecision`, `GraphResult` are immutable after construction |
| scraped_at | Promoted from `ScrapeProvenance.scraped_at` to signal root level for schema validation compatibility |

---

## Package Dependency Graph

```
aegis.scrape  ←───────────────────────────────────────────────────────────────┐
     │                                                                          │
     ▼                                                                          │
aegis.db / aegis.cache                                                         │
     │                                                                          │
     ▼                                                                          │
aegis.agents  ←──  aegis.agents_phase3_glue  ←──  aegis.predict               │
     │                                                                          │
     ▼                                                                          │
aegis.execute (workspace member: aegis-phase4/)                                │
     │                                                                          │
     ▼                                                                          │
aegis.dashboard  ──────────────────────────────────────────────────────────────┘
```

---

## Test Coverage Summary

| Scope | Tests | Coverage |
|-------|-------|---------|
| Phases 0–5 unit (`tests/unit/`) | 775+ | ≥78% |
| Phase 4 unit (`aegis-phase4/tests/`) | 161 | standalone |
| Phase 3 integration (`tests/integration/predict/`) | 4 | end-to-end |
