# AEGIS Pulse — Comprehensive Audit & Remediation Plan

**Audited:** 2026-06-09 · **Mode:** read-only investigation (no code modified) ·
**Evidence base:** full unit suite (2052 passed / 2 skipped / 0 failed, 23m19s),
`ruff check`, test collection (2054 tests, 0 import errors), `docker-compose.yml`,
`.env` / `.env.example`, all 46 scrape adapters, CLI/API wiring, Redis-stream
connectivity, dashboard backend + frontend.

> **How to read this:** every finding has a stable ID (e.g. `ORPH-1`), a severity,
> the **evidence** (file:line), the **fix technique / algorithm**, and the
> **fixtures/artifacts you must create**. Severity legend below.

---

## ✅ Remediation log — 2026-06-09

The high-impact crash/breakage/trap findings have been fixed in code. Status:

| ID | Status | What changed |
|----|--------|--------------|
| **LINT-1** + E402 | ✅ Fixed | `dashboard/cli.py` `subprocess.run(check=False)` + PID validation; `test_runner.py` E402 `# noqa`. `ruff check src/ tests/` → **All checks passed**. |
| **DASH-1** | ✅ Fixed | Research jobs now run under `asyncio.wait_for(RESEARCH_JOB_TIMEOUT_S=300)` via `_run_research_job_guarded`; eviction (`_cleanup_research_jobs`) is now **age-ordered & status-agnostic** (caps at `MAX_RESEARCH_JOBS=20`) and **cancels** the background task of any evicted unfinished job. Kills the unbounded-dict / leaked-task / pool-exhaustion hang. |
| **DASH-5** | ✅ Fixed | `_acquire_pg` now calls `_set_tenant()` on **every** acquired connection (`SELECT set_config('app.current_tenant', …)`), closing the RLS "silent 0 rows" trap. |
| **ORPH-1** | ✅ Fixed | New `src/aegis/api/main.py` `create_app()` mounts geo/compliance/evolve/datalake routers (graceful per-router try/except); `aegis api serve` CLI (`:8400`); `tests/unit/api/test_mounting.py` asserts all four mount. |
| **ENV-1** | ✅ Fixed | `scripts/gen_env_example.py` introspects all Settings models → regenerated `.env.example` (151 keys, was missing 16+). `--check` mode for CI. |
| **STRUCT-2/3** | ✅ Fixed | `.hypothesis/` + untracked duplicate phase scratch folders (incl. the space-named `aegis-phase 14`) added to `.gitignore`. Workspace members left intact. |
| **ADP-2 / HALLU-4** | ✅ Fixed | New `AdapterStatus.NEEDS_CREDENTIALS`; `SwarmAgentPool` now classifies key-gated adapters distinctly (24h cooldown, 1-strike down-threshold) instead of "success, 0 signals". |
| **ORPH-2** | ✅ Fixed | `aegis autonomous run` CLI wired to the APScheduler loop. Also fixed a **latent bug** in `scheduler/autonomous.py::job_scrape` (called `scrape_topic(limit=…, use_llm=…)` — params don't exist → `total_signals` attr error every cycle); now `limit_per_source=` + `total_unique`. Hardcoded DSNs replaced with `settings().pg_dsn_str`. |
| **HALLU-1** | ✅ Fixed | Provenance propagated end-to-end: `GraphResult.llm_used` + `reasoning_source` (`llm`/`heuristic`/`mixed`, aggregated from per-agent `used_llm` in `supervisor.py`) → phase2 stream (`runner.py`) → `/api/agents/recent` → dashboard **Source badge**. A heuristic-only verdict is no longer indistinguishable from real LLM analysis. |
| **CONN-2** | ✅ Fixed | Compliance now gates the **live alert path**: new `compliance_ftc_gate()` in Phase 4 `risk/gates.py` (first in `default_chain()`) runs the zero-I/O `FTCRuleEngine` on the alert title/summary and downgrades a deceptive-advertising ENTER → BLOCK (`AEGIS-EXEC-0014`). Degrades to no-op if Phase 8 absent. (IPR/FDA/OFAC stay on the Phase 6 plan path — they need origin/dest + live calls the trend alert can't supply on the hot path.) +2 tests. |
| **HALLU-2** | ✅ Fixed | `ComplianceRiskAssessment.data_sources` records per-dimension `live` vs `static` (e.g. `aml=live` only when `trade_gov_api_key` set; `ftc`/`privacy=static`; `counterfeit=live` only with CLIP). A "clear" now says whether it was actually checked. |
| **HALLU-3** | ✅ Fixed | `GeoOpportunity.demand_source` (`db` vs `synthetic`) threaded from `RegionalDemandAnalyzer` → `CrossMarketAnalyzer`. A market-size-proxy estimate no longer looks identical to a real signal-velocity score. |
| **ADP-1** | ✅ Fixed | Broken adapters (`tiktok`/`pinterest`/`nitter`) confirmed **already absent from swarm waves** (no per-run cost). The remaining gap was the CLI: `aegis scrape --source tiktok` now raises a clear quarantine error unless `--include-experimental` is passed (`_QUARANTINED_ADAPTERS` set + flag threaded `scrape`→`_scrape_async`→`_load_adapter_class`). |
| **INFRA-4/5** | ✅ Fixed | Documented the two operational traps in `DEVELOPER_GUIDE.md` §4: (INFRA-4) `docker compose exec/logs` take the **service** name (`predict`), not the container name (`aegis-predict`) — the latter yields a misleading "service not running"; (INFRA-5) `predict` is `8100:8000` — host `:8100` is correct, in-cluster callers use `predict:8000`. Plus the host-mode dashboard note. |
| **DASH-4** | ✅ Fixed | Dashboard pg pool hardened: acquire-side timeout already existed (`_acquire_pg(timeout=3.0)`), so this added a bigger ceiling (`max_size` 10 → **20**, env `AEGIS_DASHBOARD_PG_POOL_MAX`) and a server-side `command_timeout=10s` (env `AEGIS_DASHBOARD_PG_COMMAND_TIMEOUT`) so a slow query can't pin a pooled connection forever. Removes the "concurrent tabs + SSE + research exhaust 10 conns → hang" vector. 8 dashboard tests green. |
| **INFRA-1/2** | ✅ Fixed | **INFRA-1** already satisfied — dashboard service is gated behind `profiles: ["dashboard"]` (host mode `uv run aegis dashboard serve` is the default/recommended path; container is opt-in). **INFRA-2** — removed the `/var/run/docker.sock:ro` mount (+ now-unneeded `group_add: DOCKER_GID`) from the base dashboard service: it runs the shell-executing ops console, so the Docker API was a container-escape surface. `_docker_ps()` already returns `[]` on socket-absent, so the status panel degrades gracefully (host mode retains live status). Matches what the enterprise overlay already did. |
| **DASH-2** | ✅ Fixed | New **Markets** dashboard page surfaces phases 7/8/9: Geo Arbitrage table (route + margin% + db/synth demand-provenance badge), Compliance Assessments table (PROCEED/ESCALATE/BLOCK + composite risk), Self-Evolution Events table (retrain/status/AUC). `loadMarkets()` consumes the CONN-1 endpoints (`/api/geo|compliance|evolve/recent`); nav item + page + both loader/refresher maps wired. JS validated with `node --check`. (Capital ledger panel deferred — Phase 6 has no event-bus producer yet.) |
| **INFRA-3** | ✅ Fixed | `deploy.resources.limits` added to all 10 previously-uncapped long-running services (minio, flaresolverr, execute-api, execute-drain, prometheus, grafana, jaeger, prefect, litellm, langfuse). Now 18/19 services capped (only one-shot `ollama-init` uncapped, by design) — closes the WSL2 OOM risk. `docker-compose.yml` validates as YAML. |
| **CONN-1** | ✅ Fixed | Unified event bus: new `core/event_bus.py` `publish_event(stream, payload)` (capped `XADD {"body": json}`, best-effort cached client, never raises) + canonical streams `aegis:phase7:geo_opportunities` / `aegis:phase8:compliance_assessments` / `aegis:phase9:evolve_events`. **Producers:** geo `/analyze`, compliance `/assess`, evolve `/retrain` publish their results. **Consumers:** dashboard `/api/geo/recent`, `/api/compliance/recent`, `/api/evolve/recent` (shared `_read_phase_stream`, limit capped 100). 11 tests; 331 phase + 8 dashboard tests green. (Frontend cards = remaining DASH-2 UI; datalake bronze ingest of these streams = CONN-4.) |
| **ADP-4** | ✅ Fixed | Adaptive scrape-budget allocation (BRAIN-2). New `scrape/budget.py` `UCB1Allocator` (multi-armed bandit: `mean_reward + c·√(ln N / n)`, reward = signals yielded, cap 100). `SwarmOrchestrator.run_all_waves` now allocates each wave's per-adapter `limit` by UCB1 score — productive adapters get up to 2× base, quiet ones stay alive at `min_limit=5`, never-played arms force-explore at base — and feeds realised yields back via `allocator.record()`. New `run_wave(..., limits=)` optional per-agent override (backward compatible). Knob `scrape.swarm_adaptive_budget` (default True). 6 tests; 52 swarm tests green; `.env.example` regenerated (177 keys). |
| **ADP-6** | ✅ Fixed | Self-healing schema-drift quarantine. New `scrape/schema_drift.py` `SchemaDriftTracker` (per-platform EWMA drop-rate, `is_drifting` once smoothed drop-rate ≥ 0.5 over ≥20 observed signals). `schema_guard.validate_batch` now **feeds** the tracker (was log-only). Swarm `run_agent` **consults** it: a run that "succeeds" while most signals were dropped is downgraded to new `AdapterStatus.SCHEMA_DRIFT` and one-strike `record_failure` → 30-min cooldown (adapter quarantined until shape recovers; drop-rate decays on clean batches). 8 tests; 114 existing swarm/RSS tests green. |
| **ADP-3** | ✅ Fixed | Conditional GET (ETag / Last-Modified) added once in the shared `_rss_base.fetch_feed_entries` → all **12** feedparser RSS adapters now send `If-None-Match`/`If-Modified-Since` and skip parsing on HTTP 304. Per-URL validator cache (`_FEED_VALIDATORS`) + `clear_feed_cache()` test hook. 5 new tests (`test_rss_conditional_get.py`); existing 137 RSS adapter tests still green. |
| **STRUCT-1** | ✅ Fixed | 6 dead duplicate phase folders removed from the tree (all confirmed integrated into `src/aegis/`, none workspace members, none referenced in pyproject/compose/uv.lock): `aegis-phase6`/`7`/`pulse-phase8`/`pulse-phase9-0.9.0`/`aegis-phase 14` (untracked) + `aegis-phase11` (`git rm`, 91 files). All backed up to gitignored `archive/` for recoverability. `aegis-phase15` (active standalone DR module) kept. Top-level now: only the 4 workspace members + phase15. |
| **ORPH-3** | ✅ Fixed | Per-module decision made. **Wired:** `realtime_consumer.py` now reachable via `aegis realtime run` (XREADGROUP push consumer — also closes **CONN-4 / BRAIN-5**); `council.py`/`explainer.py`/`drift_monitor.py` already called from `agents_bridge`/`runner`/`scheduler`. **Tested:** new `tests/unit/scrape/test_anomaly_detector.py` (7 tests, 92% cov) — was the only unomitted untested module; new `tests/unit/scrape/test_realtime_consumer.py` (5 tests on `_build_candidate`/`_process_entry`). `council`/`explainer`/`stream_bridge` already 90–100%. `realtime_consumer.run()` (infinite loop) + `autonomous.py`/`api/main.py` stay coverage-omitted by design. Coverage floor restored ≥78%. |

### Remediation log — 2026-06-11 (GODMODE Pass 1)

| ID | Status | What changed |
|----|--------|--------------|
| **CONN-3** | ✅ Fixed | Predict path consolidated (ADR `docs/adr/0016-predict-path-consolidation.md`): agents use the **in-process bridge exclusively** (Pass 0 autopsy verified zero httpx/:8100 calls in the agent graph); HTTP :8100 stays for external consumers. New `AEGIS_PREDICT_FORCE_HTTP=true` debugging override in `agents_phase3_glue/bridge.py` (`_run_via_http` → POST `/predict`, duck-typed `_HttpInferenceResult`, falls back to in-process on any HTTP failure). |
| **CONN-4** | ✅ Fixed | Datalake bronze `RedisStreamIngester` gained **XAUTOCLAIM crash recovery**: PEL entries idle >5 min (abandoned by dead consumers) are reclaimed before each `XREADGROUP` pass; reclaim is best-effort (graceful when client lacks `xautoclaim`). ACK stays post-persistence; a failed Parquet write leaves entries un-ACKed for PEL retry. 12 tests in `tests/unit/datalake/test_redis_ingester_cg.py`. |
| **DASH-2** | ✅ Fixed (completed) | The deferred panels are now live. **Backend:** `/api/capital/recent` (execution_plans, RLS, graceful when unmigrated), `/api/dr/status` (DrHealthChecker, graceful when phase15 absent), `/api/swarm/recent` (results stream), `/api/anomalies/recent` (anomalies stream — publisher lands in Pass 9), `/api/health/streams` (xinfo traffic-light for 7 canonical streams: healthy/stale/empty/error). **Frontend:** Capital + Anomalies panels (Markets), new **Swarm** nav page (runs stream + 24h DB history + agent-health grid), Stream Health + DR/Data-Lake panels (Pipeline). All auto-refresh on the 30s poll, with empty/error states. Verified live against the running stack. |
| **DASH-3** | ✅ Fixed | All 14 unused endpoints decided. **Wired:** `/api/signals/stats` (overview stats bar: velocity/hr + top platform), `/api/predictions/recent` + `/api/agents/recent` (Intelligence: prediction feed + per-agent decision breakdown), `/api/swarm/history` + `/api/swarm/agents` (Swarm page), `/api/datalake/status` (Pipeline DR/Lake panel), `/api/system` (Console system metrics: env/version), `/api/search` (Signals page search box), `/api/watchlist/add`/`remove` (Research watchlist UI), `/api/topic/research/{id}/push-alert` (PUSH button on done research rows). **Deleted** (inline `DASH-3: DELETED` comments): `/api/platforms/stats` (superseded by `/api/signals/platforms`), `/api/platforms/trends` (superseded by `/api/signals/velocity`), `/api/execute/alerts` (duplicate of `/api/alerts/recent`). |
| **DASH-4** | ✅ Fixed (completed) | Final piece on top of the 2026-06-10 pool work: `_acquire_pg` now maps pool-acquire `TimeoutError` → **HTTP 504** + increments new collision-safe Prometheus counter `aegis_obs_dashboard_pool_acquire_timeout_total` (no-op stub without prometheus_client). 4 tests in `tests/unit/dashboard/test_pool_timeout.py` incl. RLS-set-on-acquire regression. |
| **ENV-2** | ✅ Fixed (completed) | Startup hard-fail added: `_require_ops_token_in_prod()` runs first in the dashboard lifespan and **refuses to boot** when `AEGIS_ENV ∈ {prod, staging}` with empty `AEGIS_DASHBOARD_OPS_TOKEN` (ops console = shell exec = unauthenticated RCE). Dev keeps the WARNING; request-time 503 stays as defense-in-depth. (Deviation from spec noted: dev is exempt to preserve the host-mode workflow.) |
| **ENV-3** | ✅ Fixed | `_probe_llm_backends()` fire-and-forget task in dashboard lifespan: probes the Phase 11 gateway (`gw.health()`, 5s budget) and logs `startup.llm_probe.all_unreachable … heuristic_only_mode=True` when no provider answers. Never blocks/fails startup; task ref kept + cancelled on shutdown. 3 tests. |
| **ORPH-4** | ✅ Fixed | Daily lake refresh scheduled both ways. **Prefect:** new parameterless `daily_lake_refresh` flow (builds own pool from `AEGIS_DATALAKE_POSTGRES_DSN`/`AEGIS_PG_DSN`; skips Bronze without DSN) + `aegis datalake schedule` CLI that serves it as deployment `aegis-daily-lake-refresh` (cron `0 2 * * *`). Spec's import-time `serve()` deliberately NOT used — it blocks module import. **Fallback:** `job_datalake_refresh()` in `scheduler/autonomous.py` (daily 02:00 UTC cron, never raises; double-run safe — Bronze batch_ids are content-addressed). 3 tests. |
| **INFRA-2** | ✅ Follow-up | `_docker_ps()` now honours `DOCKER_HOST=tcp://…` (TCP Docker API for socketless containers) before falling back to the Unix socket, then to `[]`. |

**Remaining (larger / strategic, not yet done):** ADP-5 (MinHash dedup), ADP-7 completion, BRAIN-1..6 (self-evolution loop closure, dynamic thresholds, weighted ensemble — GODMODE Passes 2–3), Pass 4+ (adapter routing, self-healing, research engine, OSS integrations). Tracked below unchanged.

### Remediation log — 2026-06-12 (GODMODE Pass 2 — Deep Intelligence Upgrades)

| ID | Status | What changed |
|----|--------|--------------|
| **2A / ADP-5** | ✅ Fixed | MinHash + LSH dedup in `src/aegis/db/dedup.py` (the real dedup module — spec's `scrape/dedup.py` path doesn't exist). 3-layer pipeline: exact token-set → MinHash(128 perms, char 3-grams) LSH candidate retrieval **confirmed by the precise token-Jaccard/SequenceMatcher similarity** (same decisions as the old O(n²) scan, O(n log n) retrieval) → optional BGE-M3 semantic layer (cosine > 0.92, gated `AEGIS_DEDUP_SEMANTIC_ENABLED`, async-only in `deduplicate_signals`, skips when Ollama absent). `deduplicate_batch` keeps its sync signature (runs in `asyncio.to_thread`, §12) so the MinHash cache is a bounded in-process LRU (documented spec deviation) with a shared-permutations table (1000 signals < 500 ms, perf-asserted). New `DedupResult.dedup_method` ∈ exact/minhash/semantic/pass via `deduplicate_batch_detailed`. `datasketch>=1.6.0` added. 14 tests (`tests/unit/db/test_dedup_minhash.py`). |
| **2B / BRAIN-2b** | ✅ Fixed | UCB1 composite quality reward: `UCB1Allocator.record_with_quality()` = 0.5×normalized-yield (signals/s, cap 100/s) + 0.3×novelty + 0.2×avg-confidence. Swarm computes novelty per run via Redis set `aegis:swarm:seen_hashes:{date}` (TTL 25 h, pipelined SISMEMBER/SADD; neutral 1.0 when Redis absent) and feeds `latency_ms`-derived elapsed. Empty/failed runs record reward 0 (the neutral-novelty fallback must not give failing adapters a 0.3 floor). 5 allocator + 4 swarm tests. |
| **2C / BRAIN-3** | ✅ Fixed | New `src/aegis/core/dynamic_thresholds.py`: confidence gate / velocity slope / compliance block threshold adapt ±0.02 weekly from realized precision vs 0.80 target over last-30d `prediction_outcomes`, hard bounds (0.60–0.95 / 1.0–5.0 / 0.50–0.90), Redis key `aegis:core:thresholds` (TTL 8d) + 1 h local cache. Getters take `fallback=` — adaptive values only win once `outcome_count > 0`, so injected settings keep working. Wired: `topic.py` (gate before `score_batch`, slope injected via new `analytics.set_high_priority_slope()` before `detect_patterns` — sync CPU code can't await), `compliance/engine.py` (block threshold; hard OFAC/FDA overrides unaffected), `job_threshold_update()` Sun 3 AM in `scheduler/autonomous.py`. 14 tests. |
| **2D / BRAIN-4** | ✅ Fixed | Feedback-weighted ensemble in `agents/supervisor.py`: per-agent accuracy weight = 0.5 + realized accuracy (0.5–1.5, clamped), multiplied with the static role weight in `compute_final_score` — uniform weights cancel in normalisation, so behaviour is regression-identical until real history exists. Weights live in Redis hash `aegis:agents:accuracy_weights` (TTL 7d), computed nightly by `job_weight_update()` (3:30 AM) via `compute_accuracy_weights()` (joins phase2 stream votes against settled `prediction_outcomes`; ENTER-vote correct iff roi>0, HOLD/BLOCK correct iff roi≤0). Runner refreshes best-effort pre-graph (1 h cache, neutral on any failure). `GraphResult` gained `agent_weights` + `weight_update_ts`. 14 tests (`test_supervisor_weighted.py`). |
| **2E** | ✅ Fixed | `FEATURE_DIM` 20 → **24** (schema 3.1.0): new tail features `cross_platform_coherence` (pairwise cross-platform title Jaccard, ≤64 pairs), `temporal_autocorr_lag1` (trailing-24-bucket lag-1 autocorr), `author_diversity_ratio` ((authors−1)/(signals−1)), `geo_spread_entropy` (normalized entropy over row region or platform→region map). All fall back to 0.0, never NaN. `LEGACY_FEATURE_NAMES_V3` accepted by the `FeatureWindow` validator; `pad_legacy_window()` pads 20-dim windows in `InferenceRunner` (first 20 indices byte-identical). `EVOLVE_FEATURE_DIM` → 24; factory's hardcoded `feature_dim=20` → `FEATURE_DIM`; all 24 features documented in `docs/features.md`. 8 new tests. **Bonus fix:** latent `heuristic_predict` `log1p` domain-error on negative counts (violated the never-raise doctrine) — guarded. |
| **2F / ADP-6b** | ✅ Fixed | Schema-drift **auto-recovery**: `QuarantineReason` enum (schema_drift / error_rate / credentials_missing / manual) stored per platform; `record_clean_batch()` + clean-batch accounting inside `record_batch()` (the `validate_batch` path) decay the EWMA and lift quarantine after 3 consecutive clean batches with smoothed drop-rate < 0.2 (`schema_drift.quarantine_lifted` log event). Swarm passes typed reasons on SCHEMA_DRIFT and NEEDS_CREDENTIALS paths. 9 new tests. |

Pass 2 verification: `ruff check` 0 violations (main + phase4/12/13 src; aegis-harden's `TC001` selector error is the pre-existing ruff-version mismatch), full unit suite green, coverage ≥ 78% floor. Pre-existing failures NOT caused by Pass 2: 2× `aegis-phase13/tests/unit/datalake` (`LocalStorageBackend.write_parquet` API drift — reproduces on the pre-Pass-2 tree).

### Remediation log — 2026-06-12 (GODMODE Pass 3 — Self-Evolution Loop Closure / BRAIN-1)

The loop is now **CLOSED**: trade → settle → record → drift check → (rollback | retrain) → shadow → promote → price adapt.

| ID | Status | What changed |
|----|--------|--------------|
| **3A / BRAIN-1a** | ✅ Fixed | Settlement → Phase 9 bridge: `SettlementManager.settle_daily()` now calls `_record_outcome_for_evolution()` per persisted order — builds a `TradeOutcome` (ROI = pnl / max(cost, $0.01), pct), records it via `OutcomeRecorder`, and publishes `outcome_recorded` to `aegis:phase9:evolve_events`. Best-effort end to end: settlement never fails because evolution is unavailable. **Adaptations from spec:** the spec's `settle_plan(plan)` API doesn't exist (real unit is `OrderOutcome`); prediction score/confidence aren't on `execution_plans` — resolved via plan → intent → alert SQL join with neutral 0.5 fallback; spec's `status="failed"` would violate the `prediction_outcomes` CHECK constraint — losses map to `dispute`/`partial_refund`/`full_refund`. 8 tests (`aegis-phase4/tests/unit/execute/test_settlement_evolution.py`). |
| **3B / BRAIN-1b** | ✅ Fixed | `DriftDetector.run_all_checks()` now ends with `_attempt_auto_rollback()`: critical drift (score > 2× `drift_threshold`, or the performance check's `should_rollback`) rolls the champion back via `RetrainingPipeline.rollback_champion()` (spec's `ModelStore.get_previous_champion()`/`activate()` don't exist — the champion flag lives in `model_candidates`), publishes `auto_rollback` to the evolve stream, and at > 3× threshold trips the Phase 4 killswitch (new optional `redis_client` ctor param; skipped with a warning when absent). Never raises. 9 new tests. |
| **3C / BRAIN-1c** | ✅ Fixed | Shadow deployment: improved candidates are no longer promoted instantly — `_register_shadow()` writes them to `model_candidates` with `is_shadow=TRUE` (migration `0012_shadow_models.sql`, applied to dev DB) and publishes `shadow_deployment_started`; retrain run status becomes `shadow_deployed` (CHECK constraint extended). `AEGIS_EVOLVE_FAST_PROMOTE=true` keeps the direct path (dev only). New `evaluate_shadows()` promotes shadows past `shadow_period_hours` (72 h) that still beat the champion AUC by `auc_improvement_threshold`, retires the rest, and events either way; wired as `job_shadow_evaluate()` every 6 h in `scheduler/autonomous.py`. **Deviation:** comparison uses stored test AUC vs current champion AUC (the codebase trains LR proxies — there is no per-shadow live re-scoring on 72 h outcomes yet). New error code `AEGIS-EVOLVE-0011` + `docs/errors/` stub. 11 new tests + 3 scheduler tests. |
| **3D / BRAIN-1d** | ✅ Fixed | `PricingStrategy` now seeds its per-SKU weights from the Phase 9 `OnlinePricingPolicy`: async classmethod `_get_policy_weights(db_pool)` loads persisted RL weights with a class-wide 6 h TTL cache (failures fall back to defaults and are never cached); new instances pick up the cached weights at construction. **Deviation:** spec's `score_price_point()` doesn't exist — integration is via `compute_price()`'s weight vector; codebase default weights (0.25/0.35/0.20/0.20) kept over spec's (0.4/0.3/0.2/0.1) to match `OnlinePricingPolicy._INITIAL_WEIGHTS`. 5 new tests. |

Pass 3 verification (2026-06-12): `ruff check` 0 violations (main src+tests, phase4 src+tests — incl. 6 pre-existing auto-fixed I001 in phase4 integration tests, phase12/13 src; aegis-harden `TC001` = pre-existing ruff-version mismatch). Full main unit suite: **2246 passed, 2 skipped (infra), coverage 78.86%** (≥ 78% floor); the single failure (`test_retrain_coverage.py::test_promotion_fires_when_auc_beats_threshold`) asserted the pre-shadow instant-promotion contract and was updated to the new `shadow_deployed` status (re-verified: 76/76 evolve tests green). Phase 4 suite: 220 passed. Migration 0012 applied to the running dev Postgres.

### Remediation log — 2026-06-12 (GODMODE Pass 4 — Intelligent Adapter Routing Engine)

AEGIS now understands WHERE to look based on WHAT is being asked: one query → topic type → ranked adapter set.

| ID | Status | What changed |
|----|--------|--------------|
| **4A** | ✅ Fixed | New `src/aegis/scrape/topic_classifier.py`: `TopicType` enum (10 intents: ecommerce_product, financial_trend, tech_news, startup_signal, consumer_trend, regulatory, competitor_intel, supplier_discovery, global_arbitrage, market_research), keyword + bigram heuristic classifier (bigrams score 2×; zero latency, zero network), and the `ADAPTER_AFFINITY` matrix. **Deviation from spec:** affinity adapter names use the swarm registry's real names (`techcrunch` not `techcrunch_rss`, `hacker-news` not `hacker_news`, `reuters` not `reuters_rss`, …) so every routed name is directly runnable — a test asserts every affinity entry resolves in `swarm._REGISTRY`. Spec's non-existent `google_trends` dropped in favour of registry's `google_trends_india`. |
| **4B** | ✅ Fixed | New `src/aegis/scrape/adapter_router.py`: `AdapterRouter.route(topic, top_n, exclude_credentials_required, exclude_flaresolverr, explicit_topic_type)` → ranked `AdapterRecommendation` list scored `affinity × health × log(yield+1)/log(101)`, priorities 1..N, human-readable reasons. Credential gating via `_CRED_REQUIRED` env probes (reddit/youtube/instagram); FlareSolverr gating for flipkart/myntra. **Deviation from spec:** spec assumed a `get_all_agents()`/`success_rate` health interface that doesn't exist — `_get_health_scores()` supports it but also adapts the real `SwarmAgentPool.agents` dict, mapping `AgentHealth` → score (healthy 1.0 / unknown 0.7 / degraded 0.45 / down 0.1; cooling → 0.1) and degrading to the optimistic 0.7 default on any tracker failure. |
| **4C** | ✅ Fixed | `scrape_topic()` gained `adapter_override: list[str] \| None` and `max_adapters: int = 12`. Override path runs EXACTLY the named swarm-registry adapters (unknown names → recorded in `result.errors`). Default path classifies the topic, stores `TopicScrapeResult.topic_type` + `routed_adapters`, schedules the unchanged core set (extracted into `_schedule_core_adapters()`, behaviour-identical), then schedules routed registry extras not covered by core (e.g. nse_bse + moneycontrol for finance, flipkart + amazon_in for e-commerce) via new `_build_registry_adapter()`/`_schedule_routed_adapters()` helpers under the same shared semaphore + HardenShim session. Routing failure falls back to core-only with a warning — never raises. **Deviation from spec:** routed extras are gated by `AEGIS_SCRAPE_TOPIC_ROUTING_EXTRAS` (default **enabled**; set to `false` in `tests/conftest.py`) because the existing scrape_topic unit tests mock only the 6 core adapter classes — without the gate they would hit the live network. |

Pass 4 verification (2026-06-12): `ruff check` 0 violations (src/, tests/, aegis-phase4/src/, aegis-phase12/src/, aegis-phase13/src/; aegis-harden `TC001` = pre-existing ruff-version mismatch). Full main unit suite: **2278 passed, 2 skipped (infra), coverage 79.01%** (≥ 78% floor). Phase 4 suite: 220 passed. 31 new tests in `tests/unit/scrape/test_adapter_router.py` (classifier, the 8 spec-mandated router behaviors, health-tracker adaptation, and scrape_topic override/routing/fallback wiring).

### Remediation log — 2026-06-12 (GODMODE Pass 5 — Autonomous Self-Healing System)

The system now detects its own failures and fixes them: a 5-minute immune-system loop plus a pooled HTTP layer that ends per-adapter TLS churn (closes ADP-7).

| ID | Status | What changed |
|----|--------|--------------|
| **5A** | ✅ Fixed | New `src/aegis/scheduler/health_checker.py`: `AegisHealthChecker.check_and_heal()` runs 7 parallel checks — Phase 2 stream staleness (> 30 min → fire-and-forget emergency `job_scrape`), swarm adapter quarantine ratio (> 50% DOWN → critical, > 30% → warning), champion model staleness (> 30 d AND ≥ 100 outcomes → trigger `job_weekly_retrain`), killswitch stuck (TRIPPED > 2 h → critical), dynamic-thresholds staleness (> 8 d → trigger `job_threshold_update`), WSL clock drift (> 5 s vs Redis time → `sudo -n hwclock -s` via `asyncio.to_thread`), evolution-loop idle (0 outcomes in 7 d → warning). Every check degrades to `warning` instead of raising; failed checks keep their real names in the report. New `job_health_check()` in `scheduler/autonomous.py` runs every 5 min and publishes `HealthReport.to_dict()` to the `aegis:core:health` stream (`{"body": json}` field per project convention, maxlen=288 ≈ 24 h). **Adaptations from spec:** (1) the killswitch value is a plain `"TRIPPED"`/`"ARMED"` string, not the JSON-with-`tripped_at` the spec assumed — the checker tracks first-observed trip time in `aegis:core:health:killswitch_tripped_since` (SET NX; cleared on arm); (2) spec's `SwarmAgentPool.get_instance()`/`get_all_agents()` don't exist — the quarantine ratio reads the persisted `aegis:swarm:agent_health` Redis hash (health == "DOWN") since no live pool exists in the scheduler process; (3) champion lives in `model_candidates` (`is_champion`, `test_auc`, `promoted_at`), not `model_manifest.status='champion'` with an `auc` column; (4) healing actions delegate to the existing autonomous jobs (which own their pools) instead of constructing `RetrainingPipeline` on the checker's soon-to-be-closed pool, and tasks are held in a module-level strong-ref set so the loop's weak refs can't GC an in-flight remediation; (5) spec condition 5 (thresholds stale) was listed but unimplemented in the spec code — implemented here against the real `aegis:core:thresholds` state (missing key = healthy "defaults in use"). |
| **5B** | ✅ Fixed | New `src/aegis/scrape/http_client.py`: `get_or_create_client(host, http2=, headers=, timeout=, follow_redirects=)` returns one pooled `httpx.AsyncClient` per (event loop, host, config) with keep-alive (20 conns/host, 10 keep-alive, 30 s expiry), 5/15/5/2 s timeouts, `_HTTP1_ONLY_HOSTS` default (reddit, news.google.com, localhost), `get_client()` non-closing context manager, and `close_all()` for shutdown. **Adaptations from spec:** (1) registry is **per event loop** (spec's module-global dict breaks under pytest's function-scoped loops — httpx transports are loop-bound) which also removes the need for the spec's loop-bound `asyncio.Lock`; (2) cache key includes a headers/timeout/redirect fingerprint so same-host adapters with different identities don't share header state; (3) closed clients are transparently replaced; (4) `http2=True` falls back to HTTP/1.1 when the optional `h2` package is absent. The 5 spec-mandated adapters migrated (`hacker_news`, `github_trending`, `google_news_rss` — spec's `google_news.py`, `reddit_rss`, `amazon` — host derived from `marketplace` TLD): `setup()` borrows the shared client, `teardown()` releases the reference without closing; all five kept their exact previous headers and explicit `http2=False` (none used HTTP/2 before — no protocol behaviour change). |

Pass 5 verification (2026-06-12): `ruff check` 0 violations (src/, tests/, aegis-phase4/src/, aegis-phase12/src/, aegis-phase13/src/; pre-existing failures in `scripts/validate_data_flow.py` + `aegis-phase13/tests/` predate this pass). Full main unit suite: **2320 passed, 2 skipped (infra), coverage 79.20%** (≥ 78% floor; new modules: health_checker 91.7%, http_client 93.7%). Phase 4 suite: 220 passed. 39 new tests: 13 in `tests/unit/scrape/test_http_client.py` (reuse/keying/close semantics, h2 fallback, 5-adapter integration), 26 in `tests/unit/scheduler/test_health_checker.py` (all 7 checks healthy/warning/critical paths, healing triggers, RLS set_config, report serialization, job wiring + never-raises).

### Remediation log — 2026-06-13 (GODMODE Pass 6 — Deep Research Output Engine)

Every output AEGIS produces is now the result of multi-pass investigation, not first-instinct analysis.

| ID | Status | What changed |
|----|--------|--------------|
| **6A** | ✅ Fixed | Second-pass verification of P0 results in `agents/runner.py`: after the graph completes, `run_trend()` attaches raw signal metadata to `GraphResult.trend_data` and calls `_deep_verify_high_confidence_result()`. For results with `final_score >= 0.85` it runs 3 independent checks — **temporal consistency** (velocity must be accelerating: `v1h >= v6h/6 AND v6h >= v24h/4`, not a spike-and-crash), **cross-source confirmation** (≥2 distinct platforms), **red-team review** (the RED_TEAM agent's decision must not be BLOCK). All pass → `deep_verified=True`; any fail → score reduced 0.10 (floor 0.70), priority downgraded to P1, failed check names recorded in `deep_verify_failures`. Never raises (each check has a benefit-of-doubt fallback) and never demotes below the actionable floor. `GraphResult` gained `deep_verified: bool`, `deep_verify_failures: list[str]`, `trend_data: dict`; provenance threaded into the phase2 stream payload (`_publish_phase2_result`). **Deviation from spec:** the spec used `result.correlation_id` for the trend log key and `result.final_priority = "P1"` (a string) — the real schema uses `result.trend_id` + the `Priority.P1_EXIT` enum, and reads velocities from `trend_data` (populated by the runner, since `GraphResult` doesn't carry the candidate). 14 tests in `tests/unit/agents/test_deep_verify.py` (langgraph-free — exercise the pure functions + orchestrator directly). |
| **6B** | ✅ Fixed | New `src/aegis/intelligence/research_engine.py`: `ResearchEngine.research(query, depth=)` runs the 5-pass methodology — Pass 1 broad harvest (routes via `AdapterRouter` + `TopicClassifier`, then `scrape_topic(adapter_override=…)`), Pass 2 cross-verification (theme words appearing on ≥2 platforms), Pass 3 temporal/volume analysis, Pass 4+5 (deep only) competitive landscape + Phase 8 compliance risk synthesis. Emits a structured `ResearchReport` (executive summary, key findings with per-finding verification, cross-verified themes, unverified claims, risks, opportunities, recommended actions, aggregate confidence). Depth tiers: `surface` (top-5 adapters, Pass 1), `standard` (top-12, Passes 1–3), `deep` (top-12, all 5). Signals normalized to plain dicts so the synthesis passes accept ProductSignal models, DB rows, and test fixtures alike. **Wired:** dashboard `POST /api/research/deep` (job-based, reuses the DASH-1 timeout/eviction machinery + the shared `/api/topic/research/{id}/stream` SSE endpoint) and `aegis research --topic … --depth … [--json-out]` CLI. **Deviation from spec:** spec's `result.composite_risk_score` is the real schema's `overall_risk_score`, and `ComplianceEngine().assess()` takes a `ComplianceRequest` object (not kwargs). 14 tests (`tests/unit/test_research_engine.py`) + 4 dashboard tests (`test_dash2_endpoints.py`). |

### Remediation log — 2026-06-13 (GODMODE Pass 7 — ADP-5 / ADP-7 / BRAIN-3 Wiring Completion)

This pass verifies and completes the three Pass 2/5 features end-to-end: MinHash dedup is confirmed live & benchmarked, the shared HTTP client is rolled out to the next tier of adapters, and the dynamic threshold is wired into its last consumer with an end-to-end propagation test.

| ID | Status | What changed |
|----|--------|--------------|
| **ADP-5** | ✅ Verified | Confirmed the MinHash + LSH layer is **actively called** (not merely defined) on the live path: `deduplicate_batch` → `deduplicate_batch_detailed` in `src/aegis/db/dedup.py` builds the `MinHashLSH` index and confirms candidates with the precise token-Jaccard/SequenceMatcher similarity (`layer=minhash` emitted at runtime; `datasketch` available). Benchmark re-run: **1000 signals deduped in 341 ms** (< 500 ms target). No code change — the Pass 2A implementation already satisfies the spec; this pass closes the verification requirement. |
| **ADP-7** | ✅ Completed | Migrated the **next 10 highest-traffic adapters** off the per-instance `httpx.AsyncClient` anti-pattern onto the shared pooled `get_or_create_client()` factory: `nse_bse`, `moneycontrol`, `devto`, `github_public`, `npm_trends`, `reddit_finance`, `reddit_ecommerce`, `youtube_rss`, `bing_news_rss`, `screener_in`. Each `setup()` now borrows the loop-scoped pooled client (host-keyed, prior headers + explicit `http2=False` preserved — no protocol change); each `teardown()` releases the reference instead of `aclose()`-ing it (shared lifecycle owned by `http_client.close_all()`). With the 5 adapters migrated in Pass 5B, **15 adapters** now reuse warm TCP/TLS connections — ending per-run TLS renegotiation churn for the busiest sources. |
| **BRAIN-3** | ✅ Completed | Closed the missing third consumer. `DynamicThresholds` was already wired into `analytics.py` (velocity slope, via injection) and `compliance/engine.py` (block threshold, awaited) but **not** the confidence gate's own module. Added a sync-safe injection point to `src/aegis/scrape/confidence.py` (`set_confidence_threshold()` / `get_confidence_threshold()`, mirroring `analytics.set_high_priority_slope()` — `score_batch` is CPU code run via `asyncio.to_thread`, §12, so it can't await); `score_batch(threshold=None)` now resolves to the adaptive gate. `topic.py` publishes the live gate to the module before the threaded scoring run. Added `TestBrain3ConsumerPropagation` (3 tests) proving the threshold **actually changes** through the `get_confidence_gate`/`get_velocity_slope`/`get_comply_block` read path after `update_from_outcomes()` with mock high-precision data, and propagates into both the confidence and analytics consumers. |

Pass 7 verification (2026-06-13): `ruff check src/ tests/` 0 violations. Full main unit suite: **2347 passed, 2 skipped (infra), coverage 79.37%** (≥ 78% floor). Scrape suite (424 tests incl. all 15 migrated adapters) green; `test_dynamic_thresholds.py` 17 passed (3 new). MinHash benchmark 341 ms < 500 ms.

| Severity | Meaning |
|----------|---------|
| 🔴 **P0 — Crash/Outage** | Will crash, hang, leak, or take a service down under normal use. |
| 🟠 **P1 — Broken/Dead** | Built but not wired/served/displayed — system not fully usable. |
| 🟡 **P2 — Degrade/Trap** | Silent degradation, misleading output, onboarding trap, rot. |
| 🔵 **P3 — Strategic** | Architecture-level work to make the system dynamic & self-driving. |

---

## 0. Current health snapshot

| Signal | Result | Note |
|--------|--------|------|
| Unit tests | **2052 passed, 2 skipped, 0 failed** | Tested+wired code is healthy. |
| Coverage gate | **FAIL — 77.37% < 78% floor** | Caused by untested dead code (`ORPH-3`). |
| Test collection | **2054 collected, 0 import errors** | Codebase is importable/wireable. |
| `ruff check src/ tests/` | **4 violations** | Claimed "0"; guardrail drifted (`LINT-1`). |
| `.env` in git | **Not tracked** ✅ | No secret leak in history. |
| DB migrations | **0001–0011 sequential** ✅ | No numbering gap. |
| Optional imports (torch/optuna/chromadb/spacy) | **All guarded** ✅ | No bare-import crash surface. |

### Verified SAFE — do **not** spend fix-time here
- Adapter HTTP timeouts: all set via `httpx.Timeout(...)`. No hang risk.
- Runner wall-clock timeout: `asyncio.wait_for(..., 120s)` → `halt="timeout"` ([runner.py:213](src/aegis/agents/runner.py#L213)).
- Graph nodes catch their own exceptions; one node failing does not abort traversal.
- SSE↔Redis timeout: `socket_timeout=5.0` > `block=1500ms` ([app.py:99](src/aegis/dashboard/app.py#L99)).
- Dashboard hardcoded latency (52.5s) already replaced with real percentiles.

---

## 1. 🟠 P1 — Orphaned subsystems (built, never connected)

**Biggest reason the system "feels weak / underused": entire phases exist as code but nothing runs, serves, or displays them.**

| ID | Finding | Evidence | Fix technique | Fixtures to create |
|----|---------|----------|---------------|--------------------|
| **ORPH-1** | `/geo/*`, `/compliance/*`, `/evolve/*` REST routers are **never mounted** in any served app. Only execute-api mounts routers. | `geo/api.py`, `compliance/api.py`, `evolve/api.py` exist but no `include_router` references them. Only [execute/api/app.py:191-196](aegis-phase4/src/aegis/execute/api/app.py#L191). | Create **one unified FastAPI app** `src/aegis/api/main.py` that `include_router`s every phase behind a prefix (`/geo`, `/compliance`, `/evolve`, `/capital`, `/datalake`, `/dr`). Run as a new compose service `aegis-api:8400`. | `src/aegis/api/main.py`; compose `aegis-api` service; `tests/unit/api/test_mounting.py` asserting every router is reachable. |
| **ORPH-2** | `scheduler/autonomous.py` (the "autonomous" loop) is **not wired** to any CLI/service. Reads the stream but nothing starts it. | `grep autonomous src/aegis/cli/main.py` → docstring only. [autonomous.py:121](src/aegis/scheduler/autonomous.py#L121). | Add `aegis autonomous run` Click command + a `aegis-autonomous` compose service (long-running). Loop: drift-check → conditional retrain → datalake refresh → adaptive scrape. | CLI command; compose service; `tests/unit/scheduler/test_autonomous.py`. |
| **ORPH-3** | Untracked, **untested, unreferenced** modules drag coverage below floor: `scrape/realtime_consumer.py`, `llm/council.py`, `predict/drift_monitor.py`, `scrape/anomaly_detector.py`, `predict/causal/explainer.py`. | All `?? ` in git; `grep council src/aegis/agents/*` → empty. Coverage 77.37% < 78%. | **Decide per file:** wire in + add tests, OR delete. `council` → call from `runner` behind `AEGIS_COUNCIL_ENABLED`; `drift_monitor`/`anomaly_detector` → into the analyze pipeline. | Tests for each kept module; deletion PR for abandoned ones. |
| **ORPH-4** | Datalake Bronze→Silver→Gold runs only on manual `aegis datalake daily`. Prefect `daily-lake-refresh` flow exists but isn't scheduled. | `orchestration/flows.py` (no deployment). | Register Prefect deployment with cron, or call from `ORPH-2` autonomous loop. | Prefect deployment YAML / scheduler hook. |

---

## 2. 🟠 P1 — Structural cruft (the "unclean structure")

| ID | Finding | Evidence | Fix technique | Fixtures |
|----|---------|----------|---------------|----------|
| **STRUCT-1** | **6 duplicate standalone phase folders** sit beside the integrated `src/aegis/` code; none are workspace members. ~3 MB dead, divergent copies. | `members = ["aegis-phase4","aegis-harden","aegis-phase12","aegis-phase13"]`; folders: `aegis-phase6/` (540K), `aegis-phase7/` (308K), `aegis-pulse-phase8/` (356K), `aegis-pulse-phase9-0.9.0/` (320K), `aegis-phase11/` (1.1M). | `diff` each against its `src/aegis/` counterpart to confirm no unique code, then move to `archive/` or delete. | `archive/` move or deletion; updated `.gitignore`. |
| **STRUCT-2** | Directory literally named **`aegis-phase 14`** (with a space) — breaks unquoted globs and tooling. | `ls -d "aegis-phase 14"` (412K). | Remove (its code is in `src/aegis/observability/`). | deletion. |
| **STRUCT-3** | Build/test artifacts floating in tree: `node_modules/`, `htmlcov/`, `.hypothesis/`, `coverage.xml`. | top-level `ls`. | Add all to `.gitignore`; remove from tree. | `.gitignore` entries. |
| **STRUCT-4** | **Doc drift:** CLAUDE.md "Package layout" lists migrations only to `0004`; actual = `0001–0011`. | `db/migrations/`. | Regenerate the layout/migration list from disk; add a doc-lint check. | doc fix + optional CI check. |

---

## 3. 🟡 P2 — Environment files stale/incomplete

| ID | Finding | Evidence | Fix technique | Fixtures |
|----|---------|----------|---------------|----------|
| **ENV-1** | `.env.example` is missing **16+ keys** the running system requires. | Confirmed missing: `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `AEGIS_OLLAMA_BASE_URL`, `AEGIS_DASHBOARD_OPS_TOKEN`, `AEGIS_COUNCIL_ENABLED`, `LANGFUSE_*`, `AEGIS_REDDIT_CLIENT_ID/SECRET`, `AEGIS_YOUTUBE_API_KEY`, `AEGIS_COMPLY_BLOCK_THRESHOLD`, `AEGIS_EVOLVE_RETRAIN_DAY`, `AEGIS_GEO_SHIPENGINE_KEY`, `AEGIS_SEC_VAULT_TOKEN`, `AEGIS_BACKUP_RESTIC_PASSWORD`, `AEGIS_DR_DRILL_PG_DSN`. | **Generate `.env.example` from the pydantic-settings models** (`Settings`, `LLMSettings`, `ComplianceSettings`, `EvolveSettings`, `SecurityConfig`, `BackupSettings`, `DisasterRecoverySettings`, `ExecuteSettings`) so it can never drift again. | `scripts/gen_env_example.py` (introspects Settings) + CI check that `.env.example` is in sync. |
| **ENV-2** | `AEGIS_DASHBOARD_OPS_TOKEN` defaults empty in compose ([line 307](docker-compose.yml#L307)) → ops console (shell-executing) may be **unauthenticated**. | compose default `:-`. | Make token **required** (fail-fast if empty when ops console enabled). | settings validator. |
| **ENV-3** | `.envrc` sets `AEGIS_DISABLE_OLLAMA=0` but ships no LLM keys → on a host without Ollama, every LLM call silently degrades. | `.envrc`. | Add a startup probe that logs `WARNING: no LLM backend reachable — heuristic-only mode`. Ties to `HALLU-1`. | startup health check. |

---

## 4. 🟠 P1 / 🟡 P2 — Infrastructure & Docker

| ID | Sev | Finding | Evidence | Fix technique |
|----|-----|---------|----------|---------------|
| **INFRA-1** | 🟡 | Dashboard-in-Docker contradicts the established **host-only** decision; still defined with `restart: unless-stopped` + `docker.sock` mount. | [docker-compose.yml:289](docker-compose.yml#L289), socket mount [line 309](docker-compose.yml#L309); memory `feedback_dashboard_host_mode`. | Remove the dashboard service (or hard-gate behind a clearly-labeled profile) and document `uv run aegis dashboard serve` as the only supported path. |
| **INFRA-2** | 🟡 | `docker.sock:ro` mounted on a service that also runs the **shell-executing ops console** = privilege-escalation surface. | compose. | Drop the socket mount; use the CLI/API for container status instead of in-container docker. |
| **INFRA-3** | 🟡 | Only **7 `deploy:` blocks for 17 services**; loki/promtail/prefect/langfuse likely uncapped → OOM risk on WSL2. | `grep -c 'deploy:'`. | Add `deploy.resources.limits` to every stateful service. |
| **INFRA-4** | 🟡 | `docker compose exec/logs` require **service names** (`postgres`), not container names (`aegis-postgres`) — silent "service not running" trap. | CLAUDE.md gotcha. | Document in `SETUP_GUIDE.md` command reference. |
| **INFRA-5** | 🟡 | `predict` host:container port `8100:8000`; docs call it ":8100" causing confusion (not a bug). | [compose:192](docker-compose.yml#L192). | Clarify in docs. |

---

## 5. 🔴 P0 / 🟠 P1 — Dashboard (frontend + backend)

| ID | Sev | Finding | Evidence | Fix technique | Fixtures |
|----|-----|---------|----------|---------------|----------|
| **DASH-1** | 🔴 | **Topic-research jobs have no per-job timeout AND eviction is blind.** `_cleanup_research_jobs` only deletes `done`/`error` jobs. If >20 jobs are stuck `running`, **none are evicted** → unbounded dict + leaked asyncio tasks + held DB/Redis conns → pool exhaustion → dashboard hang. **Most likely "dashboard stops responding" cause.** | [app.py:1283-1287](src/aegis/dashboard/app.py#L1283); no `wait_for` around the task. | Wrap each research task in `asyncio.wait_for(timeout=N)`; on timeout mark `error`. Evict by age (oldest-first) regardless of status. Cancel task on client disconnect. | per-job timeout + LRU/age eviction + disconnect handler; test. |
| **DASH-2** | 🟠 | Dashboard surfaces **none** of phases 6/7/8/9/12/15. Geo/compliance/evolve/capital invisible. | `grep -ci 'geo\|compliance\|evolve\|capital'` → 2 in HTML, 1 in app.py. | After `ORPH-1`, add panels: **Geo Arbitrage board**, **Compliance gate feed**, **Evolve panel** (drift gauge + champion AUC + last retrain), **Capital ledger** (open plans + daily PnL). | new endpoints + frontend cards. |
| **DASH-3** | 🟡 | **~40 backend endpoints, ~21 used by frontend.** ~19 dead/unused (`/api/signals/stats`, `/api/agents/recent`, `/api/swarm/history`, `/api/swarm/agents`, `/api/platforms/*`, `/api/search`, `/api/predictions/recent`, `/api/execute/alerts`, `/api/datalake/status`). | grep `api(`/`fetch(` vs route decorators. | Either wire into UI or remove. Untested endpoints rot + widen attack surface. | UI wiring or deletion + tests. |
| **DASH-4** | 🟡 | pg pool `max_size=10` + a **second** `_research_pool` (PgPool). Under concurrent tabs + research + SSE catch-up + `/api/stats` aggregation, 10 conns is thin; pool-acquire has no timeout → apparent hang. | [app.py:164](src/aegis/dashboard/app.py#L164), `_research_pool` [app.py:65](src/aegis/dashboard/app.py#L65). | Raise/instrument pool; add acquire timeout; consider sharing one pool. | pool config + metric. |
| **DASH-5** | 🟡 | **RLS tenant trap:** only 3 `current_tenant`/`set_config` calls in app.py despite many tenant-scoped `signals`/`alerts` queries. If `SET app.current_tenant` is missing before a query, RLS returns **0 rows** → dashboard silently shows empty. | `grep -c current_tenant` = 3. | Verify every tenant-scoped query sets the tenant (a connection-acquire wrapper that always `SET`s is safest). | pool-acquire wrapper + test that an unset tenant raises, not silently empties. |
| **LINT-1** | 🟡 | `subprocess.run(["kill", pid])` no `check=`, raw regex PID. | [dashboard/cli.py:39](src/aegis/dashboard/cli.py#L39) (ruff PLW1510). | Add `check=False`, validate PID, log failures. | — |

---

## 6. 🟡 P2 / 🔵 P3 — Adapters (scrape power, efficiency, accuracy)

| ID | Sev | Finding | Evidence | Fix technique / algorithm | Fixtures |
|----|-----|---------|----------|---------------------------|----------|
| **ADP-1** | 🟡 | **3 permanently-broken adapters still shipped** and burn a swarm slot + cooldown bookkeeping every run: `tiktok` (TikTok Ads auth), `pinterest` (403), `nitter` (instances dead). They `raise_for_status()`/`raise` rather than degrade. | `sources/{tiktok,pinterest,nitter}.py`. | Quarantine behind `--include-experimental`; exclude from default waves. | flag + registry change. |
| **ADP-2** | 🟡 | **Key-gated adapters fail silently to empty**: `reddit`, `youtube` need keys not in `.env.example` → look "working", always return nothing. | needs `AEGIS_REDDIT_CLIENT_ID`, `AEGIS_YOUTUBE_API_KEY`. | Emit explicit `status="needs_credentials"` instead of empty success (ties to `HALLU-4`). | adapter status enum. |
| **ADP-3** | 🔵 | No **conditional GETs** — RSS feeds re-fetched in full every poll. | adapters lack ETag/Last-Modified handling. | Store `ETag`/`Last-Modified` per feed; send `If-None-Match`/`If-Modified-Since`; skip 304s. Big bandwidth/latency win. | per-source HTTP cache store. |
| **ADP-4** | 🔵 | **Fixed 4-wave scraping** — no adaptivity; dead sources cost as much as hot ones. | `swarm.py` wave logic. | **UCB1 multi-armed bandit**: allocate per-run scrape budget by recent yield × velocity × novelty. Decays dead sources, intensifies surging ones. Reuses `AgentHealth`. | bandit allocator + state. |
| **ADP-5** | 🔵 | Dedup is token+sequence (`difflib`) — O(n²)-ish, weak at swarm scale. | `dedup.py`. | **MinHash + LSH** for O(n) near-dup; **embedding cosine** (`bge-m3` via Ollama, already available) for semantic clustering. Far higher precision. | MinHash index + embedding clusterer. |
| **ADP-6** | 🔵 | `schema_guard` fingerprints responses but **only logs** — no action on drift. | `schema_guard.py`. | Auto-quarantine an adapter whose response fingerprint drifts beyond threshold; raise alert. Self-healing. | drift threshold + quarantine hook. |
| **ADP-7** | 🟡 | Per-adapter `httpx.AsyncClient` instances; no shared keep-alive pool. | each adapter builds its own client. | One shared client per host with HTTP/2 + keep-alive; reduces TLS handshakes. | shared client factory. |

---

## 7. 🟠 P1 / 🔵 P3 — Phase connectivity & coverage

**What IS connected (verified):** `runner` → `XADD aegis:phase2:graph_results {"body":...}` (capped `maxlen=10_000`, [runner.py:390](src/aegis/agents/runner.py#L390)) → read correctly by Phase 4 IntakeWorker, dashboard ([app.py:455](src/aegis/dashboard/app.py#L455)), datalake bronze. Phase 2↔3 and 3↔4 bridges exist & import.

| ID | Sev | Gap | Fix technique | Fixtures |
|----|-----|-----|---------------|----------|
| **CONN-1** | 🟠 | **No unified event bus.** Geo/compliance/evolve produce results that flow nowhere. | Publish `aegis:phase7/8/9:*` streams mirroring the phase2 pattern → dashboard + datalake consume. | stream publishers + consumers. |
| **CONN-2** | 🟠 | **Compliance gate not in the live alert path.** `gate_execution_plan()` exists for Phase 6 but Phase 4 intake→compose doesn't call compliance before emitting ENTER alerts. | Add compliance + geo enrichment as Phase 4 pipeline stages. | pipeline stage + test. |
| **CONN-3** | 🟡 | Predict is HTTP (:8100) **and** called in-process by the agent bridge — two paths to one model. | Pick one (in-process for latency, HTTP for dashboard); document. | consolidation. |
| **CONN-4** | 🟡 | Dashboard + datalake use `xrevrange` **snapshots**, not consumer groups → entries can be missed between runs; no backpressure. | Switch datalake to `XREADGROUP` + ACK. | consumer-group migration. |

---

## 8. 🟡 P2 — Silent degradation / "hallucination" (looks authoritative, is a fallback)

This is the root of the "system hallucinates / doesn't give a real response" feeling: it answers confidently while silently in fallback mode.

| ID | Finding | Evidence | Fix technique | Fixtures |
|----|---------|----------|---------------|----------|
| **HALLU-1** | **LLM fallback is invisible.** Ollama down + no keys → every agent node silently degrades to heuristic text; verdict still renders confidently. No "heuristic-only" badge. | `agents_bridge` `except Exception` → fallback ([agents_bridge.py:79,175](src/aegis/llm/bridge/agents_bridge.py#L79)). | Propagate `reasoning_source: llm\|heuristic` + `llm_used: bool` through `GraphResult` → stream → dashboard badge. | schema field + UI badge. |
| **HALLU-2** | **Compliance/FDA/OFAC fall back to static lists** when keys absent — a "clear" may mean "not actually checked." | `compliance/*` optional keys. | Add `data_source: live\|static` to each `RiskBreakdown`; surface it. | schema field. |
| **HALLU-3** | **Geo demand uses synthetic fallback** (`market_size_score × 0.6`) when signals DB empty — identical-looking to real-velocity scores. | `geo/demand.py`. | Tag `demand_source: db\|synthetic` on `GeoOpportunity`. | schema field. |
| **HALLU-4** | **Key-gated adapters return empty silently** — "success, 0 signals" indistinguishable from "genuinely nothing." | `reddit.py`, `youtube.py`. | Distinct `needs_credentials` status (ties `ADP-2`). | status enum. |

**Unifying fix (foundation of "own brain"):** every result envelope carries **provenance + confidence-source metadata** (`live` vs `fallback`, `llm` vs `heuristic`, `db` vs `synthetic`). A system can't reason about its own reliability if it doesn't record it.

---

## 9. 🔵 P3 — Make it dynamic / self-driving ("its own brain")

Today the system is **static & reactive**: fixed waves, fixed thresholds, manual triggers, no runtime feedback loop.

| ID | Lever | Technique | Payoff |
|----|-------|-----------|--------|
| **BRAIN-1** | **Close the Phase 9 loop at runtime** | Wire `OutcomeRecorder`→`DriftDetector`→retrain→champion-promotion→`OnlinePricingPolicy` into the autonomous loop (`ORPH-2`). | System retrains & adapts without you. |
| **BRAIN-2** | **Adaptive scraping** | UCB1 bandit budget allocation (`ADP-4`). | Coverage concentrates where signal is. |
| **BRAIN-3** | **Dynamic thresholds** | Confidence gate (0.85), velocity slope (2.0), compliance block (0.70) currently static env constants → adapt from realized outcome accuracy. | Fewer false positives over time. |
| **BRAIN-4** | **Feedback-weighted agent ensemble** | Track each agent node's historical accuracy vs settled outcomes; weight supervisor verdict by per-agent reliability instead of fixed aggregation. | The literal "brain that learns which faculties to trust." |
| **BRAIN-5** | **Event-driven not polling** | `XREADGROUP` consumer groups (`CONN-4`). | Push-based, nothing missed. |
| **BRAIN-6** | **Self-healing adapters** | Auto-quarantine on schema drift (`ADP-6`). | Survives external API changes. |

---

## 10. What the system expects FROM YOU (setup demands)

| Capability | Needs from you | Without it |
|------------|----------------|------------|
| Default scrape | Nothing (reddit-rss, hacker-news, github-trending, amazon, google-news, bing-news work keyless) | — |
| `google-trends` | `pytrends` extra | adapter skipped |
| `reddit` API | `AEGIS_REDDIT_CLIENT_ID` + `_SECRET` | silently empty |
| `youtube` | `AEGIS_YOUTUBE_API_KEY` | silently empty |
| `instagram` | session cookie + `allow_red_tos=True` (high ban risk) | skipped |
| Flipkart/Myntra/Meesho | **FlareSolverr running** (`docker compose up -d flaresolverr`) | 403/empty |
| LLM reasoning | Ollama running **or** `GROQ_API_KEY`/`OPENROUTER_API_KEY`/`GEMINI_API_KEY` | heuristic-only (silent — see `HALLU-1`) |
| Council (multi-model) | `AEGIS_COUNCIL_ENABLED=1` + ≥2 models reachable | single-model |
| Compliance live | optional `AEGIS_COMPLY_FDA_API_KEY`, `_TRADE_GOV_API_KEY` | static lists |
| Geo live shipping | optional `AEGIS_GEO_SHIPENGINE_KEY` | static matrix |
| Security (Phase 12) | Vault + `AEGIS_SEC_VAULT_TOKEN`, `AEGIS_SEC_HMAC_KEY` | Fernet dev fallback |
| Backups (Phase 15) | `pgbackrest` binary + `AEGIS_BACKUP_RESTIC_PASSWORD` | backup calls error |
| DR drills | `aegis_drill` DB created manually + `AEGIS_DR_DRILL_PG_DSN` | drill fails |
| Dashboard | run **on host**: `uv run aegis dashboard serve`; set `AEGIS_DASHBOARD_OPS_TOKEN` | ops console unauthenticated |
| Postgres | migrations 0001–0011 applied | tables missing |

---

## 11. Docker command reference

```bash
# Bring up infra (NOT the dashboard — run that on host)
docker compose up -d postgres redis minio flaresolverr predict execute-api execute-drain prometheus grafana jaeger loki promtail ollama

# One-shot model pull (re-run if models missing)
docker compose run --rm ollama-init

# Dashboard — HOST process, not Docker (see INFRA-1)
uv run aegis dashboard serve

# Logs / exec — use SERVICE names, not container names (see INFRA-4)
docker compose logs -f predict
docker compose exec postgres psql -U aegis_app -d aegis

# Optional profiles
docker compose --profile llm-proxy up -d litellm

# Enterprise hardening overlay
docker compose -f docker-compose.yml -f docker-compose.enterprise.yml up -d

# Full teardown (WIPES data)
docker compose down --volumes
```

---

## 12. Prioritized fix queue (impact ÷ effort)

| Order | ID(s) | Why first |
|-------|-------|-----------|
| 1 | **DASH-1** | Concrete crash/leak vector — the dashboard-hang you're hitting. |
| 2 | **ORPH-1, ORPH-2** | Unmount→mount unified API + start autonomous loop = unlocks half the system. |
| 3 | **DASH-2, CONN-1, CONN-2** | Make geo/compliance/evolve visible + gate alerts. |
| 4 | **ENV-1** | Generated `.env.example` fixes onboarding permanently. |
| 5 | **HALLU-1..4** | Provenance metadata — stops the "hallucination" feeling; foundation for BRAIN-4. |
| 6 | **STRUCT-1..3, ORPH-3** | Clean tree + restore coverage floor above 78%. |
| 7 | **ADP-3..6** | Scrape efficiency, coverage, precision. |
| 8 | **BRAIN-1..6** | Strategic: dynamic, self-driving system. |
| 9 | **INFRA-1..3, DASH-3..5, LINT-1** | Harden infra, prune dead surface, fix RLS trap. |

---

*End of audit. No source files were modified. IDs are stable — reference them when filing fixes.*
