# AEGIS Pulse — Development Sessions

Chronological log of major development sessions. Each entry captures what was built, what was learned, and what bugs were fixed.

---

## Phase 0–1 Foundation

### Session — Reddit API Gotcha & Adapter Fixes
**Key fixes**: `cli/main.py`, `db/signals.py`, `amazon.py`, `instagram.py`, `github_trending.py`, `nitter.py`  
**Discovery**: Reddit public JSON API blocks requests with Brotli (`Accept-Encoding: br`). Fix: gzip-only + `http2=False`.  
**Status after**: reddit-rss, hacker-news, github-trending, amazon working; tiktok/pinterest/nitter broken (external API changes).

### Session — No-API-Key Adapters
Added google-news, bing-news (free RSS, support `--query`), google-trends (pytrends, 0.2 req/s rate limit).  
**Status after**: 7 working adapters, all no API key required.

---

## Phase 2 — Multi-Agent Intelligence

### Session — Phase 2 Integration (≈ 2026-05-05)
Merged 10-node LangGraph DAG from `aegis-phase2/` into `src/aegis/agents/`. Deleted `aegis-phase2/` workspace member.  
466 tests passing. Established structlog as logging standard across all agent nodes.

### Session — Phase 2 Sign-off (2026-05-08)
All phases 0–2 green. 467 tests, 79.54% coverage. 754 signals in DB. Ready for Phase 3.

---

## Phase 3 — Predictive Apex

### Session — Phase 3 Integration (2026-05-12 → 2026-05-15)
Built `src/aegis/predict/` — heuristic-first ML core with optional neural augmentation.  
**Architecture decisions made**:
- Neural models can only reduce confidence [0.5–1.0], never flip a verdict (zero-API-key guarantee)
- `predict.resilience` functional API vs `core.resilience` decorator API — coexist intentionally
- SCOUT uses `p_breakout` at 24h horizon; SENTINEL uses `p_decline` at 6h

663 tests pass. 4/4 integration tests pass. 0 ruff violations.

### Session — Code Quality Pass (2026-05-13)
192+ ruff violations fixed across codebase. Actual bugs found and fixed alongside style issues.  
653 tests green post-cleanup.

---

## Phase 4 — Execution & Alert System

### Session — Phase 4 Build & Sign-off (2026-05-16)
Built `aegis-phase4/` as uv workspace member. 7-stage alert pipeline, SSE EventBus, killswitch, outbox drainer.  
**Key decisions**:
- `MERGE_WINDOW_S = 30.0` for Phase 2 + Phase 3 merge
- Verdict mapping: `proceed→ENTER`, `hold→HOLD`, `block→BLOCK`, `escalate→HOLD`
- `AEGIS_EXECUTE_MODE=advisory` default

663 (main) + 161 (Phase 4) tests pass. 0 ruff violations. Full workspace + stream integration complete.

---

## Dashboard — Command Center

### Session — Dashboard Build (2026-05-17)
Built `src/aegis/dashboard/` — dark-theme SPA on :8300 aggregating all phases.  
Features: SSE live feed, streaming ops console (runs CLI commands, streams output), ChartJS charts, system health, signal stats.  
Security: ops console first-token allowlist `{"aegis", "uv", "python", "docker"}`.

---

## Phase 5 — Autonomous Scale

### Session — Phase 5 Analytics + Confidence Gate (2026-05-18)
Added `analytics.py` (OLS velocity slope + PCA denoising), `confidence.py` (advisory 0.85 gate).  
`PatternCluster` enriched with `is_high_priority`. Stream payload gains `data_confidence: float`.

### Session — Bug Fixes & Audit (2026-05-18)
Fixed `body`/`payload` stream key bug (Phase 4 `IntakeWorker` was reading wrong field).  
Enriched Phase 2 runner stream payload with `decisions`, `raw_verdict`, `started_at`, `finished_at`, `duration_ms`.  
Added google-news + bing-news to CLI `aegis scrape --source`. 775 tests pass.

---

## Phase 5 — Swarm Intelligence Layer

### Session — Indian E-Commerce Adapters (2026-05-19, AM)
Implemented 8 high-risk adapters: Flipkart, Myntra, Amazon.in, Meesho, Ajio, Nykaa, Snapdeal, IndiaMart.  
FlareSolverr bypass integrated. Shared UA rotation. Test coverage added.

### Session — SwarmOrchestrator v1 (2026-05-19, PM)
Built `SwarmOrchestrator`, `SwarmResult` schema, topic intelligence router, `aegis swarm` + `aegis topic` CLI commands.  
1051 tests pass. 78.00% coverage floor met.

### Session — Phase 6 Agent Integration + Dashboard Swarm Tab (2026-05-19, late PM)
Injected `SwarmResult` into Phase 2 agent pipeline via `GraphState.swarm_context`.  
Scout LLM prompt now includes swarm market context.  
Dashboard gains Swarm Intelligence tab with 5 new API endpoints.  
1062 tests pass. 78.31% coverage.

### Session — CLAUDE.md Phase 7 Documentation (2026-05-20, AM)
Appended Swarm Intelligence Layer section to CLAUDE.md covering architecture, adapter registry, Redis keys, CLI commands, and common gotchas.

---

## Stabilisation & Integration Audit

### Session — Autonomous Audit (2026-05-20, 10:06 AM)
Full ruff pass: 25 violations → 0. Suppressed 2 S608 false-positives in `alembic/env.py`.  
**Integration work done**:
- Phase 2–4 integration bridge wired into agent runner
- Settings expanded with 30+ new data source adapter config fields
- Platform + SourceTier enums expanded to 30+ adapters
- Unified content hash computation (prevents digest divergence)
- Swarm context added to `GraphState`
- Agent nodes upgraded to structlog
- Scout prompt updated to render swarm market context
- CLI expanded with 30+ adapter registry and end-to-end workflows
- `docker-compose.yml` gains Phase 4 Execute API + Drain services
- `Dockerfile.predict` refactored to use pyproject.toml dependency groups
- Phase 4 integrated into monorepo via uv workspace + namespace path extension

1062 tests pass. 78.31% coverage.

### Session — Runtime Bug Fixes (2026-05-20, 1:44 PM)
**Bug 1**: `AttributeError` in `swarm_agents.py` — `Settings.swarm_wave_timeout_s` accessed at root level; should be `settings.scrape.*`. Fixed with `getattr(settings.scrape, "swarm_wave_timeout_s", default)`.  
**Bug 2**: `ProductSignal.scraped_at` nested in `ScrapeProvenance`; after `model_dump(mode="json")` it's absent at top level → Wave 4 signals silently dropped by schema validation. Fixed by promoting `scraped_at` to signal root level with `datetime.now(UTC)` fallback.  
**Result**: End-to-end swarm execution fully operational — 60 total signals across 20 platforms confirmed.

---

## Phase 4 Final Validation (2026-05-21, 12:15 AM)

**Session S35** — Final testing and validation of SwarmOrchestrator.

**Investigated**: Full unit test suite + SwarmOrchestrator dry-run with 4 waves and 35 adapters.

**Confirmed bugs (already fixed in prior session)**:
- Swarm config fields nested under `settings.scrape.*` not root `Settings`
- `scraped_at` unavailable at signal root after `model_dump(mode="json")`

**Completed**:
- All ruff violations: 25 → 0 (unsorted imports, unused imports, UTF-8 encode calls, whitespace)
- S608 false-positives suppressed in alembic
- Scout swarm_context forwarding to `prompts.render()` fixed
- Safe `getattr()` pattern standardised across `swarm_agents.py` and `swarm.py`
- `scraped_at` promoted to signal root with UTC fallback

**Final state**: 1062 tests pass at 78.31% coverage. End-to-end live execution: 60 signals across 20 platforms across 4 waves.

---

## Environment Setup Session (2026-05-21, 12:10 PM)

**Goal**: Set up one-command parallel swarm launcher for dev environment.

**Installed / configured**:
- Gemini CLI 0.42.0 (already present at `~/.npm-global/bin/gemini`); config written to `~/.config/gemini/config.json`
- `antigravity` Python package installed
- `google-generativeai` SDK installed (note: now deprecated in favour of `google.genai`)
- `~/bin/aegis-swarm` launcher created — one-command entry point for all AEGIS operations
- `~/bin/aegis-aliases.sh` created with `aegis`, `fix-tests`, `phase10`, etc.
- `~/.bashrc` updated with `PATH="$HOME/bin:$PATH"` and `source ~/bin/aegis-aliases.sh`
- `.envrc` written to `~/code/aegis-pulse/`
- All 11 Docker services confirmed healthy (postgres, redis, minio, flaresolverr, predict, execute-api, execute-drain, dashboard, prometheus, grafana, jaeger)

**Bugs found and fixed during audit**:
- **19 failing tests** (previously counted as 1062 after a double-venv PATH issue); root causes in `test_swarm.py`:
  1. `SwarmOrchestrator.__init__` always constructed `ConcurrencyGovernor` even when `pool` was provided — `asyncio.Semaphore(MagicMock())` TypeError. Fixed: skip governor when pool is given.
  2. `_make_settings()` fixture set attrs flat (`s.swarm_wave_timeout_s = 10`) but production code reads `settings.scrape.swarm_wave_timeout_s` — MagicMock child returned truthy values for timeout, breaking `asyncio.wait_for` and `publish_redis` gate. Fixed: mirror attrs on `s.scrape.*`.
  3. `test_make_adapter_fn_success_model_dump` exact-equality assertion failed because `swarm.py` now promotes `scraped_at` into all model-dump results. Fixed: check individual fields + presence of `scraped_at`.
  4. `test_swarm_persistence_save_with_pool_calls_execute` used `MagicMock()` as async context manager — Python skips instance `__aenter__` for dunder resolution. Fixed: use `AsyncMock()`.
  5. `test_swarm_persistence_publish_redis_sends` set `settings.swarm_publish_redis = True` (flat) but not on `settings.scrape`. Fixed: set both.
- **Result**: 0 failures in `test_swarm.py` + `test_topic_and_patterns.py` (164 tests, all pass)
- **Secondary fix**: `tests/unit/scrape/test_phase0_foundation.py::TestSwarmAgentPool` — same settings nesting issue (4 tests). Added `settings.scrape.swarm_wave_timeout_s` mirror to each test. All 10 `TestSwarmAgentPool` tests now pass.

**One-line usage after restart**:
```bash
aegis status          # docker status + tests + ruff
aegis up              # start all 11 services
aegis fix all tests   # launch Claude Code swarm agent
fix-tests             # alias for above
```

---

## Current Status (2026-05-21)

| Phase | Tests | Coverage | Notes |
|-------|-------|---------|-------|
| 0–5 unit | 1062 pass | ≥78% | 23 failures fixed this session (all in test fixtures) |
| Phase 4 unit | 161 pass | standalone | |
| Phase 3 integration | 4/4 pass | — | requires live DB |
| Ruff violations | 0 | — | all phases |
| Live swarm e2e | 60 signals / 20 platforms / 4 waves | — | confirmed 2026-05-20 |
| Dev environment | ~/bin/aegis-swarm launcher | — | aliases in .bashrc |
