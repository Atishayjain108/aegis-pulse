# AEGIS Pulse — System State

_Snapshot after AEGIS KRONOS-OMEGA (GODMODE) Passes 0–12 — 2026-06-14._

This document records the current operational state of the system. For the
plain-English architectural tour see [`../SYSTEM_TOUR.md`](../SYSTEM_TOUR.md);
for per-phase status see the table in [`../CLAUDE.md`](../CLAUDE.md).

## Phase status

All phases 0–15 are green. See `CLAUDE.md` "Phase status" table for the
authoritative per-phase breakdown. The GODMODE passes layered the following
capabilities on top of the phase work:

| Pass | Capability | Key module(s) |
|------|-----------|---------------|
| 1 | Connectivity / dashboard / datalake / scheduler hardening | `scheduler/autonomous.py` |
| 6 | Deep Research Output Engine (5-pass) | `agents/research_engine.py`, `aegis research` CLI |
| 7 | MinHash/LSH dedup, shared HTTP client, dynamic confidence thresholds | `scrape/dedup.py`, `scrape/http_client.py` |
| 9 | OSS power integration (River, FAISS, Evidently, NetworkX, MLflow) | wired into runner/dedup/swarm/retrain |
| 10 | Complete test specification (11 new test files, 74 tests) | `tests/unit/**` |
| 11 | Real-Time Intelligence pattern engine | `scrape/pattern_engine.py` |
| 12 | Invariant enforcement + error docs | `tests/unit/test_invariants.py`, `docs/errors/` |

## Intelligent routing

- `aegis.scrape.topic_classifier.TopicClassifier` — keyword classification (<1 ms).
- `aegis.scrape.adapter_router.AdapterRouter` — per-query adapter ranking (<5 ms, no network).
  - Financial queries → `nse_bse`, `moneycontrol`, `economic_times` first.
  - Ecommerce queries → `amazon`/`amazon_in`, `flipkart` first.
  - Supplier-discovery queries → `indiamart` first.

## Self-healing

- `aegis.scheduler.health_checker.AegisHealthChecker` runs inside the autonomous
  loop; emergency health check fires when all adapters are quarantined.
- All 10 autonomous-loop jobs are individually error-wrapped — a failure in one
  never halts the loop (invariant audited in Pass 12).

## Observability / health

- `GET /api/health/streams` reports status for the canonical event-bus streams
  defined in `dashboard/app.py::_CANONICAL_STREAMS` (all Redis calls run in
  parallel; <100 ms).
- Dashboard renders all panels on a fresh start (degraded panels show a status,
  never a blank crash).

## Verified performance (2026-06-14, local)

| Operation | Budget | Measured |
|-----------|--------|----------|
| `deduplicate_batch(1000)` | < 500 ms | ~1 ms |
| `TopicClassifier.classify()` | < 1 ms | ~0.13 ms |
| `AdapterRouter.route()` | < 5 ms | ~0.36 ms |
| `PatternEngine.detect(1000)` | < 200 ms | ~6 ms |

## Quality gates

- `ruff check` → 0 violations.
- `pytest tests/unit/` → all green; coverage floor 78% (project), 82% target.
- Sacred invariants §1–§15 enforced (see `tests/unit/test_invariants.py`).

## Graceful-degradation guarantees

- `AEGIS_DISABLE_OLLAMA=1` → all agents produce heuristic verdicts, no exception.
- Redis offline → stream publishes fail silently with a log, no crash.
- Postgres offline → dashboard returns 503; autonomous loop retries.
- Optional OSS libs (River, FAISS, Evidently, MLflow, NetworkX) absent → no-op fallbacks.
