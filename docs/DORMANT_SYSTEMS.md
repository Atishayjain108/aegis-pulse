# Dormant & Roadmap Systems

**Verified 2026-06-18 (PROJECT OMEGA remediation).** This file is the single
source of truth for which subsystems are *built but not wired into a live
decision/report*. A system listed here MUST NOT be presented in docs, dashboards,
or status reports as a working capability. Each entry states exactly what would
make it live.

The verification method: grep every caller across `src/`, `aegis-phase4/`, CLI,
and API routes, separating production callers from test-only callers, and
classifying each as **DECISION-READER** (read into a runtime decision),
**REPORT-ONLY** (read only into CLI/audit reports), **WRITE-ONLY** (written in
prod, never read), or **DEAD** (no production caller at all).

## Quarantined / Roadmap

| System | File | Status | Why dormant | To make it live |
|--------|------|--------|-------------|-----------------|
| **MarketMemory** | `src/aegis/memory/market.py` | **DEAD** | `record_epoch()` and `compare_to_history()` have ZERO production callers (test-only). No epochs are ever written; nothing reads it. Marked `DORMANT = True`. | Add a scheduled writer recording real market epochs + a reader that feeds a decision or report; then remove the banner + `DORMANT`. |
| **KnowledgeGraph.neighbors() / .discover()** | `src/aegis/memory/graph.py` | **WRITE-ONLY** | Edges ARE written in production via `relate_opportunity()` (`memory/capture.py:120`) as opportunities settle, but the read methods `neighbors`/`discover` are queried only by tests — no decision or report consumes the graph. | Add a production reader (e.g. RealityVerifier or a CLI report) that queries the materialized edges. |
| **EntityMemory (read path)** | `src/aegis/memory/entity.py` | **WRITE-ONLY in prod** | `upsert()` is called by `SupplierIntel.record_verification/record_fulfillment`, but supplier trust is actually read from the `supplier_reliability` table, not from EntityMemory. Only `top_entities()` is read — and only by the `aegis memory entities` CLI report. | Route `EntityMemory.get()` into the supplier-trust decision, or consolidate with `supplier_reliability`. |

## Correction to prior forensic claims (Reality Assault II)

The 2026-06-17 audit asserted **"only Entity Memory is read into a decision"** and
listed OpportunityMemory / SourceMemory as decorative. **Verified false on
2026-06-18:**

- **OpportunityMemory** — **DECISION-READER.** `RealityVerifier._historical_reality()`
  (`memory/verify.py:90`) calls `OpportunityMemory.patterns()`; the realized rate
  feeds the Reality gate (`verify.py:~141`, `reality >= _MIN_REALITY`).
- **SourceMemory** — **DECISION-READER.** `RealityVerifier.assess()`
  (`memory/verify.py:132`) calls `SourceMemory.trust_for()`; the source trust
  feeds the assessed trust score. Also refreshed weekly by the scheduler.
- **EntityMemory** — actually **WRITE-ONLY** in production (the inverse of the
  prior claim) — see table above.

So the memory layer is **less decorative than previously reported**: the Reality
gate genuinely reads Opportunity + Source memory. RealityVerifier itself remains
**advisory** today (its `passed`/scores are surfaced via the `aegis memory verify`
CLI but do not yet gate the agent/alert path) — wiring it to gate a decision is
tracked as a Phase-E item, not claimed as done.

## Capital-outcome loops (DORMANT-UNTIL-REAL-EXECUTION)

Documented at the code level in `aegis-phase4/src/aegis/execute/settlement.py`
(`settle_daily` docstring). In advisory mode no real orders settle, so
`prediction_outcomes` stays empty and the capital-fed loops (retrain champion
serving, FailureForecaster accuracy, buyer/supplier trust from orders, drawdown
breaker) are dormant by design — not broken. The capital-free signal-outcome loop
(Phase A, `job_settle_claims`) is the live feedback path.
