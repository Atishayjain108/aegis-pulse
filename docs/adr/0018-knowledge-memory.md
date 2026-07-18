# ADR 0018 — Knowledge Memory Layer (PROJECT OMEGA Phase C)

**Status:** Accepted — 2026-06-15
**Context:** OMEGA Phase C "Knowledge Expansion Protocol"

## Context

After Phases A/B/C-predictor, AEGIS could settle predictions to ground truth and
calibrate confidence, but it was **stateless across time**: a trend nailed last
month, a source that repeatedly lied, and the *reason* a prediction failed were
all forgotten. The mandate: make decision quality **compound** by remembering,
verifying, scoring, and learning from outcomes — without adding new predictive
models, new scraping domains, or architecture theater.

## Decision

Add one new subpackage `src/aegis/memory/` plus additive migrations 0018–0020,
implementing eight knowledge stores, all derived from settled ground truth:

1. **Opportunity Memory** (`opportunities`) — durable ledger of every opportunity and its outcome.
2. **Failure Memory** (`failures`) — root-caused failure knowledge (only for settled-incorrect outcomes).
3. **Source Memory** (`source_profiles`) — per-platform trust/reliability/freshness, reusing the Phase B `compute_trust` engine.
4. **Reality Verification** (`verify.py`) — composes evidence/trust/reality/unknowns scores + an advisory gate.
5. **Entity Memory** (`entities`, `entity_outcomes`) — long-lived entity trust from settled outcomes.
6. **Market Memory** (`market_epochs`) — now-vs-history demand comparison.
7. **Knowledge Graph** (`knowledge_edges`) — SQL-backed relationship store (no graph-DB service; NetworkX optional in-process).
8. **Self-Audit** (`report.py`) — weekly best/worst report; autonomous via scheduler `job_knowledge_refresh`.

## Key choices

- **Falsifiability over completeness (Rule 1/12):** unmeasurable fields
  (`manipulation_risk`, per-category accuracy, entity auto-extraction) are left
  NULL/deferred rather than faked.
- **Best-effort, flag-guarded:** every write is `try/except` + `AEGIS_MEMORY_ENABLED`;
  memory failure never breaks the prediction or settlement path.
- **Reuse over rebuild:** Source/Trust reuse `aegis.trust`; capture hooks attach to
  the existing `SignalOutcomeSettler` and `ClaimEmitter`; Market reads existing data.
- **Advisory, not deciding:** RealityVerifier scores are attached, never used to flip
  a verdict, until `RealityBacktester` shows a persistent positive OOS separation.

## Consequences

- Migrations are additive and reversible (drop the new tables).
- Knowledge accumulates autonomously (hourly settlement hook + weekly refresh job).
- The decision-path "consume" stage and richer source/entity signals are explicit
  follow-ups, gated on the backtest. See `KNOWLEDGE_AUDIT.md` and `PHASE_C_BLUEPRINT.md`.
