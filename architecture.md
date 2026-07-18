# AEGIS Pulse — System Architecture

## Overview

AEGIS Pulse is an autonomous market arbitrage intelligence engine. It scrapes signals from 35+ platforms, stores them in TimescaleDB, runs a 10-node LangGraph multi-agent pipeline to score and prioritise arbitrage opportunities, executes capital-efficient plans through a three-tier approval workflow, persists everything in a Bronze/Silver/Gold data lake, continuously self-improves via weekly model retraining + RL-based online learning + KS-distance drift detection, and observes itself via a full OTel + Prometheus + Loki stack with automated disaster recovery.

---

## High-Level Data Flow

```
[Signal Sources: 35+ adapters]
         │
         ▼
[Phase 0 — Scrape Layer]            ←── [aegis-harden — Bot Resilience]
   topic.py / swarm.py                    HardenShim: JA3/JA4 fingerprints
   dedup, normalizer, confidence gate     per-source playbooks, honeypot screen
         │
         ▼
[Phase 1 — Persistence Layer]
   TimescaleDB (signals, authors, B2B supply chain)
   Redis (cache + inter-agent streams, capped at 10k entries)
   MinIO (raw / features / models / backups / WORM audit)
         │
         ▼
[Phase 11 — LLM Orchestration]
   LLMGateway: circuit breaker + fallback chain
   Ollama → vLLM → Groq → OpenRouter → Gemini → Anthropic → OpenAI
   SemanticRouter, GuardrailsValidator, InstructorAdapter
         │
         ▼
[Phase 2 — Multi-Agent Intelligence]
   10-node LangGraph DAG
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
                        ├──────────────────────┐
                        ▼                      ▼
              [Phase 10 — Data Lake]   [Dashboard :8300]
                Bronze → Silver → Gold   SSE live feed
                DuckDB query engine      ops console
                Prefect 3 orchestration  ChartJS, health
                        │
                        ├──────────────────────────────────┐
                        ▼                                  ▼
              [Phase 6 — Capital Execution]
                ExecutionEngine (3-tier: advisory/staging/live)
                fractional-Kelly 0.25× position sizing
                Telegram approval (P0/P1 plans)
                PricingStrategy (A/B + RL online learning)
                SettlementManager (EOD PnL reconciliation)
                Fulfillment: Printful POD / CJ Dropship / Shopify
                /capital/* REST API
                        │
                        ├──────────────────────────────────┐
                        ▼                                  ▼
              [Phase 7 — Geospatial Intel]     [Phase 8 — Compliance Engine]
                CrossMarketAnalyzer              7-dimension risk matrix
                WTO MFN tariffs (20 HS codes)    USPTO + EUIPO trademark APIs
                ECB/Frankfurter FX rates         OpenFDA enforcement API
                EMS/postal shipping matrix       OFAC/FATF sanctions
                RegionalDemandAnalyzer           FTC rule engine (30+ patterns)
                /geo/* REST API                  /compliance/* REST API
                        │
                        ▼
              [Phase 9 — Autonomous Self-Evolution]
                OutcomeRecorder (every settled trade → ground truth)
                RetrainingPipeline (weekly Sun 2AM UTC + Optuna TPE HPO)
                DriftDetector (KS-distance + precision monitoring)
                OnlinePricingPolicy (4-weight REINFORCE; daily updates)
                Champion promotion gate (+2% AUC; auto-rollback on drift)
                /evolve/* REST API
                        │  ◄──── feedback loop back to Phase 3 champion model
                        │
                        ├──────────────────────────────────┐
                        ▼                                  ▼
              [Phase 14 — Observability]        [Phase 15 — Disaster Recovery]
                OTel tracing → Jaeger             pgBackRest + restic backups
                24 Prometheus metrics             RPO 15 min / RTO 60 min
                Sentry error tracking             Weekly restore drill
                Loki log aggregation              DrOrchestrator + DrHealthChecker
                Grafana Mission Control           Failure-mode runbooks
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
| `harden_shim.py` | `HardenShim` — single integration callsite for aegis-harden pre-flight |

### Phase 1 — Persistence (`src/aegis/db/`, `src/aegis/cache/`)

| Component | Purpose |
|-----------|---------|
| `db/pool.py` | `get_shared_pool()` / `set_shared_pool()` — single asyncpg pool at startup |
| `db/signals.py` | `fetch_recent_signals()`, insert, dedup sweeper |
| `cache/redis.py` | `RedisCache` — TTL-backed key/value + pub/sub |
| MinIO | Object store: raw signals, feature windows, model artifacts, backups |

**TimescaleDB tables**: `signals`, `authors`, `predictions`, `prediction_audit`, `model_manifest`, `backtest_results`, `alerts`, `alert_outbox`, `alert_deliveries`, `execution_intents`, `killswitch_audit`  
**B2B supply chain tables** (migration `0006_b2b_supply_chain.sql`): `supply_chain_node`, `sku_lot`, `price_quote` (hypertable), `logistics_lane`, `arbitrage_opportunity` (hypertable) — all with RLS + `app.current_tenant` policy

All tables use Row-Level Security. Always `SET app.current_tenant = '<uuid>'` before queries.

### Phase 2 — Multi-Agent Intelligence (`src/aegis/agents/`)

```
GraphState (TypedDict, annotated reducers)
         │
         ▼
10-node LangGraph DAG  (parallel START, then sequential with gates)

  START ──┬──► scout ─────────┐
          ├──► geo_arbitrage ──┼──► historian ──► sourcer ──► auditor ──┐
          └──► narrative ──────┘         │                              │
                                         │ (scout_score < 0.55          │
                                         │  or BLOCK verdict)           │
                                         └──────────────────────────────┼─► sentinel ──► compliance
                                                                         │               │     │
                                    (no supplier from sourcer) ──────────┘    ┌──────────┘     └──► finalize
                                                                               ▼
                                                                          red_team ──► hedge ──► finalize
```

**Routing rules (2026-05-29 audit fix)**:
- Strong scout (`score ≥ 0.55`, not BLOCK): `historian → sourcer → (auditor →) sentinel → compliance`
- Weak scout / BLOCK: `historian → sentinel → compliance` (sourcer+auditor skipped, sentinel **always** runs)
- No supplier from sourcer: `sourcer → sentinel → compliance` (auditor skipped, sentinel still runs)
- Sentinel exit signals are **never silently dropped** regardless of scout strength

**LLM routing**: `aegis.agents.llm` shim re-exports `get_gateway`/`complete_for_agent` from `aegis.llm.bridge.agents_bridge` (Phase 11). Falls back gracefully when Phase 11 is not installed.  
**Key models**: `TrendCandidate`, `AgentDecision`, `GraphResult` — all Pydantic v2 frozen  
**Stream**: publishes enriched JSON to `aegis:phase2:graph_results` Redis stream under `"body"` key; **capped at 10,000 entries** (`maxlen=10_000, approximate=True`) to prevent Redis OOM

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

**Phase 3 ↔ Phase 2 bridge** (`src/aegis/agents_phase3_glue/bridge.py`): maps `InferenceResult → AgentDecision` without importing LangGraph. SCOUT uses `p_breakout` at 24h; SENTINEL uses `p_decline` at 6h. `phase3_bundle` and `phase3_audit` are **stripped from the state-carried `phase3_decision` dict** (full data lives in `phase3_result`) to prevent LangGraph state bloat on large signal batches.

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
**Vyapar bridge** (`aegis-phase4/…/bridge/vyapar.py`): stub module for B2B execution; `build_vyapar_payload(ComposerInput)` + `maybe_dispatch()` gated on `mode=live` and `AEGIS_EXECUTE_VYAPAR_WEBHOOK_URL`.

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

### Phase 5 — Adversarial Hardening (`aegis-harden/` — uv workspace member)

```
HardenShim.preflight(source, url, seq) → PreflightDecision
   │
   ├─ PlaybookRegistry.match()   → per-source delay/jitter/proxy profile (YAML)
   ├─ FingerprintPool.pick(rng)  → JA3/JA4/H2 browser fingerprint (8 built-in profiles)
   ├─ ProxyPosture.decide()      → proxy pool index (pure policy, no I/O)
   └─ screen_url(url)            → HardenVerdict (pass / block)
```

**Graceful degradation**: all `aegis.harden` imports wrapped in `try/except`; when not installed or `AEGIS_ENABLE_HARDEN=false`, `preflight()` returns `_PASS_THROUGH` (`skip=False`, `fingerprint=None`).  
**Session ContextVar**: `harden_shim.set_session_shim(shim)` / `get_session_shim()` — `scrape_topic()` binds one time-seeded `HardenShim` at the start of each harvest via ContextVar so all parallel adapter tasks share a continuously-advancing RNG sequence.

### Phase 6 — Capital Execution Engine (`aegis-phase4/src/aegis/execute/` + `src/aegis/fulfillment/`)

```
ExecutionEngine (single entry point — execute_plan(trend_id, score, confidence, sku, unit_cost))
   │
   ├─ engine.py        → ExecutionEngine: Kelly-sized 3-tier dispatch
   │                     advisory (default) / staging / live modes
   │                     fractional-Kelly 0.25× cap + daily drawdown circuit breaker
   │                     capital_max_risk_usd / capital_daily_loss_limit_usd from ExecuteSettings
   │
   ├─ pricing.py       → PricingStrategy: multi-objective weighted scoring
   │                     A/B test buckets (seeded — deterministic), online RL weight updates
   │                     markup target + floor price enforcement
   │
   ├─ approval.py      → ApprovalBroker: Telegram inline-button workflow
   │                     P0 (score ≥ 0.85) / P1 (score ≥ 0.70) require human sign-off
   │                     timeout → auto-escalate or auto-reject; asyncio.Task-based
   │
   ├─ settlement.py    → SettlementManager: DailySettlement + PnL reconciliation
   │                     EOD aggregation → DailySettlement; tax CSV export
   │                     realised / unrealised PnL tracking per tenant
   │
   └─ fulfillment/     → three client modules (src/aegis/fulfillment/)
        printful.py    → PrintfulClient: POD order creation + status polling
        cjdropshipping.py → CJDropshipClient: dropship order + tracking
        shopify.py     → ShopifyClient: draft order → checkout → fulfilment
```

**Three-tier model**: advisory (plans logged, zero capital) → staging (paper trades, mock fulfillment) → live (real orders). Default is `advisory` — zero capital at risk in CI.  
**Kelly sizing**: `quantity = floor(kelly_fraction × kelly_score × capital_max_risk_usd / unit_cost)`, capped at `max_pct_of_capital=10%`.  
**Drawdown circuit breaker**: daily realised loss ≥ `capital_daily_loss_limit_usd` → all new plans rejected until next UTC day.  
**Fulfillment degradation**: each client returns `[]` when credentials absent — never raises. Engine records `status="failed"` for the plan.  
**REST API**: `/capital/plan`, `/capital/status`, `/capital/settle`, `/capital/approve/{plan_id}` — mounted on Phase 4 FastAPI app `:8200`.  
**DB migration**: `db/migrations/0007_capital_execution.sql` — `execution_plans` (TimescaleDB hypertable on `created_at`), `pricing_events`, `settlements`, `approval_requests`; all with RLS.  
**ADR**: `docs/adr/0006-capital-execution.md` — Three-Tier Execution Model.

### Phase 7 — Geospatial Intelligence & Cross-Market Arbitrage (`src/aegis/geo/`)

```
CrossMarketAnalyzer (stateless per-call; hold one instance for FX cache reuse)
   │
   ├─ fx.py          → FXRateFetcher: Frankfurter/ECB live rates; 3600s in-process TTL cache
   │                    fallback: hardcoded ECB approximate midpoints (Dec 2024)
   │
   ├─ tariffs.py     → TariffEstimator: WTO MFN applied rates for 20 HS codes × 8 regions
   │                    (USITC/CBIC/EC TARIC/HMRC 2024); unknown HS → WTO average by sector
   │
   ├─ shipping.py    → ShippingResolver: 8×8 EMS/postal matrix (2024, 0.5 kg economy tier)
   │                    +$1.50/kg weight surcharge; optional ShipEngine live quotes
   │
   ├─ demand.py      → RegionalDemandAnalyzer: Phase 1 signals DB velocity (24h window)
   │                    intensity = min(1.0, count/500 × avg_confidence); synthetic fallback
   │
   ├─ arbitrage.py   → enumerate all O×D pairs; score = gross_margin_pct × demand × market_size
   │                    minimum 5% gross margin; results sorted descending; top-N returned
   │
   └─ phase6_bridge  → geo_opportunity_to_execution_intent(): GeoOpportunity → ExecutionIntent
                        P1 ENTER (≥40% margin) / P2 ENTER (≥20%) / P3 HOLD (≥5%); guarded by ImportError
```

**REST API**: `/geo/health`, `/geo/analyze`, `/geo/fx`, `/geo/tariff`, `/geo/shipping`  
**CLI**: `aegis geo analyze|fx|tariff|shipping|regions`  
**DB migration**: `db/migrations/0008_geo_intelligence.sql`  
**ADR**: `docs/adr/0007-geospatial-intelligence.md`

### Phase 8 — Regulatory & Compliance Engine (`src/aegis/compliance/`)

```
ComplianceEngine (parallel asyncio.gather across all checkers)
   │
   ├─ ipr.py         → IPRChecker: USPTO PatentsView (patents) + EUIPO TMview (EU trademarks)
   │                    + 40-brand local DB fuzzy match (difflib threshold 0.82)
   │                    EUIPO call skipped when local confidence > 0.9 (latency optimisation)
   │
   ├─ fda.py         → FDAChecker: OpenFDA /drug /food /device enforcement endpoints (no key)
   │                    banned-keyword fast path; optional AEGIS_COMPLY_FDA_API_KEY for rate limit uplift
   │
   ├─ ftc.py         → FTCRuleEngine: 30+ compiled regex patterns
   │                    FTC Act §5 + 16 CFR 255 (endorsements) + 16 CFR 362 (energy claims)
   │                    zero I/O — instant pattern match only
   │
   ├─ privacy.py     → PrivacyRiskAssessor: GDPR/DPDP/DSA/GPSR/CCPA/PIPEDA/COPPA
   │                    word-boundary matching (not substring) to prevent false positives
   │
   ├─ aml.py         → AMLChecker: OFAC 17-country list + FATF 2024 grey/black list
   │                    + optional Trade.gov CSL entity screening (AEGIS_COMPLY_TRADE_GOV_API_KEY)
   │
   ├─ counterfeit.py → CounterfeitDetector: exact brand match + fuzzy (difflib)
   │                    + price anomaly (< 15% of category median) + replica keywords
   │                    + optional CLIP image-similarity (AEGIS_COMPLY_CLIP_ENABLED)
   │
   └─ cache.py       → ComplianceCache: in-process TTL dict; key = SHA-256(sku|title|origin|dest)
                        default TTL 6 h (AEGIS_COMPLY_RESULT_CACHE_TTL_HOURS)
```

**Risk weights**: trademark 22% + FDA 20% + counterfeit 15% + patent 13% + privacy 10% + FTC 10% + AML 10% = 100%  
**Hard BLOCK overrides**: (1) OFAC `risk_score ≥ 0.99`; (2) `fda_risk ≥ 0.90`; (3) `CounterfeitSignal.confidence ≥ 0.90` with `signal_type == "replica_keyword"`  
**FTC hard escalate**: `ftc_risk ≥ 0.80` forces `ESCALATE` regardless of composite score (FTC weight alone can't reach the 0.50 threshold)  
**Phase 6 gate**: `gate_execution_plan()` integrated into `ExecutionEngine`; fails open with WARNING when `aegis.compliance` absent  
**Phase 12 audit**: `_emit_audit()` fires-and-forgets an `AuditLogger` write; graceful when Phase 12 absent  
**REST API**: `/compliance/health`, `/compliance/assess`, `/compliance/assess/batch`, `/compliance/trademark/{query}`, `/compliance/sanctions/{country}`, `/compliance/ftc`  
**CLI**: `aegis compliance assess|trademark|sanctions|ftc|batch`  
**DB migration**: `db/migrations/0009_compliance.sql`  
**ADR**: `docs/adr/0008-compliance-engine.md`

### Phase 9 — Autonomous Self-Evolution (`src/aegis/evolve/`)

```
RetrainingPipeline (weekly Sunday 2AM UTC + on-demand)
   │
   ├─ outcomes.py     → OutcomeRecorder: record_outcome(), fetch_recent_outcomes()
   │                    TimescaleDB hypertable `prediction_outcomes` (7-day chunks, RLS)
   │                    idempotent via ON CONFLICT DO NOTHING on (plan_id, trend_id)
   │
   ├─ hpo.py          → optimize_hyperparameters(): Optuna TPE, 30 trials, logistic-regression proxy
   │                    graceful fallback to get_default_hyperparameters() when optuna absent
   │                    optuna is optional extra — install with `uv sync --extra evolve`
   │
   ├─ retrain.py      → RetrainingPipeline.run_weekly_retrain():
   │                    fetch ≥ MIN_OUTCOMES_FOR_RETRAIN outcomes → HPO → train candidate
   │                    champion promotion gate: new AUC must exceed champion by ≥ 2%
   │                    _upsert_retrain_audit() is best-effort (DEBUG on failure, never raises)
   │
   ├─ drift.py        → DriftDetector: KS-distance on feature distributions
   │                    + precision monitoring against outcome labels
   │                    auto-rollback trigger when drift_score ≥ AEGIS_EVOLVE_DRIFT_THRESHOLD (0.15)
   │                    baseline_mean=zeros(20), baseline_std=ones(20) — replace with
   │                    champion training-time statistics for production use
   │
   └─ rl_policy.py    → OnlinePricingPolicy: 4-weight REINFORCE agent
                        weights ∈ [1e-6, ∞) (non-negative by design; prevents zero-weight collapse)
                        update_from_outcome() applies daily pricing adjustments
                        persist() / load() for checkpoint continuity across restarts
```

**Feedback loop**: `OutcomeRecorder` stores every settled Phase 6 trade; `DriftDetector` compares live prediction features against the champion baseline; `RetrainingPipeline` promotes a new champion to Phase 3 `ModelStore` when AUC improves; `OnlinePricingPolicy` adjusts Phase 6 `PricingStrategy` weights daily from outcome signals.  
**Settings env prefix**: `AEGIS_EVOLVE_*` — key vars: `AEGIS_EVOLVE_MIN_OUTCOMES_FOR_RETRAIN` (default 100), `AEGIS_EVOLVE_AUC_IMPROVEMENT_THRESHOLD` (default 0.02), `AEGIS_EVOLVE_DRIFT_THRESHOLD` (default 0.15), `AEGIS_EVOLVE_RL_LEARNING_RATE` (default 0.01).  
**REST API**: `/evolve/health`, `/evolve/status`, `/evolve/retrain`, `/evolve/outcomes`, `/evolve/drift`, `/evolve/policy`, `/evolve/runs`  
**CLI**: `aegis evolve status|retrain|drift|policy|outcomes|record`  
**DB migration**: `db/migrations/0011_evolve.sql`  
**ADR**: `docs/adr/0009-autonomous-evolution.md`

### Phase 10 — Data Lake & Analytics (`src/aegis/datalake/`)

```
DataLake (top-level composer)
   │
   ├─ bronze/   → raw ingest: PostgresSignalsIngester, RedisStreamIngester, BronzeWriter
   ├─ silver/   → SilverBuilder: cleaned + conformed (signals + predictions per UTC date)
   ├─ gold/     → GoldAggregator: daily platform stats, verdict rollup, prediction accuracy
   ├─ catalog/  → LakeCatalog (SQLite-backed table + partition registry)
   ├─ storage/  → StorageBackend protocol: LocalStorageBackend | S3StorageBackend
   ├─ query/    → DuckDBQueryEngine (read-only SQL over registered catalog views)
   └─ orchestration/ → Prefect 3 daily-lake-refresh flow (graceful no-op if Prefect absent)
```

**Storage layout**: `{bucket}/{layer}/{table}/dt=YYYY-MM-DD/tenant_id={uuid}/{batch_id}.parquet`  
**Idempotency**: each batch has a SHA256 content-addressable `batch_id` — safe to replay.  
**DuckDB**: embedded in-process, 2 GB memory limit, 4 threads, 30s query timeout.

### Phase 11 — LLM Orchestration (`src/aegis/llm/`)

```
LLMGateway (process singleton via agents_bridge)
   │
   ├─ ProviderSelector    → health-TTL cache (60s); skips circuit-open providers
   ├─ SemanticRouter      → cosine-similarity short-circuit (threshold 0.75)
   ├─ providers/          → Ollama(0) → vLLM(1) → Groq(2) → OpenRouter(3) →
   │                         Gemini(4) → Anthropic(5) → OpenAI(6)
   ├─ guardrails/         → GuardrailsValidator (max-len/PII/toxic) + PIIScrubber
   ├─ instructor/         → InstructorAdapter: typed Pydantic output + 1-retry self-correction
   ├─ cache.py            → 2-layer LRU (in-process 512 entries) + Redis; bypass temp > 0.5
   └─ bridge/agents_bridge → complete_for_agent(node_name, messages) — single call for agents
```

**Circuit breaker**: 5 consecutive failures → open; auto-recovery after 60s.  
**GuardrailBlock fail-fast**: `LLMGateway` re-raises `GuardrailBlock` immediately — never rotates to next provider.

### Phase 12 — Security & Secrets (`aegis-phase12/` — uv workspace member)

```
aegis.security (via __path__ extension)
   ├─ VaultClient         → HashiCorp Vault KV v2 + Transit; circuit breaker; token renewal
   ├─ PIIScrubber         → regex pass (email/phone/SSN/Aadhaar/PAN/IPv4/JWT) + spaCy NER
   ├─ AuditLogger         → HMAC-SHA256 chained entries; MinIO WORM archival
   ├─ RBACEnforcer        → viewer < analyst < operator < admin; JWT FastAPI integration
   ├─ SecretsManager      → Vault → SOPS → env fallback chain
   ├─ SecretRotationManager → CAS write + grace-period dual-version window
   └─ RateLimitMiddleware → Redis Lua token-bucket script
```

### Phase 13 — Testing & Quality (`aegis-phase13/` — uv workspace member)

```
aegis.testing (via __path__ extension)
   ├─ Fixtures    → fake_redis, fake_pg_pool, fake_llm_gateway, fake_minio stubs
   ├─ Helpers     → assert_structlog_event, wait_for_redis_key, …
   ├─ Schemas     → pandera DataFrame validators for signals, predictions, alerts
   └─ Property    → hypothesis strategies for TrendCandidate, FeatureWindow, AlertEnvelope
```

### Phase 14 — Observability & SLO (`src/aegis/observability/`)

```
aegis.observability
   ├─ tracing.py      → init_tracing(service_name) — OTel TracerProvider + OTLP exporter → Jaeger
   │                     get_tracer() → process-singleton tracer
   │                     instrument_fastapi(app) — auto-instrument routes + middleware
   │                     _NoOpTracer / _NoOpSpan fallback (no crash when OTel absent)
   ├─ metrics.py      → 24 Prometheus metric definitions (aegis_obs_* namespace)
   │                     ingest_signals_total, ingest_latency_ms, ingest_errors_total
   │                     agent_task_duration_ms, agent_task_errors_total
   │                     model_inference_latency_ms, model_prediction_precision, model_drift_score
   │                     llm_requests_total, llm_tokens_total, llm_cost_usd
   │                     alert_delivered_total, alert_delivery_latency_ms, killswitch_engaged_total
   │                     database_query_duration_ms, redis_operation_duration_ms
   │                     cache_hit_ratio, dedup_ratio, signals_per_source
   │                     start_metrics_server(port=8001) — idempotent Prometheus HTTP endpoint
   └─ sentry_init.py  → init_sentry() — Sentry SDK with FastAPI/asyncpg/httpx integrations
                         reads SENTRY_DSN; no-op when absent; traces_sample_rate via SENTRY_SAMPLE_RATE
```

**Docker services**: `loki` (port 3100, Grafana Loki 3.1.0) + `promtail` (no exposed port, Grafana Promtail 3.1.0) added to `docker-compose.yml`. Promtail ships all container logs to Loki; Grafana Loki datasource auto-provisioned.  
**Grafana Mission Control dashboard**: pre-provisioned at `config/grafana/provisioning/dashboards/`.  
**SLO definitions**: `docs/slo/aegis-slos.md` — error budgets and alerting rules for all phases.  
**ADR**: `docs/adr/0014-observability.md`.

### Phase 15 — Disaster Recovery & Business Continuity

**Integrated backup module** (`src/aegis/backup/`):

```
aegis.backup
   ├─ BackupManager    → pgBackRest incremental Postgres backups (AEGIS_BACKUP_PGBACKREST_*)
   ├─ ResticBackup     → encrypted filesystem snapshots (AEGIS_BACKUP_RESTIC_*)
   └─ BackupHealth     → staleness monitor + consecutive-failure alerting
                         HEALTH_CHECK_INTERVAL_S=1800; alert after N consecutive failures
```

**Full DR orchestrator** (`aegis-phase15/src/aegis/dr/`):

```
aegis.dr (standalone, NOT in uv workspace yet)
   ├─ DrOrchestrator   → asyncio task loop: runs all backup jobs on intervals
   │                     Postgres (900s), Redis (300s), ChromaDB (3600s), Models (3600s)
   │                     schedules weekly restore drill (604800s)
   ├─ RestoreDrill     → restore backup → validate row counts → emit DrillResult
   │                     drill_pg_dsn must differ from prod pg_dsn (safety check)
   ├─ DrHealthChecker  → RPO drift computation → SlaSnapshot (ok/warning/critical)
   ├─ backup/          → postgres.py (pg_dump -Fc), redis.py (BGSAVE + upload),
   │                     restic.py, models.py (ModelRegistryBackup to MinIO)
   ├─ restore/         → postgres.py (pg_restore --jobs=4)
   ├─ api.py           → FastAPI router: /dr/status /dr/health /dr/drill /dr/backup
   └─ cli.py           → aegis dr backup|restore|drill|status|runbook|health
```

**SLA targets**: RPO = 15 min (`RPO_TARGET_S = 900`), RTO = 60 min (`RTO_TARGET_S = 3600`).  
**Failure modes covered**: `pg_corruption`, `redis_oom`, `disk_full`, `docker_dead`, `laptop_stolen`, `network_outage`, `wsl_crash` — each with a YAML runbook.  
**Error codes**: `AEGIS-DR-0001..0030`.  
**Integration**: Phase 4 ntfy notifier used for backup failure alerts (`AEGIS_DR_NTFY_TOPIC`).

### Dashboard — Command Center (`src/aegis/dashboard/`)

| Component | Purpose |
|-----------|---------|
| `app.py` | FastAPI backend — REST + SSE aggregating all phases |
| `static/index.html` | Dark-theme SPA: ChartJS charts, SSE live feed, ops console |
| `cli.py` | `aegis dashboard serve` entry point |

**Connection pools**: module-level `_pg_pool` (asyncpg, min=2 max=10) and `_redis_pool` (aioredis, max=10) created in FastAPI lifespan — never per-request.  
**SSE handler**: `sse_events` uses the shared `_get_redis()` pool — does **not** open a new connection per browser tab.  
**Ops console security**: `POST /api/ops/run` only accepts first-token in `{"aegis", "uv", "python", "docker"}`.

---

## Infrastructure

| Service | Port | Purpose |
|---------|------|---------|
| aegis-postgres | 5433 | TimescaleDB + pgvector |
| aegis-redis | 6380 | Cache + inter-agent streams |
| aegis-minio | 9002/9003 | Object store + WORM audit bucket |
| aegis-flaresolverr | 8191 | Cloudflare bypass for e-commerce |
| aegis-predict | 8100 | Phase 3 FastAPI inference |
| aegis-execute-api | 8200 | Phase 4 FastAPI alerts + SSE |
| aegis-execute-drain | — | Phase 4 outbox drain worker |
| aegis-dashboard | 8300 | Command Center web UI |
| aegis-prefect | 4200 | Phase 10 Prefect 3 orchestration UI (optional) |
| ollama | 11434 | Phase 11 local LLM runtime |
| litellm | 8080 | Phase 11 LiteLLM proxy (profile: llm-proxy, optional) |
| aegis-prometheus | 9091 | Metrics scrape |
| aegis-grafana | 3001 | Dashboards + Mission Control (admin/aegis_dev_admin_pw) |
| aegis-jaeger | 16687 | Distributed traces (OTLP gRPC :4317) |
| aegis-loki | 3100 | Phase 14 log aggregation (loopback only) |
| aegis-promtail | — | Phase 14 log collector (ships Docker logs → Loki) |

---

## Key Cross-Cutting Decisions

| Concern | Decision |
|---------|---------|
| Logging | `structlog` everywhere in `aegis.agents.*` and `aegis.agents_phase3_glue.*`; never stdlib logging |
| Tracing | Phase 14 OTel `init_tracing(service_name)` at FastAPI startup; `get_tracer()` + span context managers in hot paths |
| Metrics | `aegis.core.metrics` for Phase 0–5 (`aegis_*`); `aegis.llm.metrics` for Phase 11 (`aegis_llm_*`); `aegis.observability.metrics` for cross-cutting (`aegis_obs_*`) |
| Log aggregation | Promtail ships all Docker container logs → Loki; query in Grafana Explore → Loki datasource |
| Resilience | `predict.resilience` — functional `resilient_call(op, name, timeout_s)` for ML ops; `core.resilience` — decorator `@resilient_call(policy)` for scraper I/O |
| Stream field | Redis stream entry field is `"body"` (never `"payload"`) — Phase 2 runner, Phase 4 IntakeWorker, and Dashboard all agree |
| Stream cap | `aegis:phase2:graph_results` capped at 10,000 entries; agent messaging bus and swarm results stream also capped |
| Event loop | CPU-heavy scrape ops (`score_batch`, `deduplicate_batch`, `detect_patterns`) offloaded via `asyncio.to_thread` |
| GuardrailBlock | `LLMGateway` re-raises `GuardrailBlock` immediately without rotating to the next provider |
| Tenancy | All DB tables use RLS on `app.current_tenant`. Default dev UUID: `00000000-0000-0000-0000-000000000001` |
| Frozen models | `TrendCandidate`, `AgentDecision`, `GraphResult` are immutable after construction |
| Workspace namespace | `aegis-phase4/src/aegis`, `aegis-harden/src/aegis`, `aegis-phase12/src/aegis`, `aegis-phase13/src/aegis` appended to `aegis.__path__`; `src/aegis/observability/` and `src/aegis/backup/` are regular subpackages |
| Data lake idempotency | SHA256 content-addressable `batch_id` per Parquet batch; re-running same data is safe |
| Backup RPO/RTO | Postgres backups every 15 min (matches RPO target); RTO 60 min validated by weekly automated drill |
| OTel graceful deg. | All `opentelemetry-*` imports wrapped in `try/except`; service never crashes when OTel packages absent |
| Docker resource limits | `deploy.resources.limits` set on all stateful services; use `docker-compose.enterprise.yml` overlay in production |

---

## Package Dependency Graph

```
aegis.harden  (aegis-harden workspace member)
     │  (optional — via HardenShim graceful degradation)
     ▼
aegis.scrape  ←── aegis.llm (Phase 11 LLM Orchestration)
     │                │
     ▼                │
aegis.db / aegis.cache│
     │                │
     ▼                ▼
aegis.agents  ←──  aegis.agents.llm (shim → aegis.llm.bridge.agents_bridge)
     │         ←──  aegis.agents_phase3_glue  ←──  aegis.predict
     │
     ▼
aegis.execute  (aegis-phase4 workspace member)
     │   ├── engine.py / pricing.py / approval.py / settlement.py  (Phase 6)
     │   └── aegis.fulfillment  (src/aegis/fulfillment/)  ← Printful / CJ / Shopify
     │
     ├── aegis.geo         (Phase 7 — src/aegis/geo/)
     │     └── phase6_bridge → ExecutionIntent (guarded by ImportError)
     │
     ├── aegis.compliance  (Phase 8 — src/aegis/compliance/)
     │     └── gate_execution_plan() → integrated into Phase 6 ExecutionEngine
     │
     ├── aegis.evolve     (Phase 9 — src/aegis/evolve/)
     │     ├── OutcomeRecorder ← settled trade outcomes from Phase 6 SettlementManager
     │     ├── DriftDetector   → auto-rollback trigger to Phase 3 ModelStore champion
     │     ├── RetrainingPipeline → promotes new champion to Phase 3 ModelStore
     │     └── OnlinePricingPolicy → daily weight updates feed Phase 6 PricingStrategy
     │
     ▼
aegis.datalake  (Phase 10 — src/aegis/datalake/)
     │
     ├── aegis.security  (aegis-phase12 workspace member)
     │
     ├── aegis.testing   (aegis-phase13 workspace member)
     │
     ├── aegis.observability  (Phase 14 — src/aegis/observability/)
     │     observes: all phases via Prometheus + OTel + Sentry
     │
     ├── aegis.backup    (Phase 15 integrated — src/aegis/backup/)
     │     backs up: aegis.db + model registry + Redis
     │
     └── aegis.dashboard  ──────────── aggregates all phases above
```

---

## Test Coverage Summary

| Scope | Tests | Coverage |
|-------|-------|---------|
| Phases 0–14 unit (`tests/unit/`) | 1589+ | ~79% (floor 78%) |
| Phase 4 + Phase 6 unit (`aegis-phase4/tests/`) | 164 + 55 = 219 | standalone |
| Phase 6 (`aegis-phase4/tests/unit/execute/test_capital_*.py`) | 55 | included above |
| Phase 7 geospatial (`tests/unit/geo/`) | 60+ | included in main suite |
| Phase 8 compliance (`tests/unit/compliance/`) | 80 | included in main suite |
| Phase 9 self-evolution (`tests/unit/evolve/`) | 145 | included in main suite |
| Phase 5 hardening (`aegis-harden/tests/`) | 216+ | ~95% (run separately) |
| Phase 14 observability (`tests/unit/observability/`) | 42 | included in main suite |
| Phase 15 DR (`aegis-phase15/tests/`) | 5 files | standalone (not in workspace) |
| Phase 3 integration (`tests/integration/predict/`) | 4 | end-to-end |

**Verified**: 2026-06-02. Phase 7 Geospatial Intelligence (60+ tests, real WTO/ECB/EMS data), Phase 8 Regulatory & Compliance Engine (80 tests, 7-dimension risk matrix), and Phase 9 Autonomous Self-Evolution (145 tests: outcome recording, weekly retraining + Optuna HPO, KS-distance drift detection, 4-weight REINFORCE online policy) shipped. All three phases at 0 ruff violations. One pre-existing flaky test (`test_doctor_json`) fails in full-suite ordering; passes in isolation — known ordering issue, not a code defect.
