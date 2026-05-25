# AEGIS Pulse — Claude Code Context

## Project overview

AEGIS Pulse is an autonomous market arbitrage intelligence engine.  
It scrapes signals from social platforms, stores them in TimescaleDB, and runs a 10-node LangGraph multi-agent pipeline to score and prioritise arbitrage opportunities.

**Phase 0** — Data ingestion (scrape adapters, HTTP pipeline, semantic deduplication)  
**Phase 1** — Postgres/TimescaleDB persistence, Redis cache, MinIO object store  
**Phase 2** — Multi-agent intelligence (LangGraph DAG, LLM routing, ChromaDB memory)  
**Phase 3** — Predictive Apex (hybrid ML core: heuristic floor + optional neural augmentation; FastAPI serving; fractional-Kelly RL policy)  
**Phase 4** — Execution & Alert System (alert pipeline, killswitch, SSE streaming, notification channels; FastAPI execute-api on :8200)  
**Phase 5** — Production & Autonomous Scale (statistical modeling pipeline, data quality gate, autonomous confidence scoring)  
**Phase 10** — Data Lake & Analytics (Bronze/Silver/Gold medallion over Parquet on MinIO; DuckDB query engine; Prefect orchestration; `aegis datalake` CLI)  
**Phase 11** — Local LLM Orchestration (LLMGateway with circuit breaker + provider-selector; Ollama→Groq→OpenRouter→Gemini fallback; semantic router; guardrails; Pydantic structured output; `aegis llm` CLI)  
**Dashboard** — Unified Command Center web UI on :8300 (aggregates all phases; SSE live feed; streaming console for running CLI ops)

## Package layout

```
src/aegis/
  __init__.py          — package root, version 0.3.0
  config.py            — pydantic-settings Settings singleton
  cli/                 — Click CLI (aegis scrape, aegis analyze, aegis topic, aegis daily, …)
  scrape/              — Phase 0: scrape adapters + topic intelligence
    sources/           — 13 adapters (reddit-rss, hacker-news, github-trending, amazon,
                         google-news, bing-news, google-trends, reddit, youtube, instagram,
                         tiktok, pinterest, nitter)
    topic.py           — expand_topic() + scrape_topic(): one keyword → full multi-source harvest
    patterns.py        — detect_patterns(): signal clustering by semantic theme
    dedup.py           — two-layer semantic deduplication (token + sequence similarity)
  db/                  — Phase 1: asyncpg PgPool, signals/authors CRUD, dedup sweeper
  cache/               — Redis cache (RedisCache)
  core/                — metrics (Prometheus), logging (structlog)
  agents/              — Phase 2: multi-agent intelligence
    schemas.py         — TrendCandidate, AgentDecision, GraphResult (Pydantic v2)
    state.py           — GraphState TypedDict with annotated reducers
    graph.py           — LangGraph DAG builder (10 nodes + edges)
    runner.py          — run_trend() entrypoint; compiled-graph cache
    supervisor.py      — finalize node: aggregate decisions → final verdict
    nodes/             — 10 agent nodes; scout + sentinel wired to Phase 3 bridge
    llm/               — LLM router (Ollama→Groq→OpenRouter→Gemini fallback); Phase 11 shim re-exports get_gateway/complete_for_agent
    memory/            — ChromaDB semantic store + Redis shared memory + MinIO snapshots
    messaging/         — Redis Streams inter-agent bus + HMAC-SHA256 signing
    tools/             — velocity_classify, compliance_check, monte_carlo, historical_lookup, signal_query
    prompts/           — 10 Jinja2 prompt templates (one per agent node)
  predict/             — Phase 3: Predictive Apex (heuristic-first, neural augmentation)
    __init__.py        — FEATURE_DIM=20, DEFAULT_HORIZONS=(1,6,24,72), FEATURE_NAMES
    schemas.py         — FeatureWindow, Prediction, PredictionBundle, PredictionRecord (Pydantic v2 frozen)
    constants.py       — INFERENCE_HARD_TIMEOUT_S, PREDICT_BATCH_MAX, latency budgets
    errors.py          — typed error codes AEGIS-PREDICT-0001..0015
    resilience.py      — functional async wrapper: resilient_call(op, name, timeout_s)
    features/          — builder (Phase 1 signals → FeatureWindow), velocity, creator graph
    models/            — heuristic floor + PatchTST/Autoformer/TimesNet/HGT (optional torch)
    causal/            — DeterministicAttributor + 5 counterfactual scenarios
    rl/                — HeuristicPolicy (fractional-Kelly + stop-loss) + gymnasium ArbitrageEnv
    backtest/          — WalkForwardBacktester (purged k-fold + adversarial noise)
    registry/          — ModelStore (sha256 atomic writes) + PromotionGate (shadow-first)
    training/          — Trainer + SignalDataset (Parquet/Arrow) + ONNX INT8 export
    inference/         — InferenceRunner: the single Phase 3 entry point
    serving/           — FastAPI: /predict /predict/batch /healthz /readyz /metrics
    cli/               — Typer: run, serve, bench, eval
  agents_phase3_glue/  — Phase 2 ↔ Phase 3 bridge (no LangGraph import)
    bridge.py          — InferenceResult → AgentDecision dict mapping
  datalake/            — Phase 10: Bronze/Silver/Gold data lake
    __init__.py        — lazy-import facade; PHASE="phase10", VERSION="0.10.0", FEATURE_FLAGS
    settings.py        — DataLakeSettings (AEGIS_DATALAKE_* env prefix)
    constants.py       — BRONZE/SILVER/GOLD layer names, batching + DuckDB + quality constants
    schemas.py         — BronzeSignal/Prediction/Alert/AgentResult, SilverSignal/Prediction, Gold* (frozen Pydantic v2)
    errors.py          — typed error hierarchy AEGIS-DATALAKE-0000..0502
    facade.py          — DataLake: top-level composer (open/session/query/build_silver/build_gold/health)
    migrations.py      — SQLite catalog schema migrations (run_migrations / current_version)
    retention.py       — RetentionEnforcer: plan + apply deletion by layer cutoff date
    bronze/            — Raw ingest layer: PostgresSignalsIngester, PostgresPredictionsIngester, PostgresAlertsIngester, RedisStreamIngester, BronzeWriter
    silver/            — Cleaned + conformed: SilverBuilder (signals + predictions for a UTC date)
    gold/              — Business aggregates: GoldAggregator (daily platform stats, verdict rollup, prediction accuracy)
    catalog/           — SQLite-backed table + partition registry (LakeCatalog)
    storage/           — StorageBackend Protocol; LocalStorageBackend + S3StorageBackend; parquet read/write
    query/             — DuckDBQueryEngine: read-only SQL over registered catalog views
    orchestration/     — Prefect 3 flow definitions (daily-lake-refresh; gracefully no-ops if prefect absent)
    api/               — FastAPI router (/datalake/health /tables /partitions /query /lineage)
    cli/               — Click CLI: doctor migrate list-tables ingest-* build-silver build-gold query retention daily
  llm/                 — Phase 11: Local LLM Orchestration Layer
    __init__.py        — exports LLMGateway, LLMResponse, ProviderSelector, SemanticRouter; __version__ = "11.0.0"
    config.py          — LLMSettings (AEGIS_* env prefix): all provider URLs/keys/models
    constants.py       — PROVIDER_PRIORITY, timeouts, ERR_* error codes, cost tables
    errors.py          — AegisLLMError hierarchy: AllProvidersFailed, ProviderTimeout, GuardrailBlock, etc.
    gateway/           — LLMGateway (create/complete/embed/health/aclose/cost_summary), LLMResponse, middleware, streaming
    providers/         — OllamaProvider, GroqProvider, OpenRouterProvider, GeminiProvider, AnthropicProvider, OpenAIProvider, VLLMProvider
    routing/           — ProviderSelector (health TTL cache), SemanticRouter (cosine similarity), TaskRouter (task-type matrix), CostAwareRouter
    guardrails/        — GuardrailsValidator (max-len/PII/toxic), PIIScrubber (email/phone/SSN/Aadhaar/PAN)
    instructor/        — InstructorAdapter (Pydantic structured output + retry), per-node output schemas (ScoutOutput, SentinelOutput, …)
    cache.py           — LLMCache: 2-layer LRU (in-process) + Redis; bypasses temperature > 0.5
    metrics.py         — Prometheus metrics with _NoOpMetric fallback
    tokenizer.py       — count_tokens, fits_in_context, truncate_messages (tiktoken-backed)
    bridge/            — agents_bridge (process-singleton get_gateway/complete_for_agent), phase3_bridge, phase4_bridge
    registry/          — ModelRegistry (capability + latency catalog), PromptRegistry (Jinja2 + YAML front-matter + audit trail)
    prompts/           — 10 Jinja2 prompt templates (scout, sentinel, historian, geo_arbitrage, compliance, narrative, hedge, auditor, sourcer, red_team)
    eval/              — nightly golden-answer eval runner + metrics
    cli/               — Click: health, complete, embed, eval, pull, cost, models
  dashboard/           — Command Center web UI (FastAPI :8300)
    app.py             — REST + SSE backend (aggregates Postgres, Redis, Phase3, Phase4, Docker)
    cli.py             — `aegis dashboard serve` entry point
    static/index.html  — SPA: dark-theme, sidebar nav, ChartJS charts, SSE live feed,
                         streaming ops console (runs CLI commands and streams output)
aegis-phase4/          — Phase 4: Execution & Alert System (uv workspace member)
  src/aegis/execute/
    api/               — FastAPI: POST /alerts  GET /stream  GET /dashboard/  GET /healthz
    bridge/            — ComposerInput duck-typed protocol; maps Phase 2+3 results
    bus/               — In-process SSE EventBus (bounded per-client queues)
    cli/               — Typer: serve, drain, tail, killswitch {state,trip,arm}
    config.py          — ExecuteSettings pydantic-settings singleton
    constants.py       — stream names, timeouts, batch sizes
    killswitch/        — Redis-backed kill switch (trip/arm/is_tripped)
    metrics.py         — Prometheus counters/gauges/histograms (graceful no-op without prom)
    notifiers/         — Discord, ntfy, Telegram notification channels
    outbox/            — Transactional outbox drainer (claim → deliver → mark done)
    pipeline/          — 7-stage alert composition pipeline
    policy/            — Deduper (HMAC idempotency), Composer, Confidence gate
    schemas/           — AlertEnvelope, AlertOutboxRow, ComposerInput (Pydantic v2 frozen)
    store/             — AlertRepository (asyncpg, RLS, TimescaleDB hypertable)
    utils/             — Injectable clock, decorrelated backoff
    workers/           — IntakeWorker (Redis Streams XREADGROUP), DrainWorker
  tests/unit/execute/  — 161 Phase 4 unit tests (all pass)
  docker/
    Dockerfile.api     — multi-stage FastAPI server
    Dockerfile.drain   — outbox drain worker
tests/
  unit/                — 663 unit tests (all pass, coverage ≥ 82%)
  unit/agents/         — 179 Phase 2 agent tests
  unit/predict/        — 163 Phase 3 predict tests
  integration/predict/ — 4 Phase 3 end-to-end tests (all pass)
db/
  migrations/          — SQL migrations (0001_init.sql + 0002_predictions.sql +
                         0003_execute.sql + 0004_new_platforms.sql)
docker/
  Dockerfile.predict   — multi-stage, tini PID 1, non-root, optional ML build arg
  Dockerfile.dashboard — multi-stage dashboard server
alembic/               — Alembic migration scaffolding
config/                — Prometheus + Grafana configs
docker-compose.yml     — 14-service dev stack (postgres, redis, minio, flaresolverr,
                         predict :8100, execute-api :8200, execute-drain,
                         dashboard :8300, prometheus, grafana, jaeger,
                         ollama :11434, ollama-init [one-shot], litellm :8080 [profile: llm-proxy])
docs/phase3/           — Phase 3 architecture, models, operations, integration docs
SYSTEM_TOUR.md         — Plain-English architectural tour (non-technical reference)
```

## Running the project

### 1. Install dependencies

```bash
# --all-packages pulls in aegis-execute workspace member
# --all-extras enables langgraph, chromadb, sentence-transformers, pytrends, boto3
uv sync --all-packages --all-extras
```

### 2. Start the full stack

```bash
# Starts all 11 services (postgres, redis, minio, flaresolverr, predict,
# execute-api, execute-drain, dashboard, prometheus, grafana, jaeger)
docker compose up -d

# Or use the CLI wrapper:
uv run aegis up

# Check all services are healthy:
uv run aegis status
```

### 3. Web UIs (open in browser after `docker compose up -d`)

| URL | What it is |
|-----|-----------|
| **http://localhost:8300** | AEGIS Command Center — unified dashboard (signals, agents, ops console) |
| http://localhost:8200/dashboard/ | Phase 4 Execute dashboard (alerts, killswitch) |
| http://localhost:3001 | Grafana — infra metrics (admin / aegis_dev_admin_pw) |
| http://localhost:9091 | Prometheus — raw metrics |
| http://localhost:16687 | Jaeger — distributed traces |
| http://localhost:9003 | MinIO console (aegis-dev-key / aegis-dev-secret-please-change) |

### 4. The one daily command (non-technical users)

```bash
# Scrapes 4 sources, runs AI analysis, prints plain-English verdict
uv run aegis daily
```

### 5. Full intelligence cycle for a specific topic

```bash
# One word → expand queries → scrape 6+ sources → dedup → AI analysis → verdict
uv run aegis topic "AI chips"
uv run aegis topic "bitcoin" --limit 50
uv run aegis topic "NVIDIA" --no-llm --json-out   # heuristic-only, machine-readable
uv run aegis topic "e-commerce" --no-analyze       # scrape only, skip analysis
```

### 6. Individual scrape commands

```bash
# Working adapters (no API keys needed):
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 50
uv run aegis scrape --source reddit-rss --subreddit Entrepreneur --limit 50
uv run aegis scrape --source hacker-news --limit 50
uv run aegis scrape --source github-trending --limit 30
uv run aegis scrape --source amazon --limit 80
uv run aegis scrape --source google-news --query "AI chips" --limit 30
uv run aegis scrape --source bing-news --query "bitcoin" --limit 30
uv run aegis scrape --source google-trends --limit 20

# Check what's in the DB:
uv run aegis signals tail --limit 20
uv run aegis signals tail --platform hacker_news --limit 10
```

### 7. AI analysis pipeline

```bash
# Run the 10-node agent pipeline on recent signals
# (publishes full result to aegis:phase2:graph_results Redis stream)
uv run aegis analyze --limit 20
uv run aegis analyze --limit 30 --platform reddit
uv run aegis analyze --no-llm              # heuristic-only (no API keys needed)
uv run aegis analyze --json-out            # machine-readable output
```

### 8. Database utilities

```bash
# Find + optionally delete semantic duplicates:
uv run aegis dedup                              # dry-run (safe, shows what would be removed)
uv run aegis dedup --delete                     # actually delete duplicates
uv run aegis dedup --lookback-hours 720 --threshold 0.90

# Detect emerging thematic patterns in recent signals:
uv run aegis patterns
uv run aegis patterns --limit 500 --min-cluster-size 3
uv run aegis patterns --platform hacker_news

# Daily report (yesterday's summary):
uv run aegis report daily
uv run aegis report daily --date 2026-05-17
```

### 9. Dashboard

```bash
# Start dashboard standalone (if not using docker compose):
uv run aegis dashboard serve

# Dashboard runs on http://localhost:8300 by default
# The dashboard ops console can run any aegis command with live streaming output.
```

### 10. Phase 4 alert system

```bash
# Health checks:
curl -s http://localhost:8200/healthz
curl -s http://localhost:8200/readyz

# Killswitch control:
uv run --package aegis-execute aegis-execute killswitch trip --reason "manual test"
uv run --package aegis-execute aegis-execute killswitch arm  --reason "all clear"
uv run --package aegis-execute aegis-execute killswitch state

# Tail recent alerts:
uv run --package aegis-execute aegis-execute tail \
  --tenant 00000000-0000-0000-0000-000000000001 --limit 20

# Smoke test (no infrastructure needed):
uv run --package aegis-execute aegis-execute compose-demo \
  --trend-id demo-1 --verdict ENTER --score 0.82 --confidence 0.75
```

### 11. Data Lake (Phase 10)

```bash
# Health check:
uv run aegis datalake doctor --json-out

# Apply catalog schema migrations:
uv run aegis datalake migrate

# Ingest Phase 1 signals → Bronze (last 7 days by default):
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
uv run aegis datalake query "SELECT platform, COUNT(*) FROM signals GROUP BY 1"

# List registered tables:
uv run aegis datalake list-tables
uv run aegis datalake list-tables --layer bronze

# Retention management (dry-run by default):
uv run aegis datalake retention silver
uv run aegis datalake retention silver --apply  # actually deletes

# Local filesystem mode (for testing, no MinIO needed):
uv run aegis datalake doctor --local-root /tmp/aegis-lake --json-out
```

### 12. Phase 11 LLM CLI

```bash
# Provider health check (shows latency + circuit state for all configured providers):
uv run aegis llm health
uv run aegis llm health --json-out

# One-shot completion (uses gateway fallback chain):
uv run aegis llm complete "Summarise the latest AI chip news in 3 bullets"
uv run aegis llm complete "..." --provider groq --model llama-3.3-70b-versatile

# Embedding (defaults to Ollama bge-m3 or Gemini fallback):
uv run aegis llm embed "text to embed"

# List all registered models with capability tags:
uv run aegis llm models
uv run aegis llm models --provider ollama

# Pull Ollama model (wraps `ollama pull`):
uv run aegis llm pull llama3.2:3b
uv run aegis llm pull bge-m3

# Cost summary (prints estimated USD spend from in-process metrics):
uv run aegis llm cost

# Run nightly eval against golden-answer fixture set:
uv run aegis llm eval
uv run aegis llm eval --fixture-dir src/aegis/llm/eval/golden/
```

### 13. Stack lifecycle

```bash
uv run aegis up               # start all services (detached)
uv run aegis up --build       # rebuild Docker images first
uv run aegis down             # stop (data preserved)
uv run aegis down --volumes   # stop + WIPE all data (irreversible)
uv run aegis reset            # same as down --volumes (asks for confirmation)
uv run aegis tail             # follow logs for all services
uv run aegis tail predict     # follow logs for one service
uv run aegis doctor           # health check
uv run aegis support-bundle   # collect diagnostic zip for debugging
```

### 12. Tests

```bash
# Phases 0-3 unit tests (quick, no infra):
uv run python -m pytest tests/unit/ -q -p no:hypothesis

# With coverage report (floor: 78%):
uv run python -m pytest tests/unit/ -p no:hypothesis

# Phase 3 integration tests (requires running postgres + redis):
uv run python -m pytest tests/integration/predict/ -v -p no:hypothesis

# Phase 4 unit tests:
uv run python -m pytest aegis-phase4/tests/ -q -p no:hypothesis
```

---

## Phase status — verified 2026-05-22

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 0 — Scrape | ✅ Green | 7 working adapters (reddit-rss, hn, github, amazon, google-news, bing-news, google-trends); topic intelligence with parallel multi-source harvest |
| Phase 1 — Persistence | ✅ Green | TimescaleDB, Redis, MinIO all healthy; semantic dedup sweeper |
| Phase 2 — Agents | ✅ Green | 10-node LangGraph DAG; publishes full result (decisions + timing) to `aegis:phase2:graph_results` Redis stream |
| Phase 3 — Predict | ✅ Green | Heuristic-first ML core; 663 tests pass; 4/4 integration tests pass; coverage 82.79% |
| Phase 4 — Execute | ✅ Green | Alert pipeline + killswitch + SSE; 161 tests pass; execute-api :8200 |
| Phase 5 — Autonomous Scale | ✅ Green | OLS velocity + PCA denoising + confidence gate + SwarmOrchestrator multi-wave parallel harvest + topic intelligence routing + `aegis swarm` CLI; 1051 tests pass; coverage 78.00% |
| Phase 10 — Data Lake | ✅ Green | Bronze/Silver/Gold medallion over Parquet on MinIO; DuckDB query engine; Prefect 3 orchestration; `aegis datalake` CLI; 160 unit tests pass; 0 ruff violations; integrated into main package at `src/aegis/datalake/` |
| Phase 11 — LLM Orchestration | ✅ Green | LLMGateway with circuit breaker + health-based selector; Ollama→Groq→OpenRouter→Gemini fallback; SemanticRouter; GuardrailsValidator; InstructorAdapter; 167 unit tests pass; `aegis llm` CLI; integrated at `src/aegis/llm/` |
| Dashboard | ✅ Green | Command Center on :8300; SSE live feed; ops console; system health; signal stats; agent intelligence view |
| Orchestration | ✅ Green | `aegis analyze` / `aegis topic` / `aegis daily` / `aegis swarm` → full pipeline → Phase 4 stream |
| Code quality | ✅ Green | 0 ruff violations across all phases |

---

## Scrape adapter status (verified 2026-05-18)

| CLI `--source` name | Status | Notes |
|---------------------|--------|-------|
| `reddit-rss` | ✅ Working | Best free Reddit; no API key; pass `--subreddit` |
| `hacker-news` | ✅ Working | Algolia API; fast; supports `--query` |
| `github-trending` | ✅ Working | Direct GitHub HTML; very stable |
| `amazon` | ✅ Working | Bestseller rankings; titles from URL slug (approximate) |
| `google-news` | ✅ Working | Free RSS feed; supports `--query`; no key needed |
| `bing-news` | ✅ Working | Free RSS feed; supports `--query`; no key needed |
| `google-trends` | ✅ Working | Requires pytrends extra; rate-limited at 0.2 req/s |
| `tiktok` | ❌ Broken | Creative Center API requires TikTok Ads auth |
| `pinterest` | ❌ Broken | Unofficial search API returns 403 |
| `nitter` | ❌ Broken | All public Nitter instances dead or 403 |
| `instagram` | ⚠️ Setup | Very high ban risk; needs `allow_red_tos=True` + session cookie |
| `reddit` | ⚠️ Key needed | Set `AEGIS_REDDIT_CLIENT_ID` + `AEGIS_REDDIT_CLIENT_SECRET` |
| `youtube` | ⚠️ Key needed | Set `AEGIS_YOUTUBE_API_KEY` |

**Recommended daily scrape** (no API keys needed):
```bash
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 50
uv run aegis scrape --source reddit-rss --subreddit Entrepreneur --limit 50
uv run aegis scrape --source hacker-news --limit 50
uv run aegis scrape --source github-trending --limit 30
uv run aegis scrape --source google-news --query "tech trends" --limit 30
uv run aegis scrape --source bing-news --query "market opportunity" --limit 30
uv run aegis scrape --source amazon --limit 80
```

Or just run `aegis daily` which does all of the above automatically.

---

## Infrastructure (docker-compose.yml)

| Service | Host port | Purpose |
|---------|-----------|---------|
| aegis-postgres | 5433 | TimescaleDB + pgvector |
| aegis-redis | 6380 | Cache + inter-agent streams |
| aegis-minio | 9002/9003 | Object store (raw/features/models/backups) |
| aegis-flaresolverr | 8191 | Cloudflare bypass |
| aegis-predict | 8100 | Phase 3 FastAPI inference server |
| aegis-execute-api | 8200 | Phase 4 FastAPI alert + SSE server |
| aegis-execute-drain | — | Phase 4 outbox drain worker (no exposed port) |
| aegis-dashboard | 8300 | Command Center web UI (aggregates all phases) |
| aegis-prefect | 4200 | Phase 10 Prefect 3 orchestration UI (optional) |
| ollama | 11434 | Phase 11 local LLM runtime (OpenAI-compat `/v1/chat/completions`) |
| ollama-init | — | One-shot: pulls `bge-m3` + `llama3.2:3b` into Ollama on first start |
| litellm | 8080 | Phase 11 LiteLLM proxy (profile: `llm-proxy`; optional) |
| aegis-prometheus | 9091 | Metrics scrape |
| aegis-grafana | 3001 | Dashboards (admin/aegis_dev_admin_pw) |
| aegis-jaeger | 16687 | Distributed traces |

DB connection: `postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis`  
Redis: `redis://localhost:6380/0`

---

## Key design decisions

- **Heuristic-first**: every agent produces a deterministic verdict from numeric features; LLM enhances reasoning text only. Pipeline always produces a result even with no LLM keys.
- **LLM fallback chain**: Ollama (local) → Groq → OpenRouter → Gemini. Set `AEGIS_DISABLE_OLLAMA=1` in dev/test to skip local Ollama.
- **Shared pool pattern**: `get_shared_pool()` / `set_shared_pool()` in `aegis.db.pool` — set once at startup, accessed by Phase 2 tools without dependency injection.
- **RLS everywhere**: all tables use Row-Level Security. Always `SET app.current_tenant = '<uuid>'` before queries.
- **Pydantic v2 frozen models**: `TrendCandidate`, `AgentDecision`, `GraphResult` are frozen — immutable after construction.
- **`TrendCandidate` fields**: `trend_id`, `title`, `signal_count`, `unique_authors`, `platforms: list[str]`, `velocity_1h/6h/24h`, `sentiment`, `commercial_intent`, `novelty`, `coordination_risk`. No `platform` (singular), no `engagement_velocity`, `geo_spread`, `source_credibility` — those were old field names.
- **`GraphResult` fields**: use `final_verdict`, `final_score`, `final_confidence`, `final_priority`, `halt_reason`, `decisions`, `blocked_by`. Not `verdict`/`score`/`confidence` (those are on `AgentDecision`).
- **Optional extras**: `langgraph`, `chromadb`/`sentence-transformers`, `boto3` are optional. Install with `uv sync --all-extras`.
- **Phase 3 heuristic-first doctrine**: `aegis.predict` always produces a verdict from deterministic features. Neural models (PatchTST, Autoformer, TimesNet, HGT) can only REDUCE confidence by factor [0.5, 1.0] — they cannot flip a verdict. Guarantees zero-API-key test pass, <12ms p99 latency floor, full audit reproducibility.
- **Phase 3 ↔ Phase 2 bridge**: `aegis.agents_phase3_glue.bridge` maps `InferenceResult → AgentDecision` without importing LangGraph. SCOUT uses `p_breakout` at 24h horizon; SENTINEL uses `p_decline` at 6h. Bridge gracefully no-ops when `signals` are absent from `GraphState` (e.g. unit tests).
- **Structlog everywhere in Phase 2**: all agent nodes, bridge modules, and the runner use `structlog.get_logger("aegis.agents...")` with keyword-argument log calls (e.g. `_log.warning("event", key=val)`). Never use stdlib `logging` in `aegis.agents.*` or `aegis.agents_phase3_glue.*`.
- **`predict.resilience` vs `core.resilience`**: Two distinct APIs. `predict.resilience` is functional (`await resilient_call(op, name, timeout_s)`) for ML inference ops. `core.resilience` is decorator-based (`@resilient_call(policy)`) for scraper I/O. They coexist intentionally.
- **Phase 3 DB tables**: `predictions`, `prediction_audit`, `model_manifest`, `backtest_results` — all in migration `0002_predictions.sql`. All have RLS. `predictions` is a TimescaleDB hypertable on `finished_at`.
- **Amazon tier = T3_search**: Amazon adapter scrapes bestseller rankings (no prices), so tier is TIER_3_SEARCH not TIER_2_COMMERCE.
- **Phase 4 workspace**: `aegis-phase4/` is a uv workspace member. Use `uv sync --all-packages --all-extras` (not just `--all-extras`) so `aegis-execute`'s deps land in the shared venv.
- **Redis stream field name is `"body"`**: `runner._publish_phase2_result()` publishes `{"body": <json>}`. The Phase 4 `IntakeWorker._decode_payload()` reads `flat["body"]`. The dashboard `app.py` also reads `entry_data.get("body")`. Never use `"payload"` as the field name — that was a historical bug (fixed 2026-05-18).
- **Phase 2 stream payload is now enriched**: as of 2026-05-18, the `aegis:phase2:graph_results` stream entry includes not only Phase 4 summary fields (`final_verdict` in P4 vocabulary, `final_score`, etc.) but also `decisions` (per-agent breakdown), `raw_verdict` (original P2 vocabulary), `started_at`, `finished_at`, `duration_ms` for the dashboard. Phase 4 `IntakeWorker` ignores the extra fields — it only reads what `_parse_phase2_dict` maps.
- **Phase 2 → Phase 4 verdict mapping**: `AgentVerdict` values (`proceed`, `hold`, `block`, `escalate`) MUST be mapped to Phase 4 vocabulary (`ENTER`, `HOLD`, `BLOCK`) before publishing. Mapping: `proceed→ENTER`, `hold→HOLD`, `block→BLOCK`, `escalate→HOLD`. Applied in `runner._VERDICT_TO_PHASE4` and `execute.bridge.phase2._MAP`.
- **Phase 4 merge window**: `MERGE_WINDOW_S = 30.0`. If Phase 2 and Phase 3 results for the same `trend_id` arrive within 30 s, they are merged. Stale half-inputs are submitted as-is after window expires — never dropped.
- **Phase 4 killswitch**: controlled via `aegis-execute killswitch trip/arm` or Redis key `aegis:execute:killswitch`. When tripped, all outbound notification dispatch halts.
- **Phase 4 DB tables**: `alerts` (TimescaleDB hypertable on `created_at`), `alert_outbox`, `alert_deliveries`, `execution_intents`, `killswitch_audit` — migration `0003_execute.sql`. All use `app.current_tenant` RLS pattern.
- **Phase 4 execute mode**: `AEGIS_EXECUTE_MODE=advisory` (default). Advisory mode: pipeline runs, real actions gated. Set to `live` only in production.
- **Dashboard ops console security**: `POST /api/ops/run` only accepts commands whose first token is in `{"aegis", "uv", "python", "docker"}`. Do not broaden this allowlist without adding authentication.
- **Phase 5 confidence gate**: `aegis.scrape.confidence.score_batch()` runs on every batch in `scrape_topic()`. Score < `AEGIS_SCRAPE_CONFIDENCE_THRESHOLD` (default 0.85) logs `confidence.gate_failed` with `remediation_hints`. The pipeline always continues — confidence is advisory, never blocking.
- **Phase 5 velocity regression**: `aegis.scrape.analytics.compute_velocity_slope()` runs per-cluster in `detect_patterns()`. OLS slope > `HIGH_PRIORITY_SLOPE` (2.0 signals/hour) AND R² > 0.3 → `PatternCluster.is_high_priority=True`. High-priority clusters sort first in the output list. The threshold is configurable via `AEGIS_SCRAPE_VELOCITY_HIGH_PRIORITY_SLOPE`.
- **Phase 5 PCA denoising**: `pca_denoise_vectors()` is called on TF-IDF vectors in `detect_patterns()` before clustering. Falls back to identity (no-op) when numpy is absent or corpus < 3 signals. Retains 90% of variance by default.
- **Phase 5 `data_confidence` field**: the Phase 2 → Phase 4 stream payload now includes `data_confidence: float` (0–1). Populated from `TopicScrapeResult.batch_confidence` when `scrape_topic()` feeds `run_trend()`. Defaults to 1.0 for pipelines that bypass the scrape layer.
- **Phase 10 module path**: `aegis.datalake` lives at `src/aegis/datalake/` — a subpackage of the main `aegis` namespace. **Not** a separate workspace member. Import as `from aegis.datalake import DataLake`.
- **Phase 10 storage layout**: `{bucket}/{layer}/{table}/dt=YYYY-MM-DD/tenant_id={uuid}/{batch_id}.parquet` + `_manifest.json`. Both S3/MinIO and local-filesystem backends honour this layout. Use `DataLakeSettings(use_local_filesystem=True)` for offline dev and tests (no MinIO needed).
- **Phase 10 idempotent writes**: every Parquet batch has a content-addressable `batch_id` (sha256 of canonical JSON). Re-running the same data produces the same `batch_id` — safe to replay without duplicates.
- **Phase 10 catalog**: SQLite file at `~/.aegis/datalake/catalog.sqlite3` (configurable). Tracks registered tables and partitions. Run `aegis datalake migrate` after first install to create the schema.
- **Phase 10 DuckDB**: embedded in-process engine. Memory limit 2 GB, 4 threads (configurable). Tables are registered as views pointing to Parquet globs in the storage layer. Query timeout 30 s by default.
- **Phase 10 NaN check idiom**: `v != v` (and `f != f`) in `silver/builder.py` is the intentional IEEE-754 NaN check — suppressed with `# noqa: PLR0124`. Do not replace with `math.isnan()` as that raises on non-float types.
- **Phase 10 Prefect**: flows degrade gracefully — `prefect` is an optional extra. When absent, the decorator stubs return identity functions so the flow code imports and runs without a Prefect server. Install with `uv sync --extra datalake-orchestration`.
- **Phase 10 `os.replace` in LocalStorageBackend**: kept intentionally (not replaced with `Path.replace()`) so the unit test can monkeypatch `os.replace` to simulate atomic-rename failure. Suppressed with `# noqa: PTH105`.
- **Phase 10 dependencies**: `pyarrow>=17`, `duckdb>=1.1,<1.2`, `boto3>=1.34` are in the `datalake` extra. `prefect>=3,<4` is in `datalake-orchestration`. All core ingest (asyncpg, redis) is already in root deps.
- **Phase 11 module path**: `aegis.llm` lives at `src/aegis/llm/` — a top-level subpackage of the main `aegis` namespace. **Not** a workspace member. Import as `from aegis.llm import LLMGateway`.
- **Phase 11 backward compat**: `src/aegis/agents/llm/` (`LLMRouter`) is fully preserved. The `__init__.py` there re-exports `get_gateway`/`complete_for_agent` from `aegis.llm.bridge.agents_bridge` when Phase 11 is installed; gracefully falls back to `None` when absent. Agent nodes need no import changes.
- **Phase 11 provider priority order**: `ollama(0) → vllm(1) → groq(2) → openrouter(3) → gemini(4) → anthropic(5) → openai(6)`. Lower number = tried first. `ProviderSelector` skips unhealthy providers based on TTL-cached health checks (60 s TTL).
- **Phase 11 circuit breaker**: each `BaseProvider` has a per-instance `_CircuitState`. After `CIRCUIT_BREAKER_FAILURE_THRESHOLD` (5) consecutive failures the circuit opens. Auto-recovers after `CIRCUIT_BREAKER_RECOVERY_S` (60 s). Raises `CircuitOpen` immediately when open.
- **Phase 11 SemanticRouter**: pure-Python cosine similarity (no external library). Falls back gracefully when no routes match (`threshold=0.75` default). Short-circuits the LLM call entirely on a match — zero latency, zero tokens.
- **Phase 11 guardrails**: `GuardrailsValidator` runs on every LLM output. Max length 8192 chars, PII regex (email/phone/SSN/Aadhaar/PAN), toxic-pattern blocklist. Raises `GuardrailBlock` (AEGIS-LLM-0003). `PIIScrubber` runs on *input* side and redacts before sending to any provider.
- **Phase 11 InstructorAdapter**: extracts typed Pydantic models from raw LLM text. Injects a JSON schema instruction into the system prompt; parses with `model_validate_json()`; on failure, sends the error back to the LLM for self-correction (1 retry). Per-node schemas are in `aegis.llm.instructor.schemas`.
- **Phase 11 LLM cache**: key = SHA256(provider + model + messages + temperature). Bypassed when `temperature > 0.5`. In-process LRU (default 512 entries) + optional Redis layer. Cache hit returns instantly without provider call.
- **Phase 11 PromptRegistry**: Jinja2 templates with YAML front-matter (`name`, `version`, `required_vars`, `description`). `autoescape=False` is intentional — these are LLM text prompts, not HTML. All renders are logged to `_audit_log` for reproducibility.
- **Phase 11 bridge pattern**: `agents_bridge` holds the process-singleton `_gateway`; `complete_for_agent(node_name, messages)` is the one call agent nodes should use — it goes through the full circuit-breaker + cache + guardrails stack. `phase3_bridge` and `phase4_bridge` provide deterministic fallback strings when the gateway is unavailable.
- **Phase 11 Ollama**: `http2=False` (same rule as reddit-rss — Ollama's HTTP server doesn't support HTTP/2). Health check hits `/api/tags`. Model pull is via `/api/pull` (streaming). Set `AEGIS_DISABLE_OLLAMA=1` to skip in tests.
- **Phase 11 LiteLLM**: optional proxy behind Docker Compose profile `llm-proxy`. Start with `docker compose --profile llm-proxy up -d litellm`. Provides a unified OpenAI-compat endpoint aggregating all providers. Config: `config/litellm_config.yaml`.
- **Phase 11 dependencies**: `sentence-transformers>=3` is in the `llm` extra (for SemanticRouter embeddings). `tiktoken>=0.7` is in `llm-tokenizer` extra (for accurate token counting). All provider HTTP clients use `httpx` which is already a root dep.
- **Phase 11 Prometheus metrics**: `aegis.llm.metrics` defines counters/histograms for requests, latency, token usage, guardrail blocks, cache hits. Falls back to `_NoOpMetric` stubs when `prometheus_client` is absent — import never fails.

---

## Test environment

```bash
AEGIS_ENV=test
AEGIS_DISABLE_OLLAMA=1
AEGIS_AGENT_HMAC_KEY=test-key-do-not-use-in-prod
AEGIS_PG_DSN=postgresql://aegis_app:test@127.0.0.1:5432/aegis_test
AEGIS_REDIS_URL=redis://127.0.0.1:6379/15
```

Tests run in `asyncio_mode = auto` with `asyncio_default_fixture_loop_scope = "function"`.  
Coverage floor: 78% (`--cov-fail-under=78`). Current: ~79% (1389+ tests including Phase 11).  
Phase 4 tests run separately: 161 tests, no coverage threshold (standalone pytest config).  
Phase 11 provider adapters excluded from coverage (infrastructure-dependent; need live Ollama/Groq/etc.).

---

## Common gotchas

- **Tenants table PK is `tenant_id`**, not `id`.
- **Signals table** has no `engagement_score` — engagement fields are `views`, `likes`, `comments`, `shares`, `saves`.
- **Reddit public JSON API** blocks Brotli (`Accept-Encoding: br`) — must use gzip-only + `http2=False`.
- **`fetch_recent_signals(pool, tenant_id, limit, platform=None)`** — `pool` is a required positional arg.
- **LangGraph teardown** leaves Unix domain sockets open — suppress with `filterwarnings = ["ignore::ResourceWarning"]` in pytest config.
- **`content_hash mismatch`** warnings during scrape: pre-existing bug, signal is skipped safely.
- **Amazon titles are approximate** — extracted from URL slug (hyphens → spaces). Functional for trend detection.
- **TikTok/Pinterest/Nitter return 0 signals** — these adapters are broken (external API changes). Use the working adapters.
- **Log output format**: JSON by default when stdout is not a TTY (piped/WSL). Colored output in a real TTY.
- **Phase 4 HMAC key**: `AEGIS_EXECUTE_HMAC_KEY` must match between execute-api and execute-drain containers. Dev default is `dev-hmac-key-not-for-prod`.
- **Phase 4 tenant UUID**: `AEGIS_DEFAULT_TENANT_ID` (default `00000000-0000-0000-0000-000000000001`) must match between main stack and Phase 4.
- **Dashboard stream key**: dashboard reads `entry_data.get("body")` (not `"payload"`) from `aegis:phase2:graph_results`. The `"body"` field contains a JSON-encoded string of the full result payload.
- **`google-news` and `bing-news`** are available as `--source` options in `aegis scrape` as of 2026-05-18. They pass `--query` as the search term.
- **`scrape_topic()` vs individual `scrape`**: `scrape_topic` (used by `aegis topic`) runs up to ~30 parallel adapter tasks simultaneously. It uses its own per-task adapter instances and does NOT go through the CLI's `_build_adapter` path. Both paths are valid but separate code flows.
- **Phase 11 `agents.llm` shim**: `src/aegis/agents/llm/__init__.py` re-exports `get_gateway`/`complete_for_agent` from `aegis.llm.bridge.agents_bridge`. The `_phase11_available` flag (lowercase) is `True` when Phase 11 is installed. If you see `None` for those names, the `llm` extra is not installed.
- **Phase 11 Ollama http2=False**: `OllamaProvider` forces `http2=False` — Ollama's embedded HTTP server does not support HTTP/2 and will return `RemoteProtocolError` if `h2` is installed and negotiated.
- **Phase 11 OpenRouter 429 rotation**: on rate-limit, `OpenRouterProvider._next_free_model()` rotates through `OPENROUTER_FREE_MODELS` list and raises `RuntimeError` to trigger the gateway retry loop. The next retry picks the new model automatically.
- **Phase 11 Gemini message format**: Gemini does not use OpenAI `messages` format. `GeminiProvider` converts `[{"role": "user", "content": "…"}]` to Gemini `contents=[{"role": "user", "parts": [{"text": "…"}]}]` internally. System messages are prepended as a `user` turn.
- **Phase 11 `TokenUsage.cost_usd`**: this is a regular method (not a `@property`) because it takes `input_cost_per_1m` and `output_cost_per_1m` as arguments. Do not add `@property` — PLR0206 forbids properties with parameters.
- **Phase 11 PromptRegistry `autoescape=False`**: suppressed with `# noqa: S701`. These are LLM text templates, not HTML — autoescape would corrupt prompt content with HTML entities.
- **Phase 11 `_JINJA_AVAILABLE` in prompt_registry**: Pyright flags ALL_CAPS bool assigned in `try/except` as constant-redefinition. This is a known Pyright false positive; the code is correct Python. Do not rename the flag — just suppress or ignore the Pyright diagnostic.

---

## Operations manual

See `SYSTEM_TOUR.md` for a plain-English architectural tour covering all phases, the data flow, startup procedure, observability points, and expected baseline behaviour.

### Daily workflow (non-technical)

```bash
# One command does everything:
uv run aegis daily

# Or for a specific topic:
uv run aegis topic "your topic here"

# Then open the dashboard to see results:
# http://localhost:8300
```

### Phase 4 quick ops

```bash
curl -s http://localhost:8200/healthz
curl -s http://localhost:8200/readyz

uv run --package aegis-execute aegis-execute killswitch trip --reason "manual test"
uv run --package aegis-execute aegis-execute killswitch arm  --reason "all clear"
uv run --package aegis-execute aegis-execute tail \
  --tenant 00000000-0000-0000-0000-000000000001 --limit 20
```

---

## Swarm Intelligence Layer (Phases 1–6 Extension)

### Architecture

The Swarm Intelligence Layer adds 30+ free-only adapters orchestrated in 4 parallel waves.

**Files added:**
- `src/aegis/scrape/result.py` — AdapterRun, AgentHealth, AdapterCapabilities types
- `src/aegis/scrape/governor.py` — ConcurrencyGovernor (semaphores + token buckets)
- `src/aegis/scrape/normalizer.py` — Z-score + percentile + tier-weight normalization
- `src/aegis/scrape/sentiment.py` — Keyword-based finance/ecommerce sentiment classifier
- `src/aegis/scrape/schema_guard.py` — Schema drift detection + signal validation
- `src/aegis/scrape/ecommerce_utils.py` — FlareSolverr bypass + User-Agent rotation
- `src/aegis/scrape/swarm_agents.py` — SwarmAgentPool with health tracking
- `src/aegis/scrape/swarm.py` — SwarmOrchestrator (wave execution + synthesis)
- `src/aegis/scrape/swarm_result.py` — SwarmResult Pydantic model
- `src/aegis/scrape/sources/` — 30 new adapter files (see Adapter Registry below)

### Adapter Registry

| Adapter              | Platform            | Tier       | Risk   | Auth     |
|---------------------|---------------------|------------|--------|----------|
| techcrunch_rss      | techcrunch          | SEARCH     | low    | none     |
| wired_rss           | wired               | SEARCH     | low    | none     |
| bbc_business        | bbc_news            | SEARCH     | low    | none     |
| reuters_rss         | reuters             | SEARCH     | low    | none     |
| ndtv_profit         | ndtv_profit         | SEARCH     | low    | none     |
| mint_rss            | mint                | SEARCH     | low    | none     |
| business_standard   | business_standard   | SEARCH     | low    | none     |
| yahoo_finance_rss   | yahoo_finance       | SEARCH     | low    | none     |
| investing_com_rss   | investing_com       | SEARCH     | low    | none     |
| medium_rss          | medium              | SEARCH     | low    | none     |
| devto               | devto               | SOCIAL     | low    | none     |
| github_public       | github_public       | SEARCH     | low    | none     |
| reddit_finance      | reddit_finance      | SOCIAL     | medium | none     |
| reddit_ecommerce    | reddit_ecommerce    | SOCIAL     | medium | none     |
| youtube_rss         | youtube_rss         | SOCIAL     | medium | none     |
| google_trends_india | google_trends_india | SEARCH     | medium | none     |
| producthunt         | producthunt         | SOCIAL     | medium | none     |
| npm_trends          | npm_trends          | SEARCH     | low    | none     |
| moneycontrol        | moneycontrol        | SEARCH     | medium | none     |
| economic_times      | economic_times      | SEARCH     | medium | none     |
| nse_bse             | nse_bse             | COMMERCE   | medium | none     |
| screener_in         | screener_in         | SEARCH     | medium | none     |
| amazon_in           | amazon_in           | COMMERCE   | high   | none     |
| flipkart            | flipkart            | COMMERCE   | high   | none (FlareSolverr) |
| meesho              | meesho              | COMMERCE   | high   | none     |
| myntra              | myntra              | COMMERCE   | high   | none (FlareSolverr) |
| ajio                | ajio                | COMMERCE   | medium | none     |
| nykaa               | nykaa               | COMMERCE   | medium | none     |
| snapdeal            | snapdeal            | COMMERCE   | medium | none     |
| indiamart           | indiamart           | COMMERCE   | medium | none     |

### Redis Keys
- `aegis:swarm:latest` — latest SwarmResult JSON (TTL: 24h)
- `aegis:swarm:results` — Redis stream, one entry per swarm run
- `aegis:swarm:agent_health` — hash of agent health states (TTL: 7 days)

### CLI Commands
```bash
uv run aegis swarm run              # full swarm, persists to DB
uv run aegis swarm run --dry-run    # scrape only, no DB write
uv run aegis swarm run --limit 20   # limit signals per adapter
uv run aegis swarm agents           # print agent health table
uv run aegis daily --swarm          # run daily + full swarm
uv run aegis scrape --source flipkart --limit 50  # single adapter
```

### Key Design Decisions

- **Zero paid APIs**: all 30+ adapters use only free RSS, public HTML, or public JSON.
  No YouTube API v3, no Twitter API, no paid financial data.
- **Separated concerns**: SwarmOrchestrator delegates to SwarmAgentPool,
  SwarmAnalyzer, SwarmPersistence, SwarmSynthesizer — not one monolithic class.
- **Wave isolation**: failure in Wave 3 never blocks Wave 1 results from persisting.
- **Smart health tracking**: EMPTY (0 signals) ≠ failure. Error TYPE determines
  cooldown duration and DOWN threshold. Different thresholds per error type.
- **ConcurrencyGovernor**: max 5 concurrent HTTP requests globally,
  max 2 concurrent FlareSolverr bypass requests.
- **Signal normalization**: z-score within platform, percentile across swarm,
  tier-weighted final score — makes scores comparable across platforms.
- **India-first**: priority ordering in waves is India-first (Flipkart, NSE, Moneycontrol)
  with global coverage (Reuters, Yahoo, TechCrunch) as secondary.
- **DB dedup strategy**: Python-level semantic dedup between waves +
  DB-level `ON CONFLICT DO NOTHING` on `(platform, md5(url))` for exact matches.

### Common Gotchas

- **NSE headers**: NSE API returns 401/empty without `Referer: https://www.nseindia.com`.
  Also requires session cookie priming on some endpoints — handle gracefully.
- **Reddit**: `Accept-Encoding: gzip` only, `http2=False`, no Brotli.
  Same rules as existing reddit-rss adapter.
- **YouTube RSS**: channel feed URL requires `channel_id`, NOT handle (@name).
  Handle ≠ channel ID. Discover IDs from channel page if needed.
- **FlareSolverr**: must be running for Flipkart/Myntra HTML paths.
  If not running, adapters fall back to plain httpx (expect 403 on protected pages).
  `docker compose up -d aegis-flaresolverr` before running ecommerce adapters.
- **Meesho JSON**: endpoint frequently returns 401. HTML fallback is the reliable path.
- **ProductHunt**: unofficial public endpoint throttles at ~20 req/min. Use 3s delay.
- **NSE/BSE currency**: always set `raw_json["currency"] = "INR"` for Indian market data.
- **pytrends**: synchronous library — always run via `loop.run_in_executor()` to avoid
  blocking the event loop.
- **Schema drift**: e-commerce sites (Meesho, IndiaMart, AJIO) change their undocumented
  API schemas frequently. `schema_guard.fingerprint_response()` is logged but not blocking.
  Monitor `adapter_schema_fingerprints` table for unexpected hash changes.
- **GitHub rate limits**: unauthenticated = 60 req/hr. Check `X-RateLimit-Remaining`.
  Stop fetching if ≤ 5 remaining.
