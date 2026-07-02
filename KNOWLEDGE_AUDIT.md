# KNOWLEDGE_AUDIT.md — PROJECT OMEGA Phase C

**Post-implementation audit of the AEGIS Prime Knowledge Expansion Protocol.**
Date: 2026-06-15. Companion to `PHASE_C_BLUEPRINT.md`.

This traces each of the 8 primary goals to what was actually built and how it is
verified. The governing rule throughout: **every stored fact derives from a
settled ground-truth outcome or a pure deterministic function — knowledge is
never invented** (Rule 1 / Rule 12).

---

## Goal → implementation → verification

| # | Goal | Module / table | Falsifiable because… |
|---|------|----------------|----------------------|
| 1 | **Opportunity Memory** (Rule 2) | `memory/opportunity.py`, `opportunities` (mig 0018) | Rows only created from settled `signal_outcomes`; `patterns()` reports realized vs failed counts per (type, category). |
| 2 | **Failure Memory** (Rule 5) | `memory/failure.py`, `failures` (0018) | A failure row exists **only** for a settled-incorrect outcome; `classify_failure` is a pure function (tested across every category). |
| 3 | **Source Memory** (Rule 4) | `memory/source.py`, `source_profiles` (0019) | trust reuses the Phase B `compute_trust` over settled (p,y); reliability = settled-correct rate; freshness = real signal recency. `manipulation_risk`/`per_category_accuracy` left NULL (honestly unmeasured, not faked). |
| 4 | **Reality Verification** (Rule 8) | `memory/verify.py` | Produces 4 scores in [0,1] (evidence/trust/reality/unknowns) + advisory `passed`. evidence/unknowns pure; trust ← Source Memory; reality ← historical realized-rate. |
| 5 | **Entity Memory** (Rule 3) | `memory/entity.py`, `entities`+`entity_outcomes` (0020) | trust recomputed as realized-rate over a settled entity_outcomes; upsert idempotent on (kind, name). |
| 6 | **Market Memory** (Rule 6) | `memory/market.py`, `market_epochs` (0020) | `compare_to_history` returns current−historical-mean delta; "no_history" when none — never a fabricated baseline. |
| 7 | **Knowledge Graph** (Rule 7) | `memory/graph.py`, `knowledge_edges` (0020) | SQL-backed edges with cumulative weight/evidence_count; `relate_opportunity` materialises trend→opportunity→source edges automatically at settlement. No graph-DB service. |
| 8 | **Self-Auditing Intelligence** (Rule 10) | `memory/report.py` + scheduler `job_knowledge_refresh` | Weekly report ranks best/worst opportunities, source leaderboard, recurring failures — pure aggregation over accumulated knowledge. |

---

## Stage rollout (as shipped)

- **Stage 1 — capture foundation:** Opportunity + Failure memory + backfill of the 247 settled outcomes.
- **Stage 2 — live capture:** `MemoryCapture` hooks in `SignalOutcomeSettler` (hourly) and `ClaimEmitter`; opportunities accumulate autonomously. `OpportunityMemory.settle()` UPSERT avoids the pending-stuck trap.
- **Stage 3 — use knowledge:** Source Memory + RealityVerifier (advisory scores).
- **Stage 4 — complete + validate:** Entity/Market/Graph memory, Self-Audit report, scheduler `job_knowledge_refresh` (Sun 04:00 UTC), and the **RealityBacktester** — the OOS validator.

## The headline validation (Rule 12)

`RealityBacktester.run()` buckets settled opportunities by stored `evidence_score`
and reports realized-rate per bucket plus the **high−low separation**. A positive
separation is the falsifiable evidence that the Reality layer adds signal. The
verifier's evidence proxy is now persisted on every opportunity (at claim and at
settlement), so the backtest runs on real data as outcomes accrue.

> **Gate discipline:** the `passed` flag and reality scores are **advisory** — they
> are attached to opportunities, not used to flip any verdict. Wiring them into the
> decision path (Stage "consume") remains gated on the backtest showing a
> persistent positive separation out-of-sample.

## Engineering facts

- New package `src/aegis/memory/` (regular subpackage, graceful-degradation doctrine: every write best-effort, `AEGIS_MEMORY_ENABLED` flag).
- Migrations `0018`/`0019`/`0020` — additive only, RLS, reversible by dropping tables.
- CLI: `aegis memory {backfill, patterns, failures, sources, verify, entities, audit, backtest}`.
- Tests: `tests/unit/memory/` — memory package at 97–100% line coverage; 0 ruff violations.
- Not yet built (explicitly deferred, not theater): per-category source accuracy, manipulation-risk scoring, entity auto-extraction from signal text, and the decision-path "consume" wiring (gated on the backtest).
