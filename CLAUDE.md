# AEGIS Pulse — Claude Code Context

## Project overview

AEGIS Pulse is an autonomous market arbitrage intelligence engine.  
It scrapes signals from social platforms, stores them in TimescaleDB, and runs a 10-node LangGraph multi-agent pipeline to score and prioritise arbitrage opportunities.

**Phase 0** — Data ingestion (scrape adapters, HTTP pipeline, deduplication)  
**Phase 1** — Postgres/TimescaleDB persistence, Redis cache, MinIO object store  
**Phase 2** — Multi-agent intelligence (LangGraph DAG, LLM routing, ChromaDB memory)  
**Phase 3** — Predictive Apex (hybrid ML core: heuristic floor + optional neural augmentation; FastAPI serving; fractional-Kelly RL policy)

## Package layout

```
src/aegis/
  __init__.py          — package root, version 0.3.0
  config.py            — pydantic-settings Settings singleton
  cli/                 — Typer CLI (aegis scrape, aegis analyze, aegis signals tail, …)
  scrape/              — Phase 0: scrape adapters
  db/                  — Phase 1: asyncpg PgPool, signals/authors CRUD
  cache/               — Redis cache (RedisCache)
  core/                — metrics (Prometheus), logging (structlog)
  agents/              — Phase 2: multi-agent intelligence
    schemas.py         — TrendCandidate, AgentDecision, GraphResult (Pydantic v2)
    state.py           — GraphState TypedDict with annotated reducers
    graph.py           — LangGraph DAG builder (10 nodes + edges)
    runner.py          — run_trend() entrypoint; compiled-graph cache
    supervisor.py      — finalize node: aggregate decisions → final verdict
    nodes/             — 10 agent nodes; scout + sentinel wired to Phase 3 bridge
    llm/               — LLM router (Ollama→Groq→OpenRouter→Gemini fallback)
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
tests/
  unit/                — 663 unit tests (all pass, coverage ≥ 81%)
  unit/agents/         — 179 Phase 2 agent tests
  unit/predict/        — 163 Phase 3 predict tests
  integration/predict/ — 4 Phase 3 end-to-end tests (all pass)
db/
  migrations/          — SQL migrations (0001_init.sql + 0002_predictions.sql)
docker/
  Dockerfile.predict   — multi-stage, tini PID 1, non-root, optional ML build arg
alembic/               — Alembic migration scaffolding
config/                — Prometheus + Grafana configs
docker-compose.yml     — 8-service dev stack (+ predict on :8100)
docs/phase3/           — Phase 3 architecture, models, operations, integration docs
```

## Running the project

```bash
# Install dependencies
uv sync --all-extras

# Start infrastructure
docker compose up -d

# Scrape signals (use working adapters below)
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 20
uv run aegis scrape --source hacker-news --limit 30
uv run aegis scrape --source github-trending --limit 20
uv run aegis scrape --source amazon --limit 40

# Check what's in the DB
uv run aegis signals tail --limit 20

# Run the agent intelligence pipeline on collected signals
uv run aegis analyze --limit 20

# Run tests
uv run python -m pytest tests/unit/ -q -p no:hypothesis

# Run with coverage (addopts in pyproject.toml already injects --cov=aegis; do NOT pass it again)
uv run python -m pytest tests/unit/ -p no:hypothesis

# Run Phase 3 integration tests
uv run python -m pytest tests/integration/predict/ -v -p no:hypothesis
```

## Phase status — verified 2026-05-15

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 0 — Scrape | ✅ Green | 4 working adapters; 754 signals in DB across 4 platforms |
| Phase 1 — Persistence | ✅ Green | TimescaleDB, Redis, MinIO all healthy; all 8 docker services up |
| Phase 2 — Agents | ✅ Green | 10-node LangGraph DAG; SCOUT + SENTINEL wired to Phase 3 bridge |
| Phase 3 — Predict | ✅ Green | Heuristic-first ML core; 663 tests pass; 4/4 integration tests pass; coverage 84.19% |
| Orchestration | ✅ Green | `aegis analyze` + `aegis-predict` CLIs; FastAPI predict on :8100 |
| Code quality | ✅ Green | 0 ruff violations (64 auto-fixed 2026-05-15); 141 files formatted |

**All phases integrated and verified.** Final sign-off complete 2026-05-15. Ready for Phase 4.

---

## Scrape adapter status (as of 2026-05-08)

| Adapter | Status | Notes |
|---------|--------|-------|
| `reddit-rss` | ✅ Working | Best free Reddit option; no API key needed |
| `hacker-news` | ✅ Working | Reliable Algolia API; always fast |
| `github-trending` | ✅ Working | Direct GitHub HTML; very stable |
| `amazon` | ✅ Working | Bestseller rankings scrape; titles approximate (from URL slug); no prices |
| `google-trends` | ✅ Working | Requires pytrends (`uv sync --all-extras`); rate-limited at 0.2 req/s |
| `tiktok` | ❌ Broken | Creative Center API requires TikTok Ads auth (returns code 40101 "no permission") |
| `pinterest` | ❌ Broken | Unofficial search API returns 403 Forbidden; requires authenticated session |
| `nitter` | ❌ Broken | All public Nitter instances are dead or returning 403 |
| `instagram` | ⚠️ Requires setup | Very high ban risk; needs `allow_red_tos=True` + instaloader session cookie |
| `reddit` | ⚠️ Requires key | Set `AEGIS_REDDIT_CLIENT_ID` + `AEGIS_REDDIT_CLIENT_SECRET` in `.env` |
| `youtube` | ⚠️ Requires key | Set `AEGIS_YOUTUBE_API_KEY` in `.env` |

**Recommended daily scrape sequence** (no API keys needed):
```bash
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 50
uv run aegis scrape --source reddit-rss --subreddit Entrepreneur --limit 50
uv run aegis scrape --source hacker-news --limit 50
uv run aegis scrape --source github-trending --limit 30
uv run aegis scrape --source amazon --limit 80
```

## Infrastructure (docker-compose.yml)

| Service | Host port | Purpose |
|---------|-----------|---------|
| aegis-postgres | 5433 | TimescaleDB + pgvector |
| aegis-redis | 6380 | Cache + inter-agent streams |
| aegis-minio | 9002/9003 | Object store (raw/features/models/backups) |
| aegis-flaresolverr | 8191 | Cloudflare bypass |
| aegis-prometheus | 9091 | Metrics scrape |
| aegis-grafana | 3001 | Dashboards (admin/aegis_dev_admin_pw) |
| aegis-jaeger | 16687 | Distributed traces |

DB connection: `postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis`  
Redis: `redis://localhost:6380/0`

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
- **`predict.resilience` vs `core.resilience`**: Two distinct APIs. `predict.resilience` is functional (`await resilient_call(op, name, timeout_s)`) for ML inference ops. `core.resilience` is decorator-based (`@resilient_call(policy)`) for scraper I/O. They coexist intentionally — different ergonomics, different registries.
- **Phase 3 DB tables**: `predictions`, `prediction_audit`, `model_manifest`, `backtest_results` — all in migration `0002_predictions.sql`. All have RLS (same `app.current_tenant` pattern as Phase 1). `predictions` is a TimescaleDB hypertable on `finished_at`.
- **Amazon tier = T3_search**: Amazon adapter scrapes bestseller rankings (no prices), so tier is TIER_3_SEARCH not TIER_2_COMMERCE. If a future adapter adds prices, update `_PLATFORM_TIER` in `schemas/enums.py`.

## Test environment

```bash
AEGIS_ENV=test
AEGIS_DISABLE_OLLAMA=1
AEGIS_AGENT_HMAC_KEY=test-key-do-not-use-in-prod
AEGIS_PG_DSN=postgresql://aegis_app:test@127.0.0.1:5432/aegis_test
AEGIS_REDIS_URL=redis://127.0.0.1:6379/15
```

Tests run in `asyncio_mode = auto` with `asyncio_default_fixture_loop_scope = "function"`.  
Coverage floor: 78% (`--cov-fail-under=78`). Current: 79.54% (467 tests, verified 2026-05-08).

## Common gotchas

- **Tenants table PK is `tenant_id`**, not `id`.  
- **Signals table** has no `engagement_score` — engagement fields are `views`, `likes`, `comments`, `shares`, `saves`.
- **Reddit public JSON API** blocks Brotli (`Accept-Encoding: br`) — must use gzip-only + `http2=False`.
- **`fetch_recent_signals(pool, tenant_id, limit, platform=None)`** — `pool` is a required positional arg.
- **LangGraph teardown** leaves Unix domain sockets open — suppress with `filterwarnings = ["ignore::ResourceWarning"]` in pytest config.
- **`content_hash mismatch`** warnings during scrape: pre-existing bug, signal is skipped safely.
- **Amazon titles are approximate** — extracted from URL slug (hyphens → spaces), words like "a/the/of" missing. Functional for trend detection, not display.
- **TikTok/Pinterest/Nitter return 0 signals** — these adapters are broken (external API changes). This is expected; use the working adapters above.
- **Log output format**: logs are JSON by default when stdout is not a TTY (piped/WSL). Human-readable colored output appears when running in a real terminal with TTY.

## Operations manual

See `PHASE_2_OPERATIONS_MANUAL.md` for:
- All web UI URLs + credentials
- Start/stop/restart SOPs
- Manual pipeline execution commands
- Daily health checklist
- Troubleshooting table
