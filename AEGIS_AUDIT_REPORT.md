# AEGIS PULSE — FULL-SPECTRUM AUDIT REPORT

- **Audit start:** 2026-07-02
- **Branch:** `audit/full-system-20260702` (cut from `audit-remediation-2026-06` @ c22bd12)
- **Auditor:** Claude (principal-engineer-level directive v1.0)
- **Scope so far:** Phase 0 (ground truth) + Phase 1 (stability & bugs) + Phase 2 (security). Phases 3–11 pending.
- **Artifacts:** full tool logs in `.audit/20260702/` (ruff_full.log, bandit_full.log, mypy_strict.log, pytest_main.log, detect_secrets.json, pip_audit.json)
- **Working-tree note:** the 242 uncommitted files present at audit start were committed 2026-07-02 (a102826) at user request after a secrets screen — see P0-7.

Severity scale: **Critical** (data loss / security exposure / confident-wrong verdict) · **High** (core path breaks or silently wrong) · **Medium** (degraded but functional) · **Low** (cosmetic).

---

## PHASE 0 — GROUND TRUTH & INVENTORY

Full corrected map: see `ARCHITECTURE_ACTUAL.md` (written this session). Key verified facts:

| Item | Actual value | How verified |
|---|---|---|
| Python LOC (all source trees) | 116,454 | `find … -name '*.py' \| xargs wc -l` |
| Adapter files | 51 in `src/aegis/scrape/sources/` | `ls \| wc -l` |
| Swarm-registered adapters | 37 (`src/aegis/scrape/swarm.py:72`) | imported `_REGISTRY` at runtime |
| CLI-registered adapters | 10 (`src/aegis/cli/main.py:388`) | grep |
| SQL migrations | 27 (0001 → 0027) | `ls db/migrations` |
| Compose services | 19 (incl. `autonomous`, `langfuse` — both absent from CLAUDE.md) | grep compose |
| Containers up at audit time | 17, all healthy | `docker ps` |
| Root deps | 37 direct: 28 pinned `==`, 9 bounded-floating; `uv.lock` present | tomllib parse |
| Port exposure | every published port binds 127.0.0.1 only | grep compose |
| CI | **one workflow only** (`integration.yml`) | `ls .github/workflows` |
| Python | 3.12.7; `requires-python = ">=3.12,<3.13"` | `uv run python --version` |

### Phase 0 findings

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P0-1 | **High** | CI/CD | No unit-test / lint CI lane exists. `integration.yml`'s own header references "the default `ci` workflow" for unit tests + ruff — that workflow does not exist. Nothing runs the 2400-test suite or ruff on push/PR. No pre-commit config either. All "quality gates" are manual. | `ls .github/workflows/` → `integration.yml` only |
| P0-2 | Medium | Docs | CLAUDE.md architecture is stale: omits ~10 existing subpackages (`api/`, `comply/`, `execute/`, `execution_intel/`, `intelligence/`, `memory/`, `mentor/`, `scheduler/`, `schemas/`, `trust/`), says "migrations 0001–0004" (actual: 27), says "16-service stack" (actual: 19). | diff vs `ls src/aegis/`, `ls db/migrations` |
| P0-3 | Medium | Config | Two packages claim env prefix `AEGIS_COMPLY_` with different field sets: `src/aegis/compliance/config.py:12` and `src/aegis/comply/settings.py:19`. Same env vars feed two different engines. See P1-9 for the deeper split-brain issue. | grep `env_prefix` |
| P0-4 | Medium | Docker | 3 images float on mutable tags: `minio/minio:latest`, `flaresolverr:latest`, `prefecthq/prefect:3-latest`. A re-pull can silently change behavior. | docker-compose.yml:126,158,602 |
| P0-5 | Low | Scrape | 4 unregistered adapter files remain as dead-code candidates: `ajio.py`, `meesho.py`, `nykaa.py`, `indiamart.py` (removed from swarm registry 2026-06-24 but files kept). | registry dump vs `ls sources/` |
| P0-6 | Low | Config | Commerce adapter credentials (eBay/BestBuy/Etsy) bypass the pydantic-settings layer; documented only in `docs/FREE_COMMERCE_SETUP.md`, absent from auto-generated `.env.example`. | prior session obs 4802–4804, confirmed by grep |
| P0-7 | Low | Repo | 242 uncommitted files at audit start — far worse than it looked: 121 were **untracked**, including entire subpackages (`memory/`, `mentor/`, `trust/`, `intelligence/`, `execution_intel/`, `predict/online/`), migrations 0012–0027, ~80 test files, and the CI workflow itself. Weeks of work existed only on disk. **RESOLVED 2026-07-02** at user request: committed as a102826 (330 files, +47,751 lines) after a secrets screen (generic + provider-key-format grep over all staged files: clean); the 2.4 MB Postgres dump `backup_pre_phaseCD_20260618_0129.dump` was excluded and gitignored. These files were already inside the Phase 0/1 audit scope (working-tree audit): ruff/bandit/mypy/compileall/pytest all covered them. | `git status`; commit a102826 |

---

## PHASE 1 — STABILITY & BUG AUDIT

### 1.1 Lint / static analysis

- **ruff (full project config, all trees):** `uv run ruff check .` → **0 violations** ("All checks passed", exit 0). Log: `.audit/20260702/ruff_full.log`. Note ruff runs with `S` (bandit), `ASYNC`, `B` (bugbear incl. mutable defaults), `DTZ` selected — so those classes are clean by construction. `BLE001` (broad except) is globally ignored by documented project policy.
- **mypy --strict src/aegis:** **537 errors** (project's own mypy config is non-strict; no pydantic plugin configured). Full log: `.audit/20260702/mypy_strict.log`. Breakdown: 139 `type-arg`, 128 `unused-ignore`, 53 `no-any-return`, 36 `import-untyped`, 34 `no-untyped-call`, 27 `arg-type`, 26 `attr-defined`, 21 `call-arg`, 17 `unreachable`, rest smaller. ~95% is typing hygiene, and the 19 `AgentDecision duration_ms` errors are false positives (field has default `Field(0.0)`; pydantic mypy plugin not enabled). **But triaging `call-arg`/`attr-defined` surfaced 4 real runtime bugs — P1-1..P1-4 below.**
- **bandit (all 6 source trees):** **190 findings** (29 High/High, 13 Medium+). Full log: `.audit/20260702/bandit_full.log`. Triage: 27 of 29 High are `B324` SHA1-for-content-ID in adapters, all annotated `# noqa: S324 — non-security use` — accepted. Remaining items feed P1-10..P1-12 and Phase 2.

### 1.2 Real bugs found (verified, not just typed)

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P1-1 | **High** | Scheduler / Evolve | **The weekly retrain job can never run.** `src/aegis/scheduler/autonomous.py:533` calls `RetrainingPipeline(pool=pool)` but the constructor signature is `__init__(self, db_pool=None, minio_client=None, settings=None)` (`src/aegis/evolve/retrain.py:67`). Every scheduled firing raises `TypeError`, which the job's `except Exception` collapses to a single WARNING (`scheduler.retrain.error`). The self-evolution loop is dead in the deployed scheduler while everything looks healthy. | Reproduced live: `RetrainingPipeline(pool=object())` → `TypeError: … unexpected keyword argument 'pool'`. mypy: `autonomous.py:533 [call-arg]` |
| P1-2 | Medium | Predict / Training | **`build_dataset()` is a stub that always returns an empty dataset** — the trend-enumeration loop was never implemented (`src/aegis/predict/training/dataset.py:165`: "In a real implementation we'd enumerate trends from the DB" → `return SignalDataset(samples=[])` unconditionally). Its default fetch closure (line 156) is also broken — calls `fetch_recent_signals(trend_id=…, since=…)` but the real signature (`src/aegis/db/signals.py:148`) has no `trend_id` and requires `limit` → would `TypeError` if the stub were ever completed. No module outside `predict/training` imports it. Docs present "Trainer + SignalDataset" as a shipped Phase 3 capability; it is dormant scaffolding. | code read; grep shows zero external callers; mypy `[call-arg]` |
| P1-3 | Medium | Dedup | Semantic dedup layer is silently inoperable even when enabled: `src/aegis/db/dedup.py:387` does `gateway = get_gateway()` but `get_gateway` is `async def` (`src/aegis/llm/bridge/agents_bridge.py:48`). The result is a coroutine; `gateway.embed(title)` → `AttributeError`, swallowed by the enclosing `try/except` ("skips the layer entirely — never raises"). With `AEGIS_DEDUP_SEMANTIC_ENABLED=true` the feature still never runs, plus an un-awaited-coroutine warning. | mypy: `dedup.py:398 "Coroutine…" has no attribute "embed"`; async def confirmed |
| P1-4 | Medium | Backup | `BackupManager._ensure_minio_bucket()` always fails silently: `src/aegis/backup/pgbackrest_manager.py:445` constructs `S3StorageBackend()` without its 4 required kwargs (`bucket`, `endpoint_url`, `access_key`, `secret_key`) and calls `backend.ensure_bucket` which does not exist (class has `_ensure_bucket`). Every call lands in `except Exception → warning "pgbackrest.minio_bucket_check_skipped"`. The backup bucket is never ensured. | mypy `[call-arg]` + `[attr-defined]`; code read |
| P1-5 | Medium | Execute / Killswitch | Killswitch trip/arm audit-row INSERT failures were swallowed with bare `pass` — **zero log**, directly contradicting the documented SEC-013 behavior ("killswitch audit write failures log at WARNING"). Audit trail could be lost with no trace. | `aegis-phase4/src/aegis/execute/api/routes/killswitch.py:69,91` (before fix) |

**P1-5 was fixed during audit** (qualifies as trivial/obviously-safe): replaced both `pass` blocks with `_log.warning("execute.killswitch.audit_write_failed", action=…, error=…)` and added regression test `test_killswitch_audit_failure_is_logged_not_silent` (endpoint must stay 200 and emit the warning when the audit pool raises). Verified: `pytest aegis-phase4/tests/integration/execute/test_api_extra.py` → **7 passed**; ruff clean. P1-1..P1-4 touch scheduler/training/dedup/backup logic → **flagged, not fixed**, per ground rule 4.

### 1.3 Systemic pattern (the umbrella finding)

| ID | Sev | Finding |
|---|---|---|
| P1-6 | **High** | **Broad-except-as-policy converts hard failures into silence.** `BLE001` is globally disabled by design ("every optional-dep and LLM call MUST degrade gracefully"), and 28 `except Exception: pass` blocks exist (7 in `dashboard/app.py`; also `scrape/dedup.py`, `scrape/topic.py`, `evolve/`, `execute/outbox/drainer.py` [metrics-only — acceptable]). P1-1 through P1-5 are all instances of the same failure mode: a real bug demoted to a warning or to nothing. For a system that must "not overstate its own confidence," this is the single most dangerous structural property found in Phase 1: **the system cannot currently distinguish "feature degraded gracefully" from "feature has never worked."** Recommendation (Phase 11 roadmap): every broad-except that guards a *feature* (not a metrics write) must increment a Prometheus failure counter and be visible on the dashboard; silent `pass` allowed only for best-effort telemetry. |

### 1.4 Other code-health findings

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P1-7 | Low | Codebase | Bare `except:` — none found (1 grep hit is a comment). Mutable default args — none (ruff B006 enforced). Blocking I/O in async — ruff ASYNC rules clean; `time.sleep` appears only in sync contexts (`predict/registry/store.py:124` file-lock `__enter__`, `dashboard/cli.py:79` CLI loop). | greps + ruff config |
| P1-8 | Medium | Typing | 537 mypy-strict errors incl. 128 stale `unused-ignore` and 1 malformed `type: ignore` (`datalake/silver/builder.py:308 [syntax]`). The strict gap means bugs of the P1-1 class are invisible to tooling today; the project's non-strict mypy caught none of them. Enabling the pydantic mypy plugin + strict on new code would have flagged all four pre-merge. | `.audit/20260702/mypy_strict.log` |
| P1-9 | **High** | Compliance | **Two parallel compliance engines are both live.** `src/aegis/comply/` drives the agent pipeline's compliance node (`agents/nodes/compliance.py:40`) and the `aegis comply` CLI (`cli/main.py:2563`); `src/aegis/compliance/` (the CLAUDE.md-documented Phase 8 engine) has 7 other import sites (REST API `/compliance/*`, Phase 6 gate). Different rulesets/thresholds, same env prefix (P0-3). An agent-verdict can pass one engine while execution gating consults the other — a split-brain on the exact dimension (regulatory risk) where a wrong confident answer is Critical. Needs a deliberate merge/kill decision, not a quick fix. | grep import sites |
| P1-10 | Medium | Compliance | `src/aegis/compliance/engine.py:296–314` reads `patent_title`/`patent_number`/`recall_number`/`reason` off values mypy resolves as `TrademarkMatch`. Runtime provenance (`ipr.check_patent → list[PatentMatch]`) appears correct, so likely typing-only — but it sits in the human-readable "reasons" path of risk assessments; verify under test in Phase 6. | mypy `[attr-defined]` ×5 |
| P1-11 | Medium | Scrape (security, preview of Phase 2) | 8 adapters parse remote XML with stdlib `xml.etree.ElementTree.fromstring` (bing_news, google_news, reddit×3, snapdeal, +2) — entity-expansion DoS surface on hostile feeds. bandit B314. Full assessment in Phase 2. | `.audit/20260702/bandit_full.log` |
| P1-12 | Medium | Cache (security, preview of Phase 2) | `pickle.loads` of Redis-cached MinHash (`scrape/dedup.py:106`) and of a local model checkpoint (`predict/online/river_models.py:67`). Redis is loopback-bound but **unauthenticated**; anyone/anything that can write to Redis achieves code execution in the scraper process. Defense-in-depth gap. Also `verify=False` in `aegis-phase12/src/aegis/security/cli.py:427` (a security tool checking headers over an unverified TLS connection). Full assessment in Phase 2. | bandit B301/B501 |
| P1-13 | Medium | Redis / durability (preview of Phase 3) | Redis runs `maxmemory 1gb` + `allkeys-lru` (docker-compose.yml:94–101): under memory pressure Redis may **evict stream keys** (`aegis:phase2:graph_results`, swarm results, outbox signals) → silent in-flight data loss. `noeviction` or `volatile-*` is the safe policy for a bus. Full assessment in Phase 3. | compose read |

### 1.5 Python 3.12 compatibility

- Runtime is Python 3.12.7; `requires-python = ">=3.12,<3.13"` pins the band.
- `python -m compileall` over all 6 source trees: **exit 0, no errors** — no syntax-level incompatibilities.
- Full test suite executes under 3.12.7 (see 1.6). Within the audited band (3.12 only), compatibility is **verified by execution**, not assumed. 3.13+ is explicitly out of contract.

### 1.6 Test suite & coverage (real numbers, not estimates)

Command: `uv run python -m pytest tests/unit/ -q -p no:hypothesis` (coverage on, per project config). Full log: `.audit/20260702/pytest_main.log`.

- **Main suite: 1 FAILED, 2,870 passed, 3 skipped — 33 min 20 s.**
- **Coverage: 77.48% — BELOW the project's own 78% floor.** The suite's coverage gate itself reports `FAIL Required test coverage of 78% not reached`. (27,912 statements measured, 5,642 missed.)
- **Phase 4 suite: 226 passed** (3.4 s), includes the new killswitch regression test. Log: `.audit/20260702/pytest_phase4.log`.
- Not run this session (separate-by-design suites): `aegis-harden`, `aegis-phase12`, `aegis-phase15`, integration suites (need `AEGIS_INTEGRATION_TEST=1`).

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P1-15 | **High** | Scrape routing / process | **The unit suite is red on the current working tree and nobody noticed.** `tests/unit/scrape/test_adapter_router.py::test_affinity_names_resolve_in_swarm_registry` fails: the topic classifier's `ADAPTER_AFFINITY` still routes to `meesho` — removed from the swarm registry 2026-06-24. `src/aegis/scrape/topic_classifier.py` also still references the other removed adapters: `ajio` (l.125), `nykaa` (l.125), `indiamart` (l.77,145,159) — the test aborts at the first assert, so it under-reports. E-commerce/supplier topics are being routed to adapters that cannot resolve. This has been failing for ~8 days; with no CI lane (P0-1) the project's own invariant test was decorative. **Fix decision needed (flagged, not applied): either purge the four names from the affinity maps or re-register the adapters — product call, since ajio/nykaa were deliberately removed.** | Reproduced: `AssertionError: TopicType.ECOMMERCE_PRODUCT: 'meesho' not in swarm registry`; grep shows 6 stale references |
| P1-16 | Medium | Quality gate | Coverage regressed below the documented floor: 77.48% vs 78% required. The last recorded green state (2026-06-17: "2727 pass exit-0, 78% floor held") no longer holds on the working tree — the uncommitted remediation batch grew code faster than tests. | pytest coverage gate output |

### 1.7 Clean-environment boot test (volumes preserved)

Executed 2026-07-02 13:49–13:56: `docker compose down` → `docker compose up -d` → wait for healthchecks → verify data.

**Results:**
- Core stack (postgres, redis, minio, flaresolverr, predict, execute-api, execute-drain, grafana, prometheus, jaeger) stopped and restarted cleanly; **all healthchecks green within ~40 s** of `up -d`. Full logs: `.audit/20260702/compose_down_2.log`, `compose_up_full.log`.
- **Data survived the cycle intact**: `SELECT count(*) FROM signals` → **8,391** rows (up from 5,430 on Jun 26 — ingestion demonstrably working); Redis `dbsize` → 7 keys (AOF persistence working).
- No startup-order failures observed on restart (depends_on/healthcheck gating worked).

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P1-14 | Medium | Docker ops | **`docker compose down` (and the `aegis down` CLI wrapper) tears down only 10 of 17 running containers.** The 7 running survivors (`autonomous`, `dashboard`, `loki`, `promtail`, `prefect`, `ollama`, `langfuse`) are exactly the profile-gated services (profiles: autonomous/dashboard/logging/orchestration/local-llm/tracing; `ollama-init` and `litellm` are also gated but weren't running), and a bare `down` ignores profile-gated services — reproducibly. The teardown even errors with "Network aegis Resource is still in use" and exit 0. Consequence: an operator who runs `aegis down` believing the system is stopped **leaves the autonomous scheduler (15 jobs, live scraping) running**. `aegis down` (`cli/main.py:281`) passes no profiles; only `aegis reset` (`cli/main.py:2132`) uses `--remove-orphans` which would catch them. Fix proposal: add `--remove-orphans` (or `COMPOSE_PROFILES` env) to `aegis down`/`docker compose down` docs. | Reproduced twice; `docker ps` after `down` shows the 7 still "Up 2 hours"; labels confirm same compose project |

---

## INTERIM STATUS AFTER PHASE 0 + PHASE 1

**Severity tally so far:** 0 Critical · 5 High (P0-1, P1-1, P1-6, P1-9, P1-15) · 12 Medium · 4 Low.

**Overall read:** the code that runs is in better shape than feared (ruff clean, boot cycle clean, data durable, 2,870/2,871 tests green), but the system has a **credibility problem in its failure reporting**: the retrain loop is dead, semantic dedup can't run, the MinIO backup-bucket check never works, the unit suite is red, and coverage is under its own floor — and every one of those was invisible because failures are demoted to warnings or swallowed outright, and no CI runs the gates. Right now, "green dashboard" and "working system" are not the same claim.

**Fixed during audit (1):** killswitch audit-write silent swallow → WARNING log + regression test (P1-5).

**Flagged for review before any fix (the big five):**
1. P1-1 retrain `TypeError` — one-line fix (`pool=` → `db_pool=`) but it reactivates a dormant learning loop; review before enabling.
2. P1-15 stale adapter affinity — purge vs re-register is a product decision.
3. P1-9 comply/compliance split-brain — needs a merge/kill decision.
4. P0-1 missing CI lane — add unit+ruff workflow (roadmap: Fix Now).
5. P1-6 silent-degradation policy — introduce failure counters/alerts for feature-guarding excepts (design change).

Phases 3–11 (infra, DB, scrapers, AI grounding, API/load, observability, soak, data quality, consolidated report) are **not yet run**.

### Could Not Verify (Phase 0/1 scope)

1. **True from-scratch boot (fresh volumes).** All compose ports are fixed to 127.0.0.1 and already bound by the running stack; a genuinely clean `up` requires wiping `aegis_postgres_data` etc. (destructive — ground rule 5) or a port-remapped override file. Performed instead: full `down`/`up` cycle with volumes preserved (see 1.7). Fresh-volume boot deferred until a copy-based dry-run is set up.
2. **Whether the Sunday retrain failure (P1-1) fired in production logs** — container logs only retain ~1h (restarted today); the bug is proven by reproduction instead.
3. Coverage for `aegis-harden`, `aegis-phase12`, `aegis-phase15` suites — run separately by design; only the main suite + phase4 measured this session.

---

## PHASE 2 — SECURITY AUDIT

Tools: `detect-secrets 1.5.0` (gitleaks/trufflehog not installed), `pip-audit`, manual review, and a new prompt-injection test suite (`tests/security/test_prompt_injection.py`, 3 tests, all pass). Logs: `.audit/20260702/detect_secrets.json`, `pip_audit.json`.

### Overall posture (the good news first)
- **No real secrets in the tree or the 12-commit history.** detect-secrets flagged 90 items across 57 files; every one triaged to a dev-default (`aegis_app_dev_pw`, `admin/admin` dev user store explicitly marked "NEVER use in production"), the AWS documentation example key `AKIAIOSFODNN7EXAMPLE` (used in PII-scrubber tests), or doc strings. A full-history grep for provider key formats (`gsk_`, `sk-`, `ghp_`, real `AKIA`, `AIza`, `xoxb-`, PEM private keys) returned nothing real. No `.env` was ever committed.
- **All 4 Dockerfiles run as non-root** (`USER aegis`), multi-stage, `python:3.12-slim-bookworm` base. No baked-in secrets, no `latest` base tags in the app images.
- **Every published compose port binds `127.0.0.1`** — nothing is on a public interface by default.
- **No `shell=True`, no `os.system`, no `os.popen`** anywhere in the source trees.
- The dashboard ops-console RCE endpoint is **well-defended**: constant-time `X-Ops-Token` check, `_require_ops_token_in_prod()` refuses to start in prod without a token (ENV-2), CORS locked to loopback origins.

### Phase 2 findings

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P2-1 | **High** | Dependencies | **113 known CVEs across 26 installed packages; 108/113 have a fix version available.** Worst web-facing: aiohttp 3.10.10 (32 vulns; fix 3.10.11), starlette 0.48.0 (7; fix 0.49.1), pillow 11.3.0 (7), cryptography 43.0.1 (5; fix 44.0.1), jinja2 3.1.4 (3 incl. SSTI-class CVE-2025-27516; fix 3.1.6), urllib3 2.6.3 (3; fix 2.7.0), lxml 5.3.0 (2). The three CVEs the project deliberately pinned (orjson/pyjwt/python-dotenv) are fixed, but the broader dependency set has drifted well behind. Most are patch/minor bumps. | `pip-audit`; `.audit/20260702/pip_audit.json` |
| P2-2 | **High** | Execute API | **Killswitch and all execute-api routes are unauthenticated by default.** `api_bearer_token` defaults to `""` and `require_bearer()` returns `"anonymous"` (permissive) when empty — and unlike the dashboard there is **no prod fail-fast guard**. Anyone who can reach `:8200` can `POST /killswitch/trip` (halts all trading-alert dispatch) or `/killswitch/arm` with no credential. Loopback-bound today, so exposure requires the port to be published — but the default-open posture on a trading killswitch is the wrong default. Fix: mirror the dashboard's `_require_ops_token_in_prod()` — refuse to start in prod/live mode with an empty bearer token. | `aegis-phase4/src/aegis/execute/api/auth.py:29-32`; `config.py:125`; no prod guard in `api/app.py` |
| P2-3 | **High** | Unified API | The unified API (`aegis.api.main:build_app`, tags geo/compliance/evolve/datalake) mounts **every router with zero auth** — including state-changing/expensive `POST /evolve/retrain`, `POST /compliance/assess`, `POST /geo/analyze`. Currently **not served in compose** (no `:8400` service), so it's a latent exposure: the moment anyone runs `uvicorn aegis.api.main:app` in an environment it becomes a fully open control surface. Flag before it ships. | `src/aegis/api/main.py:58-74`; 0 auth-refs in geo/evolve/compliance/comply routers; grep of compose (no 8400) |
| P2-4 | Medium | Execute / RLS | **SQL-injection-shaped RLS tenant setter.** `settlement.py:408` does `f"SET app.current_tenant = '{tenant_id}'"` — string interpolation into the value that drives Row-Level Security. The rest of the codebase (execution_intel, scheduler, trust) uses the safe parameterized idiom `SELECT set_config('app.current_tenant', $1, ...)`. `tenant_id` is internally-sourced today (low live risk), but an injection here is a cross-tenant RLS bypass — Critical class if any user-influenced value ever reaches it. Fix is mechanical: use the `set_config($1)` form already used everywhere else. | `aegis-phase4/src/aegis/execute/settlement.py:408` |
| P2-5 | Medium | LLM / prompt injection | **Scraped content reaches the LLM prompt verbatim; the verdict is safe but the human-facing reasoning is an unfiltered injection sink.** `title`/`summary`/`representative_text` are interpolated into `scout.jinja2` with no delimiting. **Tested directly** (`tests/security/test_prompt_injection.py`): a malicious model response *cannot* flip the verdict (`_llm_apply` never touches `verdict`) and *cannot* breach the confidence bound (`confidence_factor` clamped to [0.5,1.0], multiplied — the heuristic-first doctrine holds — verified). BUT the model's free-text `reasoning` is appended verbatim (up to 1500 chars) into the operator-facing output with **no output guardrail on this path** (`base.py:_llm_apply` bypasses the Phase 11 GuardrailsValidator). An adversary who seeds a marketplace listing with hidden text ("GUARANTEED 500% ROI — BUY NOW") gets that text surfaced in AEGIS's reasoning — directly undermining the "trustworthy, data-driven argument" goal even though the numeric verdict is sound. Fix: run PII/guardrail scrubbing on `reasoning` before persisting, and/or fence scraped content in the prompt. | `src/aegis/agents/nodes/base.py:188-201`; `scout.py:268`; new test suite (3 pass) |
| P2-6 | Medium | API hardening | **No HTTP rate limiting on any served endpoint.** Phase 12 ships a `RateLimitMiddleware` but it is never `add_middleware`'d onto predict/execute/dashboard — only the internal LLM gateway has throttling. Predict `:8100` `/predict` + `/predict/batch` also have **no auth at all**. Combined with P2-2, the served surface is thin on request-abuse defense. Loopback-bound mitigates today. | grep: `RateLimitMiddleware` unmounted; `predict/serving/app.py:248,256` no auth dep |
| P2-7 | Medium | XML / untrusted input | 8 adapters parse remote feeds with stdlib `xml.etree.ElementTree.fromstring` (bing_news, google_news, reddit×3, snapdeal, +2) — billion-laughs / entity-expansion DoS on a hostile or MITM'd feed. Fix: `defusedxml` or `defuse_stdlib()`. | bandit B314; `.audit/20260702/bandit_full.log` |
| P2-8 | Medium | Deserialization | `pickle.loads` of a **Redis-cached** MinHash (`scrape/dedup.py:106`) and a local model checkpoint (`predict/online/river_models.py:67`). Redis is loopback-bound but **unauthenticated (no `requirepass`)** — anything that can write the cache key achieves code execution in the scraper process. Defense-in-depth gap; prefer a non-pickle codec for the Redis path. | bandit B301; compose redis has no `requirepass` |
| P2-9 | Low | Config hygiene | Weak dev credentials are baked as compose fallbacks (`${POSTGRES_PASSWORD:-aegis_app_dev_pw}`, `admin/admin` dev user store, `verify=False` in the phase12 header-check CLI tool `security/cli.py:427`). Env overrides exist (good), but a deploy that forgets to set them silently runs on known-weak creds. | detect-secrets; compose review |

### Phase 2 severity tally: 0 Critical · 3 High (P2-1, P2-2, P2-3) · 5 Medium · 1 Low.

**One thing worth stating plainly for the trust goal (P2-5):** the heuristic-first architecture is doing exactly what it was designed to do — a prompt injection buried in scraped text cannot make AEGIS output a *wrong number*. That is a genuinely strong property most LLM-in-the-loop systems don't have. The gap is narrower and more subtle: it can still make AEGIS output a *wrong sentence* in the reasoning a human reads. For a system meant to "present convincing arguments," the argument text needs the same grounding discipline as the verdict.

### Could Not Verify (Phase 2)
1. **trivy / hadolint image + Dockerfile scan** — not installed; Dockerfiles reviewed manually (non-root confirmed) but layer-level CVE scan of the built images was not run.
2. **gitleaks/trufflehog** — not installed; used detect-secrets + targeted history greps instead. History is only 12 commits, so coverage is high, but a dedicated entropy scanner would be more thorough.
3. **Live SSRF probe** — no user-supplied-URL fetch endpoint was found in the served apps (scrapers fetch predetermined feeds), so SSRF surface looks low, but this was by code-read, not by fuzzing an exposed endpoint.
4. **Whether P2-2/P2-3 are exploitable in the current deploy** — both are loopback-bound now; the finding is about default posture, not a live open port.

---

## PHASE 3 — INFRASTRUCTURE & DOCKER

Verified against the live 17-container stack + compose file.

### Posture (verified)
- **Every long-running service has a restart policy (`unless-stopped`) and `deploy.resources.limits`.** Only `ollama-init` lacks both — correct, it's a one-shot (`restart: no`).
- **Healthchecks on 16/19 services.** Missing on `execute-drain` and `promtail` (both port-less workers) and `ollama-init` (one-shot). P3-3 below.
- **Startup ordering is gated properly**: app services `depends_on` postgres/redis with `condition: service_healthy`; grafana waits on prometheus; promtail on loki. No obvious race — the boot test (§1.7) restarted cleanly.
- **Graceful shutdown exists**: intake/drain workers use an `asyncio.Event` stop flag + task cancel + a `_flush` of buffered half-inputs on shutdown.
- **Delivery is properly at-least-once**: the outbox drainer claims rows with `SELECT … FOR UPDATE SKIP LOCKED`, transitions pending→delivering→delivered/retry/failed with `max_attempts` and backoff. Once an alert reaches the DB outbox, it will not be silently lost.

### Phase 3 findings

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P3-1 | **High** | Redis / durability | **The Phase 2→Phase 4 intake path is at-most-once and drops verdicts on any transient error.** `intake_worker._handle()` calls `self._ack(stream, entry_id)` in a `finally` block, so when `submit_phase2_dict()` raises (pipeline error, DB blip), the message is logged at ERROR **and then ACKed** — removed from the consumer-group pending list, never retried, no dead-letter. There is no DLQ or max-retry on intake. A crash strictly between submit and ack is the *only* window a message survives; a handled exception loses it permanently. For "convert opportunities before others," a dropped ENTER verdict is a missed opportunity with no trace. Fix: ACK only on success (or after DLQ-publish); leave failures pending for `XAUTOCLAIM` retry. | `aegis-phase4/src/aegis/execute/workers/intake_worker.py:232-254` |
| P3-2 | **High** | Redis / durability | **Redis is configured `maxmemory 1gb` + `allkeys-lru`** (docker-compose.yml:94-101). Under memory pressure Redis will evict *any* key by LRU — including the `aegis:phase2:graph_results` bus stream, swarm results, and any not-yet-consumed intake entries → silent in-flight data loss upstream of P3-1. A message bus must use `noeviction` (or `volatile-*` with TTLs only on disposable keys). Combined with P3-1, the ingestion→verdict path has two independent silent-loss vectors. | docker-compose.yml redis `command` |
| P3-3 | Low | Docker | `execute-drain` and `promtail` have no healthcheck. Both are port-less so there's nothing to curl, but a `pgrep`/liveness probe would let compose restart a wedged worker instead of leaving it "up" but stalled. | compose matrix |
| P3-4 | Low | Docker | 3 base/app images pin mutable tags (`minio:latest`, `flaresolverr:latest`, `prefect:3-latest`) — see P0-4; a re-pull can change behavior under you. Digest-pin or version-pin. | compose |

### Could Not Verify (Phase 3)
1. **Kill-a-service-mid-run chaos test** — deferred to Phase 9 (soak/chaos) to avoid disrupting the live stack mid-audit; P3-1/P3-2 are proven by code + config, not yet by an induced crash.
2. **Behaviour under real memory pressure** (P3-2 eviction actually firing) — would require driving Redis past 1 GB; reasoned from config, not induced.

---

## PHASE 4 — DATABASE (TimescaleDB)

Verified live against the running `aegis-postgres` (TimescaleDB pg16), 8,797 signals.

### Posture (verified)
- **16 hypertables**, sensible partitioning. Retention policies present: signals/media 90 days, velocity_snapshots 180 days. **3 continuous aggregates** with refresh policies (30 min / 2 h / 12 h cadences). This is a mature TimescaleDB setup, not a bare table.
- **10 purpose-built indexes on `signals`** (tenant+ts, platform+ts, tier+ts, author+ts, tags GIN, content_hash, intent+ts, external-unique, ts, pk) — query patterns are clearly considered.

### Phase 4 findings

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P4-1 | **High** | TimescaleDB | **Severe chunk bloat on the primary hypertable: 958 chunks, 936 empty (~98%).** Chunk ranges span **2007-10-09 → 2026-07-03** (~19 years) though only 22 distinct days actually hold data (May 2–Jul 2). Every planner run must consider chunk exclusion across ~1,000 chunks. Root cause is historical: signals were once inserted with very old `scraped_at` values (bulk test data or a since-fixed timestamp bug), creating chunks that were later emptied but never dropped. **The 90-day retention policy is not reclaiming them: 867 chunks have `range_end` older than 90 days, yet the retention job reports `Success` (34 runs, 0 failures, last 2026-07-02 07:00).** So retention *runs* but does not drop these old/empty chunks — a real gap to root-cause (likely the policy was added after the bloat and its dimension scoping doesn't match, or the chunks predate the policy's `drop_after` reference). Live `scraped_at` is currently clean (no future/ancient rows), so the timestamp bug appears fixed — but the catalog bloat it left behind persists. Fix: manual `drop_chunks('signals', older_than => INTERVAL '90 days')` to clear the backlog, then confirm the policy keeps it clear. | live TimescaleDB queries; chunk range + job_stats |
| P4-2 | Medium | TimescaleDB | **Compression is disabled on `signals` and `media`** (`compression_enabled = f`) despite both being append-heavy, time-ordered, and having 90-day retention. Native columnar compression would cut storage and speed range scans materially. Retention without compression leaves the middle of the lifecycle uncompressed. | `timescaledb_information.hypertables` |
| P4-3 | Medium | Migrations | **Migrations are forward-only with no rollback path.** 27 raw-SQL files (0001–0027), zero `down`/rollback scripts, and `alembic/versions/` has no `downgrade()` bodies. The directive's "run all migrations up then down" is not possible — there is no down. A bad migration in prod can only be fixed by writing a new forward migration, never rolled back. Acceptable for a solo project but a real operational risk as it grows. | `ls db/migrations`; grep alembic |
| P4-4 | Low | Indexes | 10 indexes on `signals` all show `idx_scan = 0` — but `pg_stat` was reset by today's container restart (~1 h uptime), so this is **not** evidence they're unused. Flagged only as: 10 indexes on a high-insert table is real write amplification; whether all 10 earn their keep needs a measurement window with stats intact (Phase 9). | `pg_stat_user_indexes`; uptime |

### Could Not Verify (Phase 4)
1. **Migration up→down→up reversibility** — no down path exists (P4-3), so the round-trip test is inapplicable rather than passed.
2. **EXPLAIN ANALYZE against real query load** — index-usage stats were reset on restart; a meaningful "indexes defined vs used" read needs an uptime window (deferred to Phase 9 soak).
3. **Backup/restore actually exercised** — pgBackRest binary is not installed in this environment (P1-4 shows the MinIO bucket-ensure is also broken); a real backup→restore round-trip was not run. The `backup_pre_phaseCD_*.dump` file proves `pg_dump` has been used manually, but the automated DR path is unverified end-to-end.

---

## PHASE 5 — DATA PIPELINE & SCRAPER AUDIT

Live-tested a representative sample + read live freshness from the DB and the running autonomous scheduler logs. Full per-adapter live validation of all 37 is deferred (see Could Not Verify), but the shape of the problem is clear and verified.

### Scraper health table (from live DB freshness + live probes, 2026-07-02)

| Platform | Signals 24h | Signals 7d | Last seen | Status | Notes |
|---|---:|---:|---|---|---|
| google_news | 1,250 | 2,232 | 2026-07-02 | **healthy** | dominant source |
| hacker_news | 530 | 619 | 2026-07-02 | **healthy** | live probe: 3/3 emitted, 1.2 s |
| reddit | 210 | 617 | 2026-07-02 | healthy | |
| bing_news | 316 | 527 | 2026-07-02 | healthy | |
| github_trending | 11 | 49 | 2026-07-02 | degraded | low yield |
| amazon | 2 | 22 | 2026-07-02 | degraded | live probe works (3/3), but near-zero in prod |
| myntra | 0 | 10 | 2026-07-01 | degraded | |
| reddit_ecommerce | 0 | 3 | 2026-07-01 | degraded | |
| amazon_in | 0 | 0 | 2026-06-20 | **dead** | 12 days silent; flagged by monitor |
| google_trends_india | 0 | 0 | 2026-06-23 | **dead** | flagged by monitor |
| youtube_rss | 0 | 0 | 2026-06-23 | **dead** | flagged by monitor |
| *(26 other registered adapters)* | 0 | 0 | never | **absent** | never produced a DB signal |

### Findings

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P5-1 | **High** | Data quality / core mission | **AEGIS is running almost entirely on news RSS, not marketplace data.** Of 37 registered adapters, only 6 produced signals in the last 24 h and 5 of those are news feeds (google/bing/hacker_news/reddit + amazon bestseller titles). 26 registered adapters have never landed a DB signal; the India-commerce layer (amazon_in, flipkart, meesho, myntra, nykaa, ajio, snapdeal, indiamart) is effectively non-productive in prod. The confidence gate confirms it live: `signal_freshness` 0.125–0.157 (≈87% of signals >24 h old). For the stated goal — "superior to single-function marketplace tools" — the actual buy-side/commerce signal is the thin part. This matches the documented "residential-proxy ceiling," but the effect on intelligence quality is the headline: the product's differentiator (cross-marketplace arbitrage) is data-starved. | live DB freshness; scheduler confidence-gate logs |
| P5-2 | Medium | Scrape orchestration | **Adapters work in isolation but don't persist through the swarm path.** Live `aegis scrape --source flipkart` returns 200 + 3 parsed signals, and `amazon` emits 3/3 — yet flipkart has 0 DB rows in 7 d and amazon only 22. The single-adapter CLI path and the swarm/autonomous path diverge (confirmed in CLAUDE.md as separate code flows); the swarm path is dropping or not persisting what the adapters can fetch. Worth tracing end-to-end: an adapter that passes a manual probe but yields nothing in prod is the most deceptive failure mode. | live probes vs DB counts |
| P5-3 | Low→Medium | Freshness monitoring | **CREDIT: freshness monitoring exists, runs, and works.** `scheduler/health_checker._check_adapter_zero_yield` fires every 5 min and correctly caught the exact dead set live: `health.adapter_zero_yield count=3 platforms=['amazon_in','youtube_rss','google_trends_india']`; stream-staleness detection triggers an emergency scrape. **The gap:** alerts go only to structlog — there is no external alert (Sentry/Telegram/email) for "source dead N days," and the only self-heal action (`emergency_scrape`) cannot fix a WAF-blocked commerce adapter, so it detects-but-cannot-heal and re-flags indefinitely. A human must be reading logs. | live autonomous logs |

### Could Not Verify (Phase 5)
1. **Full live validation of all 37 adapters** — tested hacker_news, google_news, amazon, flipkart, amazon_in directly; the rest are inferred dead/absent from DB freshness, not each individually probed (would mean 30+ live network calls, several against WAF-protected sites). The health table's "dead/absent" rows are from DB evidence, not per-adapter live failure capture.
2. **Timestamp normalization (IST vs UTC) spot-check** — live `scraped_at` is currently clean (all UTC, no future/ancient rows), but the P4-1 chunk bloat back to 2007 proves a *past* timestamp bug existed; whether source-provided publish-times (vs scrape-time) are correctly normalized to UTC across all adapters was not spot-checked against 20 raw sources this session.
3. **robots.txt / rate-limit compliance per adapter** — not audited this session; the ConcurrencyGovernor caps global concurrency but per-site robots respect was not verified.

---

## PHASE 6 — AI REASONING & GROUNDING

Examined live verdicts from the `aegis:phase2:graph_results` stream + the runner/calibration code + the DB trust tables.

### The strong parts (verified, and they matter for the trust goal)
- **Hallucination surface is ~zero on the live path.** 20/20 recent verdicts have `llm_used: false` / `reasoning_source: "heuristic"`. Reasoning is numeric templating from deterministic features (`velocity_class=flat breakout=0.11; commercial_intent=0.40; …`), not free-form generation. A verdict cannot hallucinate a fact when no LLM produced it. The LLM only augments borderline HOLD cases and is bounded (verdict-locked + clamped confidence — proven in Phase 2). **Measured hallucination rate on the deterministic path: 0 by construction.**
- **The system is honest about its own uncertainty.** Every verdict carries `confidence_basis: "UNVERIFIED_raw"` and `confidence_calibrated: false` — it explicitly refuses to present its raw confidence as trustworthy. Per the trust subsystem's own measurement, the heuristic is badly miscalibrated (0.98 stated → 0.38 realized), and the system labels it rather than hiding it. That is exactly the "don't overstate confidence" property you asked for.
- **Determinism is high.** Heuristic path + seed + content-addressed prediction_id → identical inputs give identical verdicts. The 5×-consistency concern is structurally satisfied on the no-LLM path.

### Findings

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P6-1 | **High** | Calibration / trust | **The calibration the system built is inert in the live verdict path — a tenant-key mismatch.** A real isotonic map (`heuristic_rise`, fitted on 322 settled outcomes, base_rate 0.49) exists in `calibration_maps` under tenant `00000000-0000-0000-0000-000000000001`. But `run_trend()` defaults to `tenant_id="default"` (a literal string — `runner.py:166`), and **20/20 live verdicts ran under `"default"`**. `TrustStore.load_map()` is RLS-scoped by that tenant, so it finds no map → `_calibrate_confidence` fails open → every verdict emits raw `UNVERIFIED_raw` confidence. Downstream, the calibrated-ENTER floor gate and skill gate **can never fire** (both require `confidence_calibrated=True`). Net: the entire OMEGA calibration/trust investment produces a fitted map that the production path never reads. Honest (it labels UNVERIFIED) but self-defeating. Fix: make `run_trend`/dashboard/CLI pass the canonical `default_tenant_id` UUID, or resolve `"default"`→UUID before the trust lookup. | live stream tenant=default ×20; `calibration_maps.tenant_id`=UUID; `runner.py:166,550`; `trust/store.py:74-82` |
| P6-2 | **High** | Grounding / provenance | **Verdicts do not carry per-signal provenance.** 0 of 20 published verdicts contain `sample_signal_ids`, `source_url`, or `signal_id` — the schema field `sample_signal_ids` (AgentDecision, max 50) exists but is unpopulated in the stream payload. A verdict traces to platform + count + drivers ("30 signals across 3 platforms; velocity_6h drove it") but an operator **cannot click through to the specific source posts/URLs** behind the call. The directive's standard ("an unlinked verdict is a critical finding") is partially met — it's *semi*-linked (aggregate, not per-signal). For "present convincing, data-driven arguments," the evidence trail stops one level short of the actual sources. | stream grep; `agents/schemas.py:220` |
| P6-3 | Medium | Calibration freshness | The one fitted map last updated **2026-06-25** (7 days stale) and has not refreshed since — consistent with the dead retrain/settle loop (P1-1) and the stalled outcome-settling. Even after P6-1 is fixed, the map would apply week-old calibration. The two findings compound: the loop that keeps calibration current is down, and the path that would consume it is mis-keyed. | `calibration_maps.updated_at` |

### Could Not Verify (Phase 6)
1. **Empirical hallucination rate on the LLM-augmented path** — 20/20 live verdicts were heuristic-only (no LLM keys active / Ollama path not exercised on these), so I could not sample 30 *LLM-augmented* verdicts to cross-check factual claims. The deterministic-path rate is 0 by construction; the LLM-path rate is bounded by architecture (Phase 2) but not measured on live traffic.
2. **Confidence-vs-correctness calibration on live data** — requires settled outcomes joined to predictions over a window; the settle loop is stalled (P6-3), so a fresh live calibration curve could not be computed this session. The historical fit (Brier skill on 322 samples) exists but is a week old.
3. **Adversarial/contradictory-source handling** — the heuristic blends sources deterministically and flags low `data_confidence`, but a crafted contradictory-source fixture test was not run this session (candidate for a follow-up alongside the Phase 2 injection suite).

---

## PHASE 7 — API & UI/UX

Live-tested the three served APIs (predict :8100, execute :8200, dashboard :8300).

| Check | Result |
|---|---|
| Health endpoints | all **200**, fast (predict 52 ms, execute 10 ms, dashboard 17 ms) |
| predict input validation | **422** on empty body and on non-JSON — pydantic validation works |
| execute killswitch GET, no auth | **200** — live confirmation of P2-2 (unauthenticated by default) |
| dashboard `/api/stats` | 200 but **1.46 s** |
| Load: predict `/healthz`, 300 reqs @30 concurrent | 288 req/s, 100% success, **p50 50 ms → p95 579 ms → p99 626 ms** |

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P7-1 | Medium | Predict serving | **Single-worker serving tier; tail latency degrades sharply under modest concurrency.** `Dockerfile.predict` runs `uvicorn --workers 1`. A trivial `/healthz` under 30-way concurrency goes from p50 50 ms to p95 579 ms / p99 626 ms — requests queue behind one worker. Fine for internal single-caller use; would fall over if exposed or driven by the swarm at fan-out. Raise workers or front with a process manager. | live load probe; `Dockerfile.predict:97` |
| P7-2 | Medium | Dashboard | `/api/stats` takes **1.46 s** — slow for a dashboard aggregate, and a likely downstream symptom of the P4-1 chunk bloat (planner scanning ~1,000 chunks). Would present as a sluggish UI. | live curl |

(Full per-route valid/invalid/malformed matrix and a k6/locust sustained load test were not run — see Could Not Verify.)

---

## PHASE 8 — OBSERVABILITY

The Phase 14 stack (OTel→Jaeger, Prometheus, Grafana, Loki, 24 metrics, Sentry hooks) is **built and the containers are healthy** — but the live deployment's alerting/capture last mile is disconnected.

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P8-1 | **High** | Metrics / alerting | **Prometheus has near-zero visibility and there is no alerting at all.** The scrape config defines only 2 jobs: `prometheus` (self) and `aegis` (app metrics at `host.docker.internal:8001`) — and that one is **DOWN** (`dial tcp … connection`). None of the 19 containers (postgres, redis, predict, execute, dashboard, …) are scraped. There are **no alert rules and no Alertmanager**. So metrics that are collected never page anyone, and most services emit nothing Prometheus can see. | live `/api/v1/targets` (1 up, 1 down); `config/prometheus/*.yml` (2 jobs, 0 alert files) |
| P8-2 | **High** | Error tracking | **Sentry is off** — `AEGIS_SENTRY_DSN=` is empty, so `init_sentry()` is a documented no-op. Combined with the broad-except-logs-only policy (P1-6), the freshness alerts being log-only (P5-3), and P8-1, **no failure in this system pages a human**. Everything found in this audit that "fails silently" is silent precisely because the last mile — Sentry + Prometheus alerts — is not wired. This is the single highest-leverage fix for the "run a week unattended" goal. | `.env.example:35`; Phase 14 no-op behavior |
| P8-3 | Low | Observability | Structured logging is genuinely good (structlog, consistent event keys, correlation_ids throughout — visible in every log sample this audit) and Loki/Promtail ship it. The gap is not log *quality*, it's that logs are the *only* channel — nothing escalates. | log samples across phases |

### Could Not Verify (Phase 7–8)
1. **k6/locust sustained load test** and full per-route fuzzing — not run (no load tool installed; used a Python concurrent probe for a first-order req/s + tail-latency read).
2. **Whether the `aegis:8001` metrics target is down by config or by the app not starting its metrics server** — the target is unreachable from Prometheus; root-causing (wrong host in a container context vs `start_metrics_server` never called) needs a follow-up.
3. **Jaeger trace completeness** — Jaeger is healthy and OTel is wired, but I did not drive a request end-to-end and confirm a full trace span tree this session.

---

## PHASE 9 — LOAD, STRESS & CHAOS

### Chaos test (executed live)
Restarted `execute-drain` + `execute-api` mid-flight, then checked recovery and data integrity:
- Both containers recovered; `execute /healthz` → **200** within 8 s.
- Phase 2 stream length **preserved (84 → 84)** — no bus loss.
- Outbox unchanged (**36 delivered**) — the durable delivery path survived the restart cleanly.

**Verdict: the at-least-once delivery path is genuinely resilient** — a mid-run restart lost nothing. This is a real strength and the correct counterpoint to P3-1.

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P9-1 | Medium | Redis consumer group | The `aegis-execute-intake` group has **21 registered consumers for effectively one worker** — a consumer-name leak across restarts (each restart registers a new consumer, none reaped). Harmless now but unbounded; `XGROUP DELCONSUMER` on shutdown or a periodic reaper would fix it. Also confirms P3-1: group `pending=0, lag=0` across 84 entries means every message was ACKed (nothing ever left pending for retry). | live `XINFO GROUPS` |
| P9-2 | Low (now) | Redis eviction | P3-2 (allkeys-lru evicting the bus) is **not currently firing** — Redis is at 2.05 MB of a 1 GB cap with 84 stream entries. The risk is real under a sustained campaign but was not triggered in this session; reported honestly as latent, not active. | live `INFO memory` |

### Could Not Verify (Phase 9)
1. **Inducing an actual P3-1 drop** — would require injecting a deliberately-failing message into the live intake stream and confirming it's ACKed-and-lost; not done to avoid polluting the live pipeline. P3-1 stands on code + the pending=0 evidence, not an induced loss.
2. **Sustained multi-hour soak** for memory growth / crash loops — out of scope for this session's time budget; the stack has been up ~2 h healthy with no observed growth, but that is not a soak.

---

## PHASE 10 — DATA QUALITY & QUANTITY SCORECARD

Computed live from `signals` (8,797 rows).

| Dimension | Measured | Read |
|---|---|---|
| title populated | **100%** | good |
| url populated | **100%** | good |
| source_confidence populated | **100%** | good |
| author_id populated | 47.3% | expected (news RSS often has no author) |
| **price_amount populated** | **0.0%** | **critical for the mission** |
| Dedup effectiveness | 3 dupes / 4,224 (**0.07%**) | excellent |
| Volume last 4 active days | Jul 2: 2,326 · Jul 1: 1,490 · Jun 26: 356 · Jun 25: 52 | spiky |
| Ingestion gap | **Jun 27–30: zero signals (4-day blackout)** | unalerted |

| ID | Sev | Component | Finding | Evidence |
|---|---|---|---|---|
| P10-1 | **High** | Data quality / mission | **Not a single signal in the database has a price (0.0% of 8,797).** The T2_commerce price-extraction fix was committed, but because the commerce adapters don't land signals (P5-1), the arbitrage/pricing engine has **no price data to operate on**. Every "cross-market margin" or "arbitrage opportunity" the system can produce is computed from category-median fallbacks, not observed marketplace prices. For a product positioned as superior to marketplace tools, the single most important field for that claim is empty. | `SELECT … price_amount IS NOT NULL` → 0.0% |
| P10-2 | **High** | Ingestion continuity | **A 4-day ingestion blackout (Jun 27–30) passed with no alert.** Volume is spiky (52 → 356 → 0×4 days → 1,490 → 2,326), not steady. Nothing paged during the outage (consistent with P8-1/P8-2 — no alerting). A market-intelligence system that can go dark for four days unnoticed cannot be "trusted to catch opportunities before others." | daily volume query |
| P10-3 | Low (credit) | Dedup | Deduplication is working very well — 0.07% duplicate retention across 4,224 recent rows. The MinHash/semantic dedup investment is paying off (even though the *optional* embedding layer P1-3 is broken, the primary layers work). | dup-rate query |

### Could Not Verify (Phase 10)
1. **Accuracy spot-check of 20 normalized signals vs raw source** — not performed this session; completeness and dedup are measured, but field-level accuracy (does the normalized title/sentiment match the source) was not sampled.
2. **Source-event → signal-availability latency** — not instrumented end-to-end this session; the confidence gate reports `signal_freshness` but true ingestion latency per source was not measured.
