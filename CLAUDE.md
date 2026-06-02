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
**Phase 12** — Security & Secrets Layer (HashiCorp Vault KV v2 + Transit; SOPS/age env encryption; PII scrubbing pipeline with regex + spaCy NER; HMAC-signed append-only audit log with MinIO WORM archival; hierarchical RBAC; JWT auth; rate-limiting middleware; secret rotation manager; `aegis security` CLI)  
**Phase 13** — Testing & Quality Infrastructure (`aegis.testing` reusable helpers; pandera DataFrame schemas; 120 cross-phase unit tests; `fake_redis` / `fake_pg_pool` / `fake_llm_gateway` / `fake_minio` in-memory stubs; hypothesis property tests; `aegis-phase13` uv workspace member)  
**Phase 14** — Observability & SLO Framework (OpenTelemetry tracing → Jaeger; 24 Prometheus metrics; Sentry error tracking; Loki log aggregation + Promtail collector; Grafana Mission Control dashboard; SLO definitions with error budgets; `aegis.observability` subpackage)  
**Phase 15** — Disaster Recovery & Business Continuity (pgBackRest incremental Postgres backups; restic encrypted filesystem snapshots; backup health monitor; `src/aegis/backup/`; full DR orchestrator with RPO 15 min / RTO 60 min, weekly restore drill automation, `aegis dr` CLI; `aegis-phase15` standalone module)  
**Phase 6** — Capital Execution Engine (three-tier advisory/staging/live model; fractional-Kelly 0.25× position sizing; Telegram approval workflow for P0/P1 plans; dynamic RL pricing engine; Printful POD + CJ Dropshipping + Shopify fulfillment clients; `SettlementManager` EOD PnL reconciliation; daily drawdown circuit breaker; `/capital/*` REST API; migration `0007_capital_execution.sql`; 55 unit tests; `src/aegis/fulfillment/` package)  
**Phase 7** — Geospatial Intelligence & Cross-Market Arbitrage (WTO MFN tariff schedule 2024 for 20 HS codes × 8 regions; ECB FX rates via Frankfurter API — no key needed; EMS/postal shipping matrix 8×8 regions; `CrossMarketAnalyzer` enumerates all O×D pairs ranked by `gross_margin_pct × demand_intensity × market_size`; demand intensity from Phase 1 signals DB; Phase 6 bridge → `ExecutionIntent`; `/geo/*` REST API; `aegis geo` CLI; DB migration `0008_geo_intelligence.sql`; `docs/adr/0007-geospatial-intelligence.md`; `src/aegis/geo/` package)  
**Phase 8** — Regulatory & Compliance Engine (composite 7-dimension risk matrix: trademark/patent/FDA/counterfeit/FTC/privacy/AML; real APIs — USPTO PatentsView, EUIPO TMview, FDA OpenFDA, Trade.gov CSL; OFAC + FATF sanctions; GDPR/DPDP/DSA/GPSR/CCPA privacy rules; FTC rule engine with 30+ compiled patterns; counterfeit detector with text heuristics + optional CLIP; Phase 6 `gate_execution_plan()` integration; `/compliance/*` REST API; `aegis compliance` CLI; DB migration `0009_compliance.sql`; `docs/adr/0008-compliance-engine.md`; `src/aegis/compliance/` package; 80 unit tests)  
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
  observability/       — Phase 14: Observability & SLO Framework
    __init__.py        — exports init_tracing, init_metrics, init_sentry, get_tracer, instrument_fastapi; AEGIS_OBSERVABILITY_VERSION = "14.0.0"
    tracing.py         — OpenTelemetry OTLP → Jaeger; init_tracing(service_name); get_tracer(); instrument_fastapi(app); _NoOpTracer fallback
    metrics.py         — 24 Prometheus metric definitions (counters/histograms/gauges); start_metrics_server(port=8001); _lookup_existing() for namespace collision avoidance
    sentry_init.py     — Sentry SDK init with FastAPI/asyncpg/httpx integrations; reads SENTRY_DSN; no-op when absent
  backup/              — Phase 15: Disaster Recovery & Business Continuity (integrated into main namespace)
    __init__.py        — exports BackupManager, ResticBackup, BackupHealth, backup_settings
    settings.py        — BackupSettings (AEGIS_BACKUP_* env prefix): pgBackRest stanza, restic repo, MinIO bucket, health thresholds
    errors.py          — AegisBackupError hierarchy: BackupCommandError, BackupTimeoutError, ResticCommandError, RestoreError, BackupInitError
    pgbackrest_manager.py — BackupManager: incremental pgBackRest Postgres backups + BackupMetadata
    restic_manager.py  — ResticBackup: encrypted filesystem snapshots + ResticSnapshot listing
    health.py          — BackupHealth: staleness monitor + consecutive-failure alerting
    cli.py             — Click CLI: dr-status, dr-backup, dr-restore, dr-health (registered under main aegis CLI)
  geo/                 — Phase 7: Geospatial Intelligence & Cross-Market Arbitrage
    __init__.py        — exports CrossMarketAnalyzer, Region, GeoOpportunity, REGION_CONFIGS; __version__ = "7.0.0"
    config.py          — Region enum, RegionConfig, HS_TARIFF_SCHEDULE (20 HS codes × 8 regions, WTO MFN 2024), SHIPPING_MATRIX_USD, CATEGORY_MEDIAN_PRICES_USD
    schemas.py         — GeoOpportunity, GeoArbitrageReport, TariffLookupResult, RegionalDemandSnapshot (frozen Pydantic v2)
    fx.py              — FXRateFetcher: Frankfurter/ECB rates, in-process TTL cache, fallback midpoints
    tariffs.py         — TariffEstimator: WTO MFN static schedule + optional UN Comtrade fallback
    shipping.py        — ShippingResolver: 8×8 EMS/postal matrix + optional ShipEngine live quotes
    demand.py          — RegionalDemandAnalyzer: Phase 1 signals DB velocity + price data; synthetic fallback
    arbitrage.py       — CrossMarketAnalyzer: enumerate all O×D pairs, score by margin × demand × market_size
    phase6_bridge.py   — geo_opportunity_to_execution_intent(): GeoOpportunity → Phase 6 ExecutionIntent
    api.py             — FastAPI router: /geo/health /geo/analyze /geo/fx /geo/tariff /geo/shipping
    cli.py             — Click CLI: analyze, fx, tariff, shipping, regions
  compliance/          — Phase 8: Regulatory & Compliance Engine
    __init__.py        — exports ComplianceEngine, ComplianceRiskAssessment, ComplianceRequest, ComplianceSettings; __version__ = "8.0.0"
    config.py          — ComplianceSettings (AEGIS_COMPLY_* env prefix): API keys, risk thresholds, cache TTLs
    constants.py       — RISK_WEIGHTS, OFAC_SANCTIONED_COUNTRIES, FATF_HIGH_RISK, GDPR_COUNTRIES, DPDP_COUNTRIES, FTC_VIOLATION_PATTERNS, KNOWN_TRADEMARK_BRANDS
    errors.py          — AegisComplianceError hierarchy: TrademarkRiskError, FDAViolationError, SanctionViolationError, ComplianceBlockError, etc.
    schemas.py         — TrademarkMatch, PatentMatch, FDAEnforcement, SanctionMatch, CounterfeitSignal, FTCViolation, PrivacyRisk, ComplianceRiskAssessment, RiskBreakdown (frozen Pydantic v2)
    cache.py           — ComplianceCache: in-process TTL dict; key = SHA-256(sku + title + origin + dest)
    ipr.py             — IPRChecker: USPTO PatentsView API (patents) + EUIPO TMview API (EU trademarks) + local brand DB fuzzy match
    fda.py             — FDAChecker: OpenFDA /drug /food /device enforcement endpoints; banned-keyword fast path
    ftc.py             — FTCRuleEngine: 30+ compiled regex patterns for FTC Act § 5, 16 CFR 255, 16 CFR 362 violations
    privacy.py         — PrivacyRiskAssessor: GDPR/DPDP/DSA/GPSR/CCPA/PIPEDA/COPPA rule engine with word-boundary matching
    aml.py             — AMLChecker: OFAC country sanctions + FATF grey/black list + Trade.gov CSL entity screening
    counterfeit.py     — CounterfeitDetector: exact brand match + fuzzy (difflib) + price anomaly + replica keywords + optional CLIP
    engine.py          — ComplianceEngine: parallel asyncio.gather across all checkers; hard overrides for OFAC/FDA/replica; FTC escalate override; Phase 12 audit integration
    api.py             — FastAPI router: /compliance/health /assess /assess/batch /trademark/{query} /sanctions/{country} /ftc
    cli.py             — Click CLI: assess, trademark, sanctions, ftc, batch
  dashboard/           — Command Center web UI (FastAPI :8300)
    app.py             — REST + SSE backend (aggregates Postgres, Redis, Phase3, Phase4, Docker)
    cli.py             — `aegis dashboard serve` entry point
    static/index.html  — SPA: dark-theme, sidebar nav, ChartJS charts, SSE live feed,
                         streaming ops console (runs CLI commands and streams output)
aegis-harden/          — Phase 5 Hardening: adversarial bot-resilience library (uv workspace member)
  src/aegis/harden/
    __init__.py        — exports DEFAULT_HARDEN_PROFILE, PHASE5_VERSION
    schemas.py         — TLSFingerprint, H2Settings, FingerprintProfile, Playbook, HardenVerdict (frozen Pydantic v2)
    fingerprint/       — FingerprintPool: 8 curated JA3/JA4/H2 browser profiles; pick(rng) is deterministic
    proxy/             — ProxyPosture: pure policy (no I/O); decides proxy pool index from playbook + rng
    honeypot/          — score_url(), score_element(), filter_safe(): URL + DOM trap detection
    detect/            — screen_url(), screen_inference(), screen_training_batch() → HardenVerdict
    playbooks/         — PlaybookRegistry + builtin_registry(); YAML per-source configs (delay, jitter, proxy profile)
    bridge_phase4.py   — publishes HardenVerdict to aegis:phase5:verdicts Redis stream
    utils/rng.py       — SeededRng: stdlib + numpy, same seed → deterministic fingerprint sequences
  playbooks/           — YAML playbooks: 00-default.yaml, 10-reddit-rss.yaml, 20-hacker-news.yaml, …
  tests/unit/harden/   — 216+ tests at ~95% coverage (run separately; see below)
src/aegis/scrape/
  harden_shim.py       — HardenShim: single integration callsite for all scrape adapters
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
  unit/                — unit tests (phases 0–14; coverage ≥ 78%)
  unit/agents/         — 179 Phase 2 agent tests
  unit/predict/        — 163 Phase 3 predict tests
  unit/observability/  — 42 Phase 14 observability tests (test_tracing.py, test_metrics.py, test_sentry.py)
  integration/predict/ — 4 Phase 3 end-to-end tests (all pass)
aegis-phase15/         — Phase 15 DR orchestrator (standalone; NOT yet a uv workspace member)
  src/aegis/dr/        — DrOrchestrator, RestoreDrill, DrHealthChecker, BackupManifest, SlaSnapshot
    config.py          — DisasterRecoverySettings (AEGIS_DR_* env prefix): RPO/RTO targets, MinIO, Redis, drill settings
    constants.py       — RPO_TARGET_S=900, RTO_TARGET_S=3600, DRILL_INTERVAL_S=604800, error codes AEGIS-DR-0001..0030
    schemas.py         — BackupManifest, DrillResult, SlaSnapshot, RecoveryPlan (frozen Pydantic v2)
    orchestrator.py    — DrOrchestrator: asyncio task loop for all backup jobs + drill scheduler
    drill.py           — RestoreDrill: on-demand or weekly automated restore validation
    health.py          — DrHealthChecker: RPO drift + SLA snapshot computation
    backup/            — postgres.py, redis.py, restic.py, models.py (ModelRegistryBackup)
    restore/           — postgres.py, __init__.py
    api.py             — FastAPI router (/dr/* routes for dashboard integration)
    cli.py             — Click: dr backup|restore|drill|status|runbook|health
    runbooks/          — YAML failure-mode runbooks (pg_corruption, redis_oom, disk_full, docker_dead, …)
  tests/               — 5 test files: test_dr_schemas, test_dr_backup_postgres, test_dr_drill, test_dr_health, test_dr_orchestrator
db/
  migrations/          — SQL migrations (0001_init.sql + 0002_predictions.sql +
                         0003_execute.sql + 0004_new_platforms.sql)
docker/
  Dockerfile.predict   — multi-stage, tini PID 1, non-root, optional ML build arg
  Dockerfile.dashboard — multi-stage dashboard server
alembic/               — Alembic migration scaffolding
config/
  prometheus/          — Prometheus scrape config
  grafana/             — Grafana provisioning (datasources: Prometheus, Loki; dashboards: AEGIS Mission Control)
  loki/loki.yaml       — Phase 14 Loki log aggregation server config
  promtail/config.yaml — Phase 14 Promtail log collector config (ships Docker container logs → Loki)
docker-compose.yml     — 16-service dev stack (postgres, redis, minio, flaresolverr,
                         predict :8100, execute-api :8200, execute-drain,
                         dashboard :8300, prometheus, grafana, jaeger,
                         ollama :11434, ollama-init [one-shot], litellm :8080 [profile: llm-proxy],
                         loki :3100 [Phase 14], promtail [Phase 14 log shipper])
docs/phase3/           — Phase 3 architecture, models, operations, integration docs
docs/adr/              — Architecture Decision Records (0014-observability.md, 0015-disaster-recovery.md)
docs/slo/              — SLO definitions with error budgets (aegis-slos.md)
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
| http://localhost:3001 | Grafana — AEGIS Mission Control + infra metrics (admin / aegis_dev_admin_pw) |
| http://localhost:9091 | Prometheus — raw metrics |
| http://localhost:16687 | Jaeger — distributed traces (Phase 14 OTel) |
| http://localhost:3100 | Loki — log aggregation API (Phase 14; query via Grafana Explore) |
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

### 13. Phase 14 Observability CLI / integration

```bash
# The observability package is used programmatically at service startup — no standalone CLI.
# To instrument a FastAPI service:
#   from aegis.observability import init_tracing, init_sentry, get_tracer, instrument_fastapi
#   init_tracing(service_name="aegis-dashboard")
#   init_sentry()
#   instrument_fastapi(app)

# View logs in Grafana (Loki datasource):
#   http://localhost:3001 → Explore → select "Loki" → search {container_name="aegis-dashboard"}

# Prometheus metrics scrape endpoint (port 8001 when start_metrics_server() is called):
curl -s http://localhost:8001/metrics | grep aegis_obs_

# OTLP traces flow to Jaeger automatically when OTEL_ENABLED=true (default):
# http://localhost:16687
```

### 14. Phase 15 Disaster Recovery CLI

```bash
# Backup operations:
uv run aegis dr backup                         # backup all targets
uv run aegis dr backup --target postgres       # postgres only
uv run aegis dr backup --target redis          # redis only
uv run aegis dr backup --target models         # model registry only
uv run aegis dr backup --target restic         # encrypted filesystem snapshot

# Restore (dry-run by default — always check before applying):
uv run aegis dr restore --target postgres --dry-run
uv run aegis dr restore --target postgres      # actual restore

# Weekly restore drill (validates backup integrity without touching prod):
uv run aegis dr drill
uv run aegis dr drill --dry-run

# SLA status (RPO target: 15 min, RTO target: 60 min):
uv run aegis dr status
uv run aegis dr health                         # continuous watch
uv run aegis dr health --watch

# Failure-mode runbook (print response procedure for a specific scenario):
uv run aegis dr runbook pg_corruption
uv run aegis dr runbook redis_oom --dry-run
# Available modes: pg_corruption, redis_oom, disk_full, docker_dead, laptop_stolen,
#                  network_outage, wsl_crash
```

### 15. Phase 7 Geospatial Intelligence CLI

```bash
# Find cross-market arbitrage opportunities for a product (no DB / no API key needed)
uv run aegis geo analyze "TSHIRT-001" "Classic Cotton T-Shirt" --category apparel --top-n 5
uv run aegis geo analyze "PHONE-001" "Budget Smartphone" --category smartphones --json-out

# Live FX rates from ECB (no API key)
uv run aegis geo fx
uv run aegis geo fx --json-out

# WTO MFN tariff lookup
uv run aegis geo tariff 610910 IN --value 100        # cotton T-shirt → India
uv run aegis geo tariff 851712 US --value 500        # smartphone → US (0%)
uv run aegis geo tariff 640411 EU --value 80         # sneakers → EU

# Shipping cost quote (EMS/postal economy tier)
uv run aegis geo shipping US IN                      # US → India, 0.5 kg
uv run aegis geo shipping CN US --weight 1.0         # China → US, 1 kg

# List supported regions with market size data (World Bank 2024)
uv run aegis geo regions
```

### 16. Phase 8 Regulatory & Compliance CLI

```bash
# Full compliance risk assessment for a product + trade route
uv run aegis compliance assess "SKU-001" "Classic Cotton T-Shirt" \
  --category apparel --origin CN --dest US --price 15.00

# Output raw JSON
uv run aegis compliance assess "SKU-001" "Louis Vuitton Inspired Bag" \
  --category luxury --origin CN --dest US --json-out

# Trademark lookup (USPTO PatentsView + EUIPO TMview live APIs)
uv run aegis compliance trademark "Nike"
uv run aegis compliance trademark "Cotton T-Shirt" --description "athletic apparel"

# OFAC / FATF sanctions check for a country
uv run aegis compliance sanctions IR      # Iran — BLOCKED
uv run aegis compliance sanctions NG      # Nigeria — FATF grey list
uv run aegis compliance sanctions US      # USA — clear

# FTC advertising rule engine (no API call — instant)
uv run aegis compliance ftc \
  --title "Miracle Weight Loss Supplement" \
  --description "FDA-Approved! Guaranteed to cure cancer!"

# Batch assessment from JSON file (up to 20 products concurrently)
uv run aegis compliance batch products.json
uv run aegis compliance batch products.json --json-out
```

### 17. Stack lifecycle

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

### 16. Tests

```bash
# Phases 0-14 unit tests (quick, no infra):
uv run python -m pytest tests/unit/ -q -p no:hypothesis

# With coverage report (floor: 78%):
uv run python -m pytest tests/unit/ -p no:hypothesis

# Phase 3 integration tests (requires running postgres + redis):
uv run python -m pytest tests/integration/predict/ -v -p no:hypothesis

# Phase 4 unit tests:
uv run python -m pytest aegis-phase4/tests/ -q -p no:hypothesis

# Phase 5 Hardening unit tests (run separately — different hypothesis profile):
uv run --package aegis-harden python -m pytest aegis-harden/tests/ -q -p no:hypothesis

# Phase 14 observability unit tests (included in tests/unit/observability/):
uv run python -m pytest tests/unit/observability/ -q -p no:hypothesis

# Phase 15 DR unit tests — aegis-phase15 uses its own venv (standalone module):
cd aegis-phase15 && uv sync --extra dev && .venv/bin/python -m pytest tests/ -q -p no:hypothesis

# Phase 15 backup unit tests (included in main test suite):
uv run python -m pytest tests/unit/backup/ -q -p no:hypothesis
```

---

## Phase status — verified 2026-05-31

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 0 — Scrape | ✅ Green | 7 working adapters (reddit-rss, hn, github, amazon, google-news, bing-news, google-trends); topic intelligence with parallel multi-source harvest; CPU ops offloaded via `asyncio.to_thread` |
| Phase 1 — Persistence | ✅ Green | TimescaleDB, Redis, MinIO all healthy; semantic dedup sweeper; B2B supply chain schema in migration 0006 |
| Phase 2 — Agents | ✅ Green | 10-node LangGraph DAG; sentinel always runs on all paths; stream capped at 10k; phase3_bundle stripped from state |
| Phase 3 — Predict | ✅ Green | Heuristic-first ML core; 4/4 integration tests pass; coverage 79.85% |
| Phase 4 — Execute | ✅ Green | Alert pipeline + killswitch + SSE; 164 tests pass; Vyapar bridge stub added; `vyapar_webhook_url` field in ExecuteSettings |
| Phase 5 — Autonomous Scale | ✅ Green | OLS velocity + PCA denoising + confidence gate + SwarmOrchestrator multi-wave parallel harvest + topic intelligence routing + `aegis swarm` CLI |
| Phase 5 — Hardening (aegis-harden) | ✅ Green | TLS/JA3/JA4 fingerprint diversity + per-source YAML playbooks + honeypot detection + HardenShim integration; session ContextVar API added; graceful degradation when not installed; 0 ruff violations |
| Phase 10 — Data Lake | ✅ Green | Bronze/Silver/Gold medallion over Parquet on MinIO; DuckDB query engine; Prefect 3 orchestration (Pydantic 2.11 compat fixed); `aegis datalake` CLI; 0 ruff violations; integrated at `src/aegis/datalake/` |
| Phase 11 — LLM Orchestration | ✅ Green | LLMGateway with circuit breaker + health-based selector; GuardrailBlock fail-fast (no provider rotation); Ollama→Groq→OpenRouter→Gemini fallback; SemanticRouter; InstructorAdapter; `aegis llm` CLI |
| Phase 12 — Security & Secrets | ✅ Green | `aegis-phase12` uv workspace member; `aegis.security` exposed via `__path__` extension; VaultClient (circuit breaker + token renewal); PIIScrubber (regex + spaCy NER); AuditLogger (HMAC-signed chain-hash + MinIO WORM); RBACEnforcer (hierarchical roles); JWTManager; RateLimitMiddleware; SecretRotationManager; 253 unit tests pass; 0 ruff violations |
| Phase 13 — Testing & Quality | ✅ Green | `aegis-phase13` uv workspace member; `aegis.testing` exposed via `__path__` extension; 120 unit tests pass (33 skipped for optional infra); pandera DataFrame schemas; hypothesis property tests; `fake_redis`/`fake_pg_pool`/`fake_llm_gateway`/`fake_minio` stubs; `aegis-test` CLI; 0 ruff violations |
| Phase 14 — Observability & SLO | ✅ Green | `aegis.observability` at `src/aegis/observability/`; OTel tracing → Jaeger; 24 Prometheus metrics (namespaced `_obs_`); Sentry error tracking; Loki + Promtail in docker-compose; Grafana Mission Control dashboard; SLO docs; 42 unit tests; 0 ruff violations |
| Phase 15 — Disaster Recovery | ✅ Green | `src/aegis/backup/` — `BackupManager` (pgBackRest, WAL archiving, PITR), `ResticBackup` (encrypted filesystem), `BackupHealth` (staleness monitor + alerts); `aegis backup` CLI (create/list/restore/prune/health); `bootstrap/wsl/backup_wsl_disk.sh`; `docs/DR_RUNBOOK.md` + `docs/adr/0015-disaster-recovery.md`; 58 unit tests pass; 0 ruff violations; RPO ≤ 15 min / RTO ≤ 60 min. `aegis-phase15/` standalone DR orchestrator: `DrOrchestrator`, `RestoreDrill`, `DrHealthChecker`, FastAPI router, `aegis dr` CLI; 39 unit tests pass; 0 ruff violations |
| Phase 6 — Capital Execution | ✅ Green | `aegis-phase4/src/aegis/execute/` extended: `engine.py` (ExecutionEngine, ExecutionPlan, Kelly sizing, drawdown circuit breaker), `pricing.py` (PricingStrategy, multi-objective weighted pricing, A/B test, online learning), `approval.py` (ApprovalBroker, Telegram inline-button workflow), `settlement.py` (SettlementManager, DailySettlement, tax CSV); `src/aegis/fulfillment/` package (PrintfulClient POD, CJDropshipClient, ShopifyClient); `/capital/*` REST API routes; DB migration `0007_capital_execution.sql`; `docs/adr/0006-capital-execution.md`; 55 unit tests pass; 0 ruff violations; advisory mode default — zero capital at risk in CI |
| Phase 7 — Geospatial Intelligence | ✅ Green | `src/aegis/geo/` — `CrossMarketAnalyzer` (all O×D region pairs, real WTO tariffs, ECB FX, EMS shipping); `FXRateFetcher` (Frankfurter/ECB, no key, 1h TTL cache); `TariffEstimator` (20 HS codes × 8 regions, WTO MFN 2024); `ShippingResolver` (8×8 matrix, EMS/postal 2024); `RegionalDemandAnalyzer` (Phase 1 signals DB + synthetic fallback); `geo_opportunity_to_execution_intent()` Phase 6 bridge; `/geo/*` REST API; `aegis geo` CLI (analyze, fx, tariff, shipping, regions); DB migration `0008_geo_intelligence.sql`; `docs/adr/0007-geospatial-intelligence.md`; 60+ unit tests; 0 ruff violations |
| Phase 8 — Compliance Engine | ✅ Green | `src/aegis/compliance/` — 7-dimension risk matrix (trademark 22%, FDA 20%, counterfeit 15%, patent 13%, privacy 10%, FTC 10%, AML 10%); `IPRChecker` (USPTO PatentsView + EUIPO TMview live APIs + 40-brand local DB); `FDAChecker` (OpenFDA /drug /food /device enforcement API, no key); `FTCRuleEngine` (30+ compiled regex, zero I/O); `PrivacyRiskAssessor` (GDPR/DPDP/DSA/GPSR/CCPA/COPPA); `AMLChecker` (OFAC 17-country list + FATF 2024 grey/black list + optional Trade.gov CSL entity screening); `CounterfeitDetector` (exact + fuzzy brand match + price anomaly + replica keywords + optional CLIP); `ComplianceEngine` (parallel asyncio.gather, hard BLOCK overrides, 6-hour result cache); `gate_execution_plan()` Phase 6 integration; `/compliance/*` REST API; `aegis compliance` CLI (assess, trademark, sanctions, ftc, batch); DB migration `0009_compliance.sql`; `docs/adr/0008-compliance-engine.md`; 80 unit tests; 0 ruff violations |
| Dashboard | ✅ Green | Command Center on :8300; asyncpg + Redis shared pools; SSE uses shared pool (no per-tab connections); ops console |
| Orchestration | ✅ Green | `aegis analyze` / `aegis topic` / `aegis daily` / `aegis swarm` → full pipeline → Phase 4 stream |
| Docker infra | ✅ Green | 16-service stack; resource limits on all stateful services; `docker-compose.enterprise.yml` overlay; Loki + Promtail added (Phase 14) |
| Code quality | ✅ Green | 0 ruff violations across all phases; 1589+ unit tests pass (main + Phase 8); coverage floor met |

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
| aegis-grafana | 3001 | Dashboards + AEGIS Mission Control (admin/aegis_dev_admin_pw) |
| aegis-jaeger | 16687 | Distributed traces (OTel OTLP receiver) |
| aegis-loki | 3100 | Phase 14 log aggregation (Promtail → Loki → Grafana Explore) |
| aegis-promtail | — | Phase 14 log collector (ships Docker container logs → Loki; no exposed port) |

DB connection: `postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis`  
Redis: `redis://localhost:6380/0`

**Resource limits** (added 2026-05-29 audit): all stateful services have `deploy.resources.limits` set in `docker-compose.yml`. Ollama is capped at 8G/4cpu with `OLLAMA_NUM_CTX=8192` and `OLLAMA_MAX_LOADED_MODELS=1` (was 32768 ctx / 2 models).  
**Enterprise overlay**: `docker-compose.enterprise.yml` — apply with `-f docker-compose.yml -f docker-compose.enterprise.yml`. Adds tighter caps, removes docker.sock from dashboard, restricts `OLLAMA_ORIGINS`, adds `AEGIS_DISABLE_OPS_CONSOLE=true`.

---

## Key design decisions

- **Heuristic-first**: every agent produces a deterministic verdict from numeric features; LLM enhances reasoning text only. Pipeline always produces a result even with no LLM keys.
- **Phase 2 graph sentinel invariant (2026-05-29)**: SENTINEL always runs before COMPLIANCE regardless of scout strength or sourcer outcome. Weak scout (`score < 0.55` or BLOCK verdict) bypasses sourcer+auditor but routes `historian → sentinel → compliance`. No-supplier from sourcer routes `sourcer → sentinel → compliance`. This ensures saturation/exit signals are never silently dropped. The conditional edge maps are `{"sourcer": "sourcer", "sentinel": "sentinel"}` for historian and `{"auditor": "auditor", "sentinel": "sentinel"}` for sourcer.
- **`scrape_topic` is async-safe for CPU ops**: `score_batch`, `deduplicate_batch`, and `detect_patterns` are synchronous CPU/parse functions; they are always called via `await asyncio.to_thread(fn, ...)` inside `scrape_topic` to avoid blocking the event loop during heavy harvests.
- **`playwright_fetcher` browser pool**: `fetch_page_html` reuses a single module-level Chromium process (`_acquire_browser()`). Each call gets a fresh browser context (no cross-request state leakage). A `Semaphore(2)` caps concurrent page fetches. Do NOT call `browser.close()` inside `fetch_page_html` — call `ctx.close()` instead.
- **`phase3_bundle` is NOT in `phase3_decision` state key**: `bridge._enrich()` strips `phase3_bundle` and `phase3_audit` before storing `phase3_decision` in GraphState. Full Phase 3 audit data lives in `phase3_result`. This prevents LangGraph state bloat when signals lists are large.
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
- **Phase 2 stream is capped**: `runner._publish_phase2_result()` calls `xadd(..., maxlen=10_000, approximate=True)`. This prevents unbounded Redis memory growth on long-running scrape campaigns. The agent messaging bus (`aegis.agents.messaging.streams`) was already capped at 10k.
- **Phase 2 → Phase 4 verdict mapping**: `AgentVerdict` values (`proceed`, `hold`, `block`, `escalate`) MUST be mapped to Phase 4 vocabulary (`ENTER`, `HOLD`, `BLOCK`) before publishing. Mapping: `proceed→ENTER`, `hold→HOLD`, `block→BLOCK`, `escalate→HOLD`. Applied in `runner._VERDICT_TO_PHASE4` and `execute.bridge.phase2._MAP`.
- **Phase 4 merge window**: `MERGE_WINDOW_S = 30.0`. If Phase 2 and Phase 3 results for the same `trend_id` arrive within 30 s, they are merged. Stale half-inputs are submitted as-is after window expires — never dropped.
- **Phase 4 killswitch**: controlled via `aegis-execute killswitch trip/arm` or Redis key `aegis:execute:killswitch`. When tripped, all outbound notification dispatch halts.
- **Phase 4 DB tables**: `alerts` (TimescaleDB hypertable on `created_at`), `alert_outbox`, `alert_deliveries`, `execution_intents`, `killswitch_audit` — migration `0003_execute.sql`. All use `app.current_tenant` RLS pattern.
- **Phase 4 execute mode**: `AEGIS_EXECUTE_MODE=advisory` (default). Advisory mode: pipeline runs, real actions gated. Set to `live` only in production.
- **Dashboard ops console security**: `POST /api/ops/run` only accepts commands whose first token is in `{"aegis", "uv", "python", "docker"}`. Do not broaden this allowlist without adding authentication.
- **Dashboard connection pools**: `app.py` uses module-level `_pg_pool` (asyncpg, min=2 max=10) and `_redis_pool` (aioredis, max_connections=10) singletons created in FastAPI lifespan. All handlers use `_acquire_pg()` and `_get_redis()` — never call `asyncpg.connect()` or `aioredis.from_url()` directly inside a handler.
- **Dashboard SSE uses shared pool**: `sse_events()` `_generate()` calls `_get_redis()` (shared pool) — do **not** call `aioredis.from_url()` inside the generator and do **not** call `aclose()` on disconnect. Each SSE browser tab previously created a dedicated Redis TCP connection; this is now fixed (2026-05-29).
- **Dashboard CORS**: restricted to `AEGIS_DASHBOARD_ALLOWED_ORIGINS` (default `http://localhost:8300,http://localhost:3001`). Do not set to `*` — the ops console executes shell commands.
- **Phase 4 SEC-013**: killswitch audit write failures (trip/arm) log at WARNING; outbox drainer metrics update + delivery metrics failures log at DEBUG. All four are best-effort non-fatal paths — `# noqa: BLE001` not needed because structlog calls are the correct handling.
- **Phase 4 Vyapar bridge**: `aegis-phase4/src/aegis/execute/bridge/vyapar.py` — stub B2B webhook integration. `build_vyapar_payload(ComposerInput)` converts an ENTER verdict to a Vyapar draft-order JSON. `maybe_dispatch()` is gated on `cfg.mode == "live"` AND `AEGIS_EXECUTE_VYAPAR_WEBHOOK_URL` being non-empty. `_dispatch_stub` raises `NotImplementedError` intentionally — replace with a real `httpx.AsyncClient POST` when wiring live. `ExecuteSettings.vyapar_webhook_url` field added (env: `AEGIS_EXECUTE_VYAPAR_WEBHOOK_URL`, default empty = disabled).
- **B2B supply chain tables**: migration `0006_b2b_supply_chain.sql` adds `supply_chain_node`, `sku_lot`, `price_quote` (TimescaleDB hypertable on `observed_at`), `logistics_lane`, `arbitrage_opportunity` (hypertable on `detected_at`). All five tables use `app.current_tenant` RLS. Run `aegis datalake migrate` (or apply manually) before using the B2B pipeline.
- **Dependency version constraints (audit 2026-05-26)**: `orjson==3.11.9` (PYSEC-2026-107), `pyjwt[crypto]==2.13.0` (PYSEC-2026-120+PYSEC-2025-183), `python-dotenv==1.2.2` (CVE-2026-28684). Do not downgrade these — all three had production-affecting CVEs. `pydantic>=2.11,<3` — matches the baseline lock (2.11.10); Prefect compat is handled at the import site, not by pinning pydantic down.
- **Phase 5 confidence gate**: `aegis.scrape.confidence.score_batch()` runs on every batch in `scrape_topic()`. Score < `AEGIS_SCRAPE_CONFIDENCE_THRESHOLD` (default 0.85) logs `confidence.gate_failed` with `remediation_hints`. The pipeline always continues — confidence is advisory, never blocking.
- **Phase 5 velocity regression**: `aegis.scrape.analytics.compute_velocity_slope()` runs per-cluster in `detect_patterns()`. OLS slope > `HIGH_PRIORITY_SLOPE` (2.0 signals/hour) AND R² > 0.3 → `PatternCluster.is_high_priority=True`. High-priority clusters sort first in the output list. The threshold is configurable via `AEGIS_SCRAPE_VELOCITY_HIGH_PRIORITY_SLOPE`.
- **Phase 5 PCA denoising**: `pca_denoise_vectors()` is called on TF-IDF vectors in `detect_patterns()` before clustering. Falls back to identity (no-op) when numpy is absent or corpus < 3 signals. Retains 90% of variance by default.
- **Phase 5 `data_confidence` field**: the Phase 2 → Phase 4 stream payload now includes `data_confidence: float` (0–1). Populated from `TopicScrapeResult.batch_confidence` when `scrape_topic()` feeds `run_trend()`. Defaults to 1.0 for pipelines that bypass the scrape layer.
- **Phase 5 Hardening module path**: `aegis.harden` lives at `aegis-harden/src/aegis/harden/` — a uv workspace member, NOT a subpackage of `src/`. Import as `from aegis.harden.fingerprint import FingerprintPool`. Access exposed via `__path__` extension in `src/aegis/__init__.py` (same pattern as `aegis-phase4`).
- **Phase 5 Hardening `HardenShim`**: `src/aegis/scrape/harden_shim.py` is the single integration callsite for scrape adapters. Construct ONE `HardenShim` per scrape session (not per request) — internal RNG state advances across calls to keep fingerprints diverse. Call `shim.preflight(source, url, seq)` → `PreflightDecision` with `skip`, `fingerprint`, `proxy_index`, `delay_ms`, `playbook_name`.
- **Phase 5 Hardening session ContextVar**: `harden_shim` now exports `set_session_shim(shim)` and `get_session_shim()` backed by `contextvars.ContextVar`. `scrape_topic()` binds a time-seeded `HardenShim` at harvest start so all parallel `asyncio.create_task` adapter tasks inherit the same ContextVar copy (each child task gets a fork at task creation time). This ensures diverse, non-repeating fingerprints across the swarm instead of every task restarting from seed `0xA5615`.
- **Phase 5 Hardening graceful degradation**: All `aegis.harden` imports are wrapped in `try/except Exception` at module level. When `aegis-harden` is not installed or `AEGIS_ENABLE_HARDEN=false`, every `preflight()` call returns a shared `_PASS_THROUGH` sentinel (`skip=False`, `fingerprint=None`). Adapters that key on `decision.fingerprint is not None` handle both paths transparently.
- **Phase 5 Hardening `HARDEN_AVAILABLE`**: public constant exported from `harden_shim` and in `__all__`. Tests that require the full harden stack decorate with `@pytest.mark.skipif(not HARDEN_AVAILABLE, reason="aegis.harden not installed")`.
- **Phase 5 Hardening coverage**: `aegis-harden/*` is in `[tool.coverage.run].omit` — harden's own tests run separately with `uv run --package aegis-harden python -m pytest aegis-harden/tests/`. The main suite (1467 tests) excludes harden code from its coverage floor.
- **Phase 5 Hardening `__path__` extension**: `src/aegis/__init__.py` appends `aegis-harden/src/aegis` to `aegis.__path__` at import time. Pyright reports `reportMissingImports` for `aegis.harden.*` — this is expected; `[[tool.mypy.overrides]]` silences mypy for these modules.
- **Phase 10 module path**: `aegis.datalake` lives at `src/aegis/datalake/` — a subpackage of the main `aegis` namespace. **Not** a separate workspace member. Import as `from aegis.datalake import DataLake`.
- **Phase 10 storage layout**: `{bucket}/{layer}/{table}/dt=YYYY-MM-DD/tenant_id={uuid}/{batch_id}.parquet` + `_manifest.json`. Both S3/MinIO and local-filesystem backends honour this layout. Use `DataLakeSettings(use_local_filesystem=True)` for offline dev and tests (no MinIO needed).
- **Phase 10 idempotent writes**: every Parquet batch has a content-addressable `batch_id` (sha256 of canonical JSON). Re-running the same data produces the same `batch_id` — safe to replay without duplicates.
- **Phase 10 catalog**: SQLite file at `~/.aegis/datalake/catalog.sqlite3` (configurable). Tracks registered tables and partitions. Run `aegis datalake migrate` after first install to create the schema.
- **Phase 10 DuckDB**: embedded in-process engine. Memory limit 2 GB, 4 threads (configurable). Tables are registered as views pointing to Parquet globs in the storage layer. Query timeout 30 s by default.
- **Phase 10 NaN check idiom**: `v != v` (and `f != f`) in `silver/builder.py` is the intentional IEEE-754 NaN check — suppressed with `# noqa: PLR0124`. Do not replace with `math.isnan()` as that raises on non-float types.
- **Phase 10 Prefect**: flows degrade gracefully — `prefect` is an optional extra. When absent, the decorator stubs return identity functions so the flow code imports and runs without a Prefect server. Install with `uv sync --extra datalake-orchestration`. **Pydantic 2.11 compat note**: Prefect 3.x raises a `pydantic_core.ValidationError` (not `ImportError`) at import time on Pydantic ≥2.11 due to `LoggingToAPISettings.model_fields` instance access deprecation. `flows.py` uses `except Exception` (not `except ImportError`) at the Prefect import block to handle both cases — do not narrow back to `ImportError`.
- **Phase 10 `os.replace` in LocalStorageBackend**: kept intentionally (not replaced with `Path.replace()`) so the unit test can monkeypatch `os.replace` to simulate atomic-rename failure. Suppressed with `# noqa: PTH105`.
- **Phase 10 dependencies**: `pyarrow>=17`, `duckdb>=1.1,<1.2`, `boto3>=1.34` are in the `datalake` extra. `prefect>=3,<4` is in `datalake-orchestration`. All core ingest (asyncpg, redis) is already in root deps.
- **Phase 11 module path**: `aegis.llm` lives at `src/aegis/llm/` — a top-level subpackage of the main `aegis` namespace. **Not** a workspace member. Import as `from aegis.llm import LLMGateway`.
- **Phase 11 backward compat**: `src/aegis/agents/llm/` (`LLMRouter`) is fully preserved. The `__init__.py` there re-exports `get_gateway`/`complete_for_agent` from `aegis.llm.bridge.agents_bridge` when Phase 11 is installed; gracefully falls back to `None` when absent. Agent nodes need no import changes.
- **Phase 11 provider priority order**: `ollama(0) → vllm(1) → groq(2) → openrouter(3) → gemini(4) → anthropic(5) → openai(6)`. Lower number = tried first. `ProviderSelector` skips unhealthy providers based on TTL-cached health checks (60 s TTL).
- **Phase 11 circuit breaker**: each `BaseProvider` has a per-instance `_CircuitState`. After `CIRCUIT_BREAKER_FAILURE_THRESHOLD` (5) consecutive failures the circuit opens. Auto-recovers after `CIRCUIT_BREAKER_RECOVERY_S` (60 s). Raises `CircuitOpen` immediately when open.
- **Phase 11 SemanticRouter**: pure-Python cosine similarity (no external library). Falls back gracefully when no routes match (`threshold=0.75` default). Short-circuits the LLM call entirely on a match — zero latency, zero tokens.
- **Phase 11 guardrails**: `GuardrailsValidator` runs on every LLM output. Max length 8192 chars, PII regex (email/phone/SSN/Aadhaar/PAN), toxic-pattern blocklist. Raises `GuardrailBlock` (AEGIS-LLM-0003). `PIIScrubber` runs on *input* side and redacts before sending to any provider.
- **Phase 11 `GuardrailBlock` fail-fast**: `LLMGateway.complete()` catches `GuardrailBlock` **before** the generic `except Exception` handler and immediately re-raises — it does NOT rotate to the next provider. Blocked content must not be retried; a different provider would produce the same violation and incur extra cost + latency. This was fixed in the 2026-05-29 audit.
- **Phase 11 InstructorAdapter**: extracts typed Pydantic models from raw LLM text. Injects a JSON schema instruction into the system prompt; parses with `model_validate_json()`; on failure, sends the error back to the LLM for self-correction (1 retry). Per-node schemas are in `aegis.llm.instructor.schemas`.
- **Phase 11 LLM cache**: key = SHA256(provider + model + messages + temperature). Bypassed when `temperature > 0.5`. In-process LRU (default 512 entries) + optional Redis layer. Cache hit returns instantly without provider call.
- **Phase 11 PromptRegistry**: Jinja2 templates with YAML front-matter (`name`, `version`, `required_vars`, `description`). `autoescape=False` is intentional — these are LLM text prompts, not HTML. All renders are logged to `_audit_log` for reproducibility.
- **Phase 11 bridge pattern**: `agents_bridge` holds the process-singleton `_gateway`; `complete_for_agent(node_name, messages)` is the one call agent nodes should use — it goes through the full circuit-breaker + cache + guardrails stack. `phase3_bridge` and `phase4_bridge` provide deterministic fallback strings when the gateway is unavailable.
- **Phase 11 Ollama**: `http2=False` (same rule as reddit-rss — Ollama's HTTP server doesn't support HTTP/2). Health check hits `/api/tags`. Model pull is via `/api/pull` (streaming). Set `AEGIS_DISABLE_OLLAMA=1` to skip in tests.
- **Phase 11 LiteLLM**: optional proxy behind Docker Compose profile `llm-proxy`. Start with `docker compose --profile llm-proxy up -d litellm`. Provides a unified OpenAI-compat endpoint aggregating all providers. Config: `config/litellm_config.yaml`.
- **Phase 11 dependencies**: `sentence-transformers>=3` is in the `llm` extra (for SemanticRouter embeddings). `tiktoken>=0.7` is in `llm-tokenizer` extra (for accurate token counting). All provider HTTP clients use `httpx` which is already a root dep.
- **Phase 11 Prometheus metrics**: `aegis.llm.metrics` defines counters/histograms for requests, latency, token usage, guardrail blocks, cache hits. Falls back to `_NoOpMetric` stubs when `prometheus_client` is absent — import never fails.
- **Phase 12 module path**: `aegis.security` lives at `aegis-phase12/src/aegis/security/` — a uv workspace member (`aegis-security`). Exposed via `__path__` extension in `src/aegis/__init__.py` (same pattern as `aegis-harden`). Import as `from aegis.security import VaultClient, PIIScrubber, AuditLogger, RBACEnforcer`.
- **Phase 12 workspace member**: `aegis-phase12/` is listed under `[tool.uv.workspace].members` and sourced as `aegis-security = { workspace = true }`. Install with `uv sync --all-packages --all-extras`. Tests run with `python -m pytest aegis-phase12/tests/unit/ -q`.
- **Phase 12 `SecurityConfig`**: uses `env_prefix="AEGIS_SEC_"` and `extra="ignore"` (not `"forbid"`). The shared `.env` file has `AEGIS_*` vars from the main `Settings` class — `"forbid"` would reject all of them as unknown. Any security-specific env var is `AEGIS_SEC_<NAME>` (e.g. `AEGIS_SEC_VAULT_TOKEN`, `AEGIS_SEC_HMAC_KEY`).
- **Phase 12 VaultClient**: async context manager using httpx. Circuit breaker trips at 30% error rate over 60s window, half-opens after 120s. Exponential backoff: 0.5s base, 16s max, 3 retries. Token renewal background task fires 60s before TTL expiry. Dev mode uses `vault_token = "dev-root-token"` (override via `AEGIS_SEC_VAULT_TOKEN`).
- **Phase 12 PIIScrubber**: two-pass pipeline — Pass 1: regex patterns (email→SHA256-hash, phone/CC/SSN/Aadhaar/PAN/IPv4/IPv6/JWT/AWS keys→REDACT); Pass 2: spaCy NER (PERSON entities→REDACT). spaCy is optional (`aegis-security[spacy]` extra); degrades gracefully to regex-only when absent. Call `scrubber.scrub_text(text)` or `scrubber.scrub_dict(data)`.
- **Phase 12 AuditLogger**: `async with AuditLogger() as logger:` or `await logger.start()`. Each entry: `seq`, `ts`, `event`, `actor`, `resource`, `outcome`, `metadata`, `prev_hash` (SHA-256 of previous line), `hmac` (HMAC-SHA256 of the entry minus the hmac field). Chain integrity verifiable via `await logger.verify_integrity()`. Uploads batches to MinIO WORM bucket asynchronously (non-blocking).
- **Phase 12 RBACEnforcer**: roles = `viewer < analyst < operator < admin` (rank 0-3) plus `service` (rank 3). Inheritance is cumulative — `operator` has all `viewer` + `analyst` permissions too. FastAPI integration: `Depends(enforcer.require_permission("alerts:write"))`. JWT token extracted from `Authorization: Bearer <token>` header.
- **Phase 12 SecretsManager**: fallback chain — Vault KV v2 → SOPS-decrypted `.env.sops.yaml` → raw `os.environ` → `default` param. Encrypt/decrypt via Vault Transit; falls back to Fernet (AES-128-CBC, key derived from HMAC secret via SHA-256) when Vault unavailable.
- **Phase 12 SecretRotationManager**: `await mgr.rotate_secret(path)` generates new random hex secret, writes to Vault with CAS (check-and-set), starts a grace-period timer (default 300s) during which both old and new versions are valid. `_rotation_tasks` dict keeps asyncio.Task refs to prevent GC. `complete_rotation(path)` soft-deletes old version.
- **Phase 12 Pyright false positives**: `aegis.security.*` imports show `reportMissingImports` in Pyright/IDE — same as `aegis.harden.*`. Resolved at runtime via `__path__` extension; static analysis can't see it. Do not add type stubs; the imports are correct.
- **Phase 12 `_LUA_BUCKET_SCRIPT`**: the Redis Lua token-bucket script constant in `ratelimit.py` was renamed from `_LUA_TOKEN_BUCKET` to avoid ruff S105 false positive (variable name containing "TOKEN" flagged as potential hardcoded password).
- **Phase 13 module path**: `aegis.testing` lives at `aegis-phase13/src/aegis/testing/` — a uv workspace member (`aegis-phase13`). Exposed via `__path__` extension in `src/aegis/__init__.py` (same pattern as `aegis-harden`, `aegis-phase12`). Import as `from aegis.testing import assert_structlog_event, wait_for_redis_key, ...`.
- **Phase 13 workspace member**: `aegis-phase13/` is listed under `[tool.uv.workspace].members` and `aegis-phase13 = { workspace = true }` in `[tool.uv.sources]`. Unit tests added to `testpaths` in root `pyproject.toml`. Run with `uv run python -m pytest aegis-phase13/tests/unit/ -q -p no:hypothesis`.
- **Phase 13 test fixtures**: `conftest.py` in `aegis-phase13/tests/` provides `fake_redis` (_FakeRedis in-memory), `fake_pg_pool` / `fake_pg_conn` (asyncpg stubs), `fake_llm_gateway` (_FakeLLMGateway stub), `fake_minio` (_FakeMinIOClient), `fake` (Faker), `frozen_time`, and signal/trend/alert factory fixtures. Import via pytest fixture injection — no explicit import needed.
- **Phase 13 `_get_feature_names()`**: the conftest uses `_get_feature_names()` (a local helper in the conftest) to import the canonical `FEATURE_NAMES` tuple from `aegis.predict`. This ensures test FeatureWindow fixtures use the exact names the validator expects, falling back to sequential `feat_NN` names if `aegis.predict` is unavailable.
- **Phase 13 schema alignment notes**: `AgentDecision` requires `agent` (not `node_name`), `trend_id`, `correlation_id`, `details` (not `metadata`). `GraphResult` requires `correlation_id`, `started_at`, `finished_at`, `duration_ms`; `halt_reason` must be a literal from `('completed', 'vetoed_by_red_team', 'vetoed_by_hedge', 'blocked_by_compliance', 'scout_below_threshold', 'no_supplier', 'exception', 'timeout')`; `blocked_by` is `list[str]` not `str`. `FeatureWindow` requires `values` (not `features`), `window_size`, `feature_dim`, `correlation_id`, `captured_at` (not `computed_at`); `len(values) == window_size * feature_dim`.
- **Phase 13 alert fixture**: `AlertEnvelope` wraps `Alert` nested inside `{"alert": {...}, "signature_hex": None}`. `Alert.source` must be `AlertSource` enum: `"phase2_only"`, `"phase3_only"`, or `"phase2_and_phase3"`. BLOCK verdict requires `halt_reason` or `blocked_by` to be set.
- **Phase 13 heuristic API**: `heuristic_predict(window=fw, graph=None, horizons=(24,))` returns `list[Prediction]` (not a single Prediction). Each `Prediction` has `action` (`PredictionAction` enum: `enter/hold/exit/avoid/observe`) and `confidence` (float). The old `heuristic.predict(fw)` API does not exist.
- **Phase 13 LLMCache API**: `LLMCache(max_lru_size=N)` (not `max_size`). `get`/`set` are async coroutines taking `messages: list[dict[str, str]]` as the cache key (not string keys). `temperature > 0.5` bypasses cache on both `get` and `set`. `set` requires a real `LLMResponse` object with `usage.input_tokens`, `usage.output_tokens` etc.
- **Phase 13 Pyright false positives**: `aegis.testing.*` imports show `reportMissingImports` in Pyright/IDE — same as `aegis.harden.*` and `aegis.security.*`. Resolved at runtime via `__path__` extension. Do not add type stubs.
- **Phase 14 module path**: `aegis.observability` lives at `src/aegis/observability/` — a regular subpackage of the main `aegis` namespace (NOT a workspace member, NOT a `__path__` extension). Import directly: `from aegis.observability import init_tracing, get_tracer, init_metrics, init_sentry`.
- **Phase 14 graceful degradation**: all OTel and Sentry imports wrapped in `try/except`. When `opentelemetry-*` packages are absent, `init_tracing()` is a no-op, `get_tracer()` returns a `_NoOpTracer` with a `_NoOpSpan` context manager. When `sentry-sdk` is absent or `SENTRY_DSN` is empty, `init_sentry()` is a no-op. Services **never crash** due to missing observability packages.
- **Phase 14 metrics namespace**: all 24 metric definitions use an `aegis_obs_` prefix (e.g. `aegis_obs_ingest_signals_total`) to avoid Prometheus duplicate-collector errors with `aegis.core.metrics` (which uses `aegis_` prefix). The `_lookup_existing(name)` helper in `metrics.py` returns an already-registered collector when another module registered it first — do not call `Counter(name, ...)` if the name may already be registered.
- **Phase 14 `start_metrics_server`**: `start_metrics_server(port=8001)` is idempotent — subsequent calls are no-ops (guarded by `_metrics_server_started` flag). This prevents `OSError: address already in use` when the function is called multiple times during tests or service restarts.
- **Phase 14 Loki datasource**: auto-provisioned in Grafana via `config/grafana/provisioning/datasources/loki.yaml`. Loki is bound to `127.0.0.1:3100` (loopback only) by default — it is not publicly exposed. Query via Grafana Explore → select "Loki" datasource.
- **Phase 14 OTLP endpoint**: `OTEL_EXPORTER_OTLP_ENDPOINT` defaults to `http://localhost:4317` (Jaeger OTLP gRPC receiver). Set `OTEL_ENABLED=false` to disable tracing without uninstalling OTel packages.
- **Phase 15 module path**: `aegis.backup` lives at `src/aegis/backup/` — a direct subpackage of the main `aegis` namespace. NOT a workspace member. Import as `from aegis.backup import BackupManager, ResticBackup, BackupHealth`.
- **Phase 15 `BackupHealth` settings threading**: `BackupHealth.__init__` stores the `BackupSettings` instance as `self._settings`. Both `_check_pgbackrest()` and `_check_restic()` pass `settings=self._settings` to the manager constructors. Do NOT call `BackupManager()` or `ResticBackup()` with no args inside `BackupHealth` — it would bypass the injected settings and pull from the global env (which breaks unit tests and multi-env deployments).
- **Phase 15 `BackupSettings` env prefix**: `AEGIS_BACKUP_`. Key vars: `AEGIS_BACKUP_PGBACKREST_STANZA` (default `aegis-prod`), `AEGIS_BACKUP_PGBACKREST_REPO_PATH`, `AEGIS_BACKUP_RESTIC_PASSWORD` (required for restic), `AEGIS_BACKUP_RESTIC_REPOSITORY`. `restic_password=""` means restic is not configured — `BackupHealth._check_restic` treats this as a skip (healthy), not a failure.
- **Phase 15 pgBackRest regex**: `_extract_backup_id()` uses `[0-9A-Fa-f\-]+` (lowercase + uppercase hex). The pgBackRest log format is `backup stop archive = <hex>-<hex>`. Do NOT restrict to `[0-9A-F]` only — WAL segment IDs contain lowercase hex.
- **Phase 15 RPO/RTO targets**: pgBackRest incremental every 15 min → RPO ≤ 15 min, RTO ≤ 10 min. restic daily snapshots → RPO ≤ 24 h, RTO ≤ 20 min. WSL2 weekly export → RPO ≤ 7 days, RTO ≤ 30 min. See `docs/DR_RUNBOOK.md` for per-scenario step-by-step recovery procedures.
- **Phase 15 integration tests**: `tests/integration/test_disaster_recovery.py` — gated on `AEGIS_INTEGRATION_TEST=1`. Metadata integrity tests run always (no infra). pgBackRest tests require pgBackRest binary + running Postgres. restic tests additionally require `AEGIS_BACKUP_RESTIC_PASSWORD`. `TestBackupMetadataIntegrity` (4 tests) runs in every `pytest tests/unit/` invocation.
- **Phase 15 WSL backup script**: `bootstrap/wsl/backup_wsl_disk.sh` — run from WSL2 terminal. Exports distro with `wsl.exe --export`, gzip-compresses, optionally encrypts with `age`. Set `BACKUP_ENCRYPT=true` + `BACKUP_AGE_RECIPIENT=<pubkey>` for encryption. Prunes to `MAX_SNAPSHOTS` (default 5). Must be run as a user with access to `wsl.exe` on PATH.
- **Phase 15 drill database isolation**: `DrillResult` restores into `drill_pg_dsn` (default: `aegis_drill` database), NOT the production `aegis` database. Always verify `drill_pg_dsn != pg_dsn` before running a live drill. The `--dry-run` flag skips the actual restore and just validates manifest integrity.
- **Phase 15 `BackupSettings` vs `DisasterRecoverySettings`**: these are two separate settings classes with different env prefixes. `BackupSettings` (`AEGIS_BACKUP_*`) is for `src/aegis/backup/`. `DisasterRecoverySettings` (`AEGIS_DR_*`) is for `aegis-phase15/src/aegis/dr/`. They do not share keys.
- **Phase 6 module location**: all Phase 6 files live inside `aegis-phase4/src/aegis/execute/` — the existing workspace member. No new workspace member was created. `engine.py`, `pricing.py`, `approval.py`, `settlement.py` are all new modules in that package. Fulfillment clients live at `src/aegis/fulfillment/` (regular subpackage, not a workspace member, not a `__path__` extension).
- **Phase 6 `ExecutionEngine` only reads settings from `ExecuteSettings`**: never reads `CAPITAL_*` constants directly. All limits (`capital_max_risk_usd`, `capital_daily_loss_limit_usd`, `capital_kelly_fraction`) come from the injected `ExecuteSettings` instance. This makes the engine testable without touching the environment.
- **Phase 6 `ALLOWED_MODES` now includes `"staging"` and `"live"`**: the constant was previously `frozenset({"advisory"})`. Tests that construct `ExecuteSettings` with `mode="staging"` or `mode="live"` will now pass the validator. The default remains `"advisory"`.
- **Phase 6 `ApprovalBroker` has no `__slots__`**: removed to allow `monkeypatch.setattr(broker, "_send_telegram", ...)` in tests. The broker is a long-lived service object so `__slots__` provides no meaningful memory benefit.
- **Phase 6 fulfillment clients degrade gracefully**: `PrintfulClient`, `CJDropshipClient`, and `ShopifyClient` all return empty `list[str]` when their API key / credentials are absent — no exception, no crash. The engine records `status="failed"` for the plan in that case. Never raise from a fulfillment client.
- **Phase 6 `PricingStrategy` A/B test is seeded**: `PricingStrategy(sku, seed=N)` uses an isolated `random.Random(N)` so tests are deterministic. Never use the global `random` module inside `PricingStrategy` — that would make tests non-deterministic.
- **Phase 6 approval timeout uses `TimeoutError` not `asyncio.TimeoutError`**: ruff UP041 requires the builtin `TimeoutError` alias (Python 3.11+). The old `asyncio.TimeoutError` form was auto-fixed.
- **Phase 6 `/capital/status` accesses private `_settings`**: `engine._settings` is accessed directly in the status route to read `capital_daily_loss_limit_usd` and `mode`. This is acceptable — the route lives in the same package boundary. `noqa: SLF001` was removed because SLF001 is not enabled in the Phase 4 ruff config.
- **Phase 7 module path**: `aegis.geo` lives at `src/aegis/geo/` — a regular subpackage of the main `aegis` namespace. NOT a workspace member. Import as `from aegis.geo import CrossMarketAnalyzer, Region`.
- **Phase 7 FX source**: `FXRateFetcher` calls `https://api.frankfurter.app/latest?from=USD` (Frankfurter — ECB rates, no API key required). Cache TTL 3600 s in-process. Fallback: hardcoded ECB approximate midpoints (Dec 2024) when Frankfurter is unreachable.
- **Phase 7 tariff data source**: `HS_TARIFF_SCHEDULE` in `config.py` contains real WTO MFN applied rates for 20 HS codes × 8 regions, sourced from USITC/CBIC/EC TARIC/HMRC 2024. Unknown HS codes fall back to `_WTO_AVG_MFN_BY_SECTOR` (WTO World Tariff Profiles 2023). UN Comtrade API is stubbed as optional (`use_comtrade_fallback=True`) but disabled by default due to 5–30 s latency.
- **Phase 7 shipping baseline**: `SHIPPING_MATRIX_USD` uses EMS/postal published rates 2024 for a 0.5 kg tracked parcel (economy tier). Weight surcharge: +$1.50/kg above 0.5 kg. ShipEngine live quotes available via `AEGIS_GEO_SHIPENGINE_KEY` env var (disabled by default).
- **Phase 7 demand intensity from Phase 1 DB**: `RegionalDemandAnalyzer._db_demand()` queries `signals` table for signal velocity (24 h window) filtered by platform list for each region. Intensity = `min(1.0, signal_count/500 × avg_confidence)`. Falls back to `market_size_score × 0.6` (synthetic) when pool is None or DB fails.
- **Phase 7 price data priority**: (1) `median_price_usd` from Phase 1 signals DB (scraped `price_amount` converted via FX). (2) `CATEGORY_MEDIAN_PRICES_USD` from `config.py` — real category median prices from marketplace research 2024 (not mock). No random/fake prices anywhere.
- **Phase 7 opportunity scoring**: `opportunity_score = gross_margin_pct × demand_intensity × market_size_score`. Only pairs with `gross_margin_pct ≥ 5%` are included. Results sorted descending by score; top N returned (default 10).
- **Phase 7 `CrossMarketAnalyzer` is stateless per-call**: hold one instance per process for FX cache reuse (`FXRateFetcher._cache` is per-instance with TTL). All demand/FX fetches for a single `find_opportunities()` call run in parallel via `asyncio.gather`.
- **Phase 7 Phase 6 bridge**: `geo_opportunity_to_execution_intent()` is guarded by `try/except ImportError` — Phase 7 works standalone without Phase 6. Priority: `margin ≥ 40%` → P1 ENTER, `≥ 20%` → P2 ENTER, `≥ 5%` → P3 HOLD. All geo metadata (HS code, shipping carrier, transit days, FX rate) stored in `ExecutionIntent.metadata`.
- **Phase 7 `AEGIS_GEO_SHIPENGINE_KEY`**: when set, `ShippingResolver.get_quote()` attempts ShipEngine API before falling back to the static matrix. ShipEngine's carrier IDs are placeholders — replace with real IDs from a ShipEngine account before enabling in production.
- **Phase 8 module path**: `aegis.compliance` lives at `src/aegis/compliance/` — a regular subpackage of the main `aegis` namespace. NOT a workspace member. Import as `from aegis.compliance import ComplianceEngine, ComplianceRiskAssessment`.
- **Phase 8 hard BLOCK overrides**: three conditions always force `Recommendation.BLOCK` regardless of composite score: (1) any `SanctionMatch.risk_score >= 0.99` (OFAC-sanctioned country); (2) `fda_risk >= 0.90` (FDA banned keyword); (3) any `CounterfeitSignal.confidence >= 0.90` with `signal_type == "replica_keyword"`. The composite risk weight for AML is only 10%, so a lone OFAC hit produces overall score ~0.099 — hard override is essential to guarantee blocking.
- **Phase 8 FTC hard escalate**: if `ftc_risk >= 0.80` and no hard BLOCK, the engine forces `Recommendation.ESCALATE` regardless of composite score. Rationale: FTC weight is 10% max — a solo 100% FTC score only contributes 0.10 to the composite, not enough to reach the 0.50 escalate threshold. Without this override, products with multiple severe FTC violations (e.g. false FDA claims + guaranteed cancer cure) would be auto-proceeded.
- **Phase 8 risk weights**: `{trademark: 0.22, fda: 0.20, counterfeit: 0.15, patent: 0.13, privacy: 0.10, ftc: 0.10, aml: 0.10}`. Sum = 1.0. Trademark + FDA dominate because these carry the highest legal exposure per violation.
- **Phase 8 fuzzy trademark matching**: `IPRChecker._fast_trademark_check()` compares each individual title word against brand names using `difflib.SequenceMatcher(None, brand_name, title_word).ratio()`. Threshold: 0.82. This catches typosquats like "Adiddas" → "adidas" (ratio 0.92) but not generic words. The entire-title ratio was NOT used (would always be too low for multi-word titles).
- **Phase 8 privacy word-boundary matching**: `PrivacyRiskAssessor._product_collects_data()` uses `re.split(r"\W+", combined)` to build a word set, then checks exact word membership — NOT substring. This prevents "app" matching "apparel", "smart" matching "supermarket", etc. `DATA_COLLECTING_CATEGORIES` is checked via `re.search(r"\b...\b", combined)` for the same reason.
- **Phase 8 OFAC/FATF data is static**: `OFAC_SANCTIONED_COUNTRIES` and `FATF_HIGH_RISK` in `constants.py` are hardcoded and updated quarterly. For real-time entity-level screening, set `AEGIS_COMPLY_TRADE_GOV_API_KEY` (free registration at api.trade.gov) — the `AMLChecker` will then call the Trade.gov Consolidated Screening List API for entity name lookup.
- **Phase 8 `ComplianceSettings` env prefix**: `AEGIS_COMPLY_`. Key vars: `AEGIS_COMPLY_BLOCK_THRESHOLD` (default 0.70), `AEGIS_COMPLY_ESCALATE_THRESHOLD` (default 0.50), `AEGIS_COMPLY_FDA_API_KEY` (optional, raises OpenFDA rate limit), `AEGIS_COMPLY_TRADE_GOV_API_KEY` (optional, enables CSL entity screening), `AEGIS_COMPLY_CLIP_ENABLED` (default false, enables CLIP image-similarity counterfeit detection).
- **Phase 8 result cache key**: SHA-256 of `"{sku}|{title.lower().strip()}|{origin}|{destination}"`. Cache TTL default 6 h (configurable via `AEGIS_COMPLY_RESULT_CACHE_TTL_HOURS`). Cached results are returned with `cached=True` via `model_copy(update={"cached": True})` — the stored result always has `cached=False`.
- **Phase 8 `gate_execution_plan()` fails open**: when `aegis.compliance` is not installed, `gate_execution_plan()` returns `(True, "Compliance gate bypassed")` with a WARNING log. Never raises. This allows Phase 6 to operate in environments without Phase 8 installed.
- **Phase 8 Phase 12 audit integration**: `ComplianceEngine._emit_audit()` fires-and-forgets an `AuditLogger` write via `loop.create_task()`. Task reference stored as `_task` to prevent GC before completion. Wrapped in `try/except (ImportError, ModuleNotFoundError)` — Phase 12 absence is fully graceful.
- **Phase 8 EUIPO API skip condition**: `IPRChecker.check_trademark()` skips the live EUIPO API call when any local brand match has `confidence_score > 0.9`. This avoids 5 s network latency when an exact luxury brand match is already found locally. The EUIPO call still fires for borderline keyword matches (confidence ≤ 0.9).
- **Phase 8 counterfeit price anomaly threshold**: products priced below 15% of category median USD are flagged (`_PRICE_ANOMALY_THRESHOLD = 0.15`). Category medians sourced from Phase 7 `CATEGORY_MEDIAN_PRICES_USD` (marketplace research 2024). No random/mock prices.
- **Phase 8 compliance tests run in main suite**: `tests/unit/compliance/` is in the main `testpaths` (not a separate workspace member). All 80 tests mock every external API call (httpx) — no network access required. `cli.py` and `api.py` are in `coverage.run.omit` (same pattern as `*/geo/cli.py`).

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
- **`PREFECT_AVAILABLE` in flows.py**: same Pyright false positive as `_JINJA_AVAILABLE` above. Additionally, the `except` clause uses bare `Exception` (not `ImportError`) — this is intentional, not a linting error; Prefect 3.x raises `pydantic_core.ValidationError` (not `ImportError`) on Pydantic 2.11 at import time.
- **ruff `--select RUF100 --fix` pitfall**: running ruff with only `--select RUF100` deselects all other rules, so every `# noqa: S324` annotation appears "unused" (S324 never fires when it's not selected). Running `--fix` then silently removes all these annotations. Always run `ruff check` (full config) to verify zero violations — never trust `--select RUF100` alone to validate noqa correctness.
- **`aegis.harden` Pyright false positives**: `harden_shim.py` and any code that imports `aegis.harden.*` will show `reportMissingImports` in Pyright/IDE. This is expected — `aegis.harden` resolves at runtime via `__path__` extension, not via static analysis. Mypy is silenced via `[[tool.mypy.overrides]]`. Do not add type stubs; the imports are correct.
- **`HardenShim` `# noqa: BLE001` is NOT needed**: `BLE001` (blind exception catch) is globally ignored in the main ruff config. Adding `# noqa: BLE001` to `except Exception:` blocks in `harden_shim.py` will cause a `RUF100` violation (unused noqa directive). Just use bare `except Exception:`.
- **aegis-harden tests run separately**: `aegis-harden/tests/` is NOT in the main `testpaths`. Run harden tests with `uv run --package aegis-harden python -m pytest aegis-harden/tests/ -q`. Adding them to the main suite causes hypothesis profile conflicts and pollutes coverage numbers.
- **`docker compose exec` / `docker compose logs` require service names, not container names**: the service names (`postgres`, `redis`, `dashboard`, `predict`, `execute-api`, …) are defined in `docker-compose.yml`. The container names (`aegis-postgres`, `aegis-redis`, …) are what Docker assigns at runtime. `exec` and `logs` only accept the service name form — using a container name produces `"service X is not running"` even when the container is healthy.
- **`AEGIS_ENV` must be `dev`, `staging`, `prod`, or `test` — never `development`**: `Settings.env` is a `Literal` type; any other value raises a `pydantic_core.ValidationError` at handler call time even if the app started. Containers started before `.env` was populated (or with a stale environment) may hold `development` — recreate them with `docker compose up -d --no-build <service>` to pick up the correct value.
- **Dashboard `sentiment` / `commercial_intent` / `novelty` are NOT columns on `signals`**: those names come from the `TrendCandidate` Pydantic model (computed agent output). The dashboard SQL maps real schema columns: `source_confidence → sentiment`, `completeness → novelty`, and a CASE expression on `intent`/`price_amount → commercial_intent`. Do not add these names to the `signals` table or query them directly.
- **`Dockerfile.dashboard` must COPY workspace member directories**: `uv pip install "/app[predict]"` reads `pyproject.toml` and tries to resolve all workspace members listed under `[tool.uv.workspace].members`. If `aegis-harden/` or `aegis-phase4/` are missing from the build context the build fails with `"Failed to parse entry: aegis-harden"`. The Dockerfile `COPY`s both directories explicitly — do not remove those lines.
- **`ollama-init` is a one-shot container**: it pulls `bge-m3` and `llama3.2:3b` on first start and then exits with code 0 (correct). If models are missing (e.g. the volume was wiped or the init failed), re-run it with `docker compose run --rm ollama-init`. The prior `>` YAML block with multi-line curl args was broken (YAML folded scalars preserve indented lines as literal newlines, causing `-H` and `-d` to be passed as separate shell arguments). The entrypoint now uses list form with a `|` literal block.
- **Graph test names updated (2026-05-29)**: `test_weak_scout_skips_to_compliance` and `test_no_supplier_skips_to_compliance` were renamed to `test_weak_scout_routes_to_sentinel` / `test_no_supplier_routes_to_sentinel` after the sentinel-routing fix. If a future test expects `"compliance"` as the direct return of `_post_historian_route` or `_post_sourcer_route` on a weak/no-supplier path it is testing the old (broken) behaviour.
- **`asyncio.to_thread` in `scrape_topic`**: `score_batch`, `deduplicate_batch` (non-pool path), and `detect_patterns` are sync CPU/parse functions. They **must** be called with `await asyncio.to_thread(fn, *args, **kwargs)`. Adding a new post-gather CPU step inside `scrape_topic` must follow the same pattern — do not call sync functions directly in the async body.
- **Playwright browser lifetime**: `playwright_fetcher._browser` and `._pw` are module-level globals. They are created lazily by `_acquire_browser()` and never explicitly stopped (process lifetime). When writing tests that mock Playwright, mock `_async_playwright` or `_acquire_browser` — do not mock `browser.close()` (it is not called anymore).
- **Enterprise overlay removes docker.sock**: `docker-compose.enterprise.yml` sets `volumes: []` on the dashboard service, removing the `docker.sock` mount. The CLI `aegis status` Docker-socket fallback (`_docker_ps_via_socket`) still works from inside the container only when `group_add: [DOCKER_GID]` is active.
- **Phase 14 `_lookup_existing` is required for test isolation**: if `test_metrics.py` imports `aegis.observability.metrics` in multiple test runs (or combined with `aegis.core.metrics`), Prometheus will throw `ValueError: Duplicated timeseries` on the second registration. `_lookup_existing(name)` checks `REGISTRY._names_to_collectors` first. When writing new metric definitions, always go through the `_mk_counter/gauge/histogram` factory helpers — do not call `Counter(name, ...)` directly.
- **Phase 14 Loki service name is `loki` (not `aegis-loki`)**: in `docker-compose.yml`, the service is named `loki` (without the `aegis-` prefix, unlike most other services). Use `docker compose logs loki` not `docker compose logs aegis-loki`. Container name is `aegis-loki`.
- **Phase 14 Promtail needs docker.sock**: the Promtail service mounts `/var/run/docker.sock` read-only to discover container log paths. On WSL2, this requires Docker Desktop with socket forwarding enabled. If Promtail shows `permission denied` on the socket, check `DOCKER_GID` matches the host's `docker` group ID.
- **Phase 14 OTel and Phase 11 LLM metrics coexist**: `aegis.llm.metrics` defines `aegis_llm_*` metrics; `aegis.observability.metrics` defines `aegis_obs_*` metrics. Both use the shared `prometheus_client.REGISTRY`. The `_obs_` namespace was added specifically to avoid the `aegis_llm_` / `aegis_` collision. Do not remove the `_obs_` prefix.
- **Phase 15 `src/aegis/backup/` is in the main package**: unlike `aegis-phase12`, `aegis-phase15` DR module, or `aegis-harden`, `src/aegis/backup/` does NOT need a `__path__` extension — it is a normal subpackage of `src/aegis/`. Import directly as `from aegis.backup import BackupManager`.
- **Phase 15 pgBackRest requires the binary**: `BackupManager` shells out to `pgbackrest` via subprocess. In dev environments without pgBackRest installed, all backup calls return `BackupCommandError`. Use `AEGIS_BACKUP_PGBACKREST_STANZA=skip` (or patch `subprocess.run`) in unit tests — the test suite mocks subprocess, so no binary is needed to run tests.
- **Phase 15 restic password must be set before `init`**: `ResticBackup.init_repo()` will create a new restic repository at `AEGIS_BACKUP_RESTIC_REPOSITORY`. If the password (`AEGIS_BACKUP_RESTIC_PASSWORD`) is empty, restic refuses to initialize. For dev: set any non-empty value. For prod: use `AEGIS_SEC_*` vault integration to inject the password.
- **Phase 15 drill database must pre-exist**: `DrOrchestrator`/`RestoreDrill` connects to `AEGIS_DR_DRILL_PG_DSN` for restore validation. The `aegis_drill` database must be created manually before the first drill: `psql -h localhost -p 5433 -U postgres -c "CREATE DATABASE aegis_drill;"`. The drill does NOT create the database automatically.

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
