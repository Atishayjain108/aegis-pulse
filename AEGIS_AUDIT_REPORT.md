# AEGIS PULSE — FULL-SPECTRUM AUDIT REPORT

- **Audit start:** 2026-07-02
- **Branch:** `audit/full-system-20260702` (cut from `audit-remediation-2026-06` @ c22bd12)
- **Auditor:** Claude (principal-engineer-level directive v1.0)
- **Scope this session:** Phase 0 (ground truth) + Phase 1 (stability & bugs). Phases 2–11 pending.
- **Artifacts:** full tool logs in `.audit/20260702/` (ruff_full.log, bandit_full.log, mypy_strict.log)
- **Working-tree caveat:** 242 files were modified-but-uncommitted from the prior remediation session when the audit began. The audit covers the working tree (= what is actually running in the containers), not HEAD. These changes were deliberately NOT committed by the audit.

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

Phases 2–11 (security, infra, DB, scrapers, AI grounding, API/load, observability, soak, data quality, consolidated report) are **not yet run**.

### Could Not Verify (Phase 0/1 scope)

1. **True from-scratch boot (fresh volumes).** All compose ports are fixed to 127.0.0.1 and already bound by the running stack; a genuinely clean `up` requires wiping `aegis_postgres_data` etc. (destructive — ground rule 5) or a port-remapped override file. Performed instead: full `down`/`up` cycle with volumes preserved (see 1.7). Fresh-volume boot deferred until a copy-based dry-run is set up.
2. **Whether the Sunday retrain failure (P1-1) fired in production logs** — container logs only retain ~1h (restarted today); the bug is proven by reproduction instead.
3. Coverage for `aegis-harden`, `aegis-phase12`, `aegis-phase15` suites — run separately by design; only the main suite + phase4 measured this session.
