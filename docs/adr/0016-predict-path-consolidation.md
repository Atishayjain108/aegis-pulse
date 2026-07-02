# ADR 0016 — Predict Path Consolidation (CONN-3)

- **Status**: Accepted
- **Date**: 2026-06-11
- **Audit ID**: CONN-3 (AEGIS_AUDIT.md)

## Context

Two paths existed to Phase 3 (Predictive Apex) inference:

1. **In-process bridge** — `aegis.agents_phase3_glue.bridge` constructs an
   `InferenceRunner` inside the agent process and calls it directly.
2. **HTTP service** — the FastAPI server on `:8100`
   (`aegis.predict.serving`) exposing `/predict` and `/predict/batch`.

Having both reachable from the agent graph risked split-brain behaviour:
different latency envelopes, different failure modes, and double
maintenance of the result→decision mapping. The audit (CONN-3, P1) required
choosing exactly one path for agents.

## Decision

**Agents use the in-process bridge exclusively.**

- `scout` and `sentinel` call `enrich_scout_decision` /
  `enrich_sentinel_decision`, which delegate to `InferenceRunner.run()`
  in-process. No `httpx` call to `:8100` exists anywhere in
  `src/aegis/agents/` or `src/aegis/agents_phase3_glue/` (verified by the
  Pass 0 autopsy, 2026-06-11).
- **HTTP `:8100` stays alive for external consumers**: the dashboard
  (`/api/predictions/recent`), direct API callers, and load tests.
- A debugging override exists: `AEGIS_PREDICT_FORCE_HTTP=true` routes the
  bridge enrichment through `POST {AEGIS_PREDICT_API_URL}/predict` instead.
  It is for parity verification only. Any HTTP failure logs a warning and
  falls back to the in-process runner — the agent pipeline never fails
  because the override is set and the service is down.

## Rationale

- The in-process path is the only one that satisfies Phase 3's p99 < 12 ms
  heuristic-floor latency budget; an HTTP hop adds serialization +
  network + queueing that can exceed the entire budget.
- The runner is heuristic-first and dependency-light — embedding it in the
  agent process costs little memory and removes a whole network failure
  class from the critical scoring path.
- The full audit trail (`InferenceResult.audit`, `causal`) is only
  available in-process; the HTTP envelope carries summaries.

## Consequences

- The agent container must have `aegis.predict` importable (it does — same
  package).
- Model hot-reload for agents follows in-process `ModelStore` activation,
  not service redeploys.
- The HTTP override path duck-types `InferenceResult` via
  `_HttpInferenceResult` (empty `causal`, `audit=None`) — acceptable for a
  debug path, documented in `bridge.py`.
