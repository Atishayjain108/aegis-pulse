# PROJECT OMEGA — PHASE E

## AEGIS PRIME: Decision Authority & Evidence Utilization Report

**Date:** 2026-06-18
**Method:** Live database only (`aegis` on `aegis-postgres:5433`). Actual row counts, actual
outcome ledgers. No synthetic data. No simulated results. Where N is insufficient, the verdict
is `INSUFFICIENT_EVIDENCE` — not an invented statistic.

---

## RULE 11 — CURRENT STATE ASSESSMENT

### Live ledger inventory (row counts, measured 2026-06-18)

| Ledger | Rows | Usable for authority? |
|---|---:|---|
| `signals` | 3022 | raw input, not outcomes |
| `signal_outcomes` | **494** | **only ledger with N≥30 settled labels** |
| `opportunities` | 494 | mirror of signal_outcomes |
| `failures` | 211 | mirror (incorrect outcomes) |
| `prediction_outcomes` | 11 | below threshold |
| `predictions` | 0 | empty |
| `trust_scores` | 5 | source/model trust, has n_outcomes |
| `source_profiles` | 6 | duplicates trust_scores (inconsistent) |
| `calibration_snapshots` / `calibration_maps` | 1 / 1 | single fit |
| `entity_outcomes` | 0 | empty |
| `execution_records` | **0** | empty |
| `execution_forecasts` | **0** | empty |
| `execution_assumptions` | **0** | empty |
| `supplier_reliability` | **0** | empty |
| `buyer_demand` | **0** | empty |
| `knowledge_edges` | 0 | empty |
| `entities` | 0 | empty |
| `model_candidates` / `drift_snapshots` | 0 / 0 | empty |

**Headline:** the entire execution side of the system (Phase D) has produced **zero realized
outcomes**. The only ledger carrying labeled ground truth at usable scale is `signal_outcomes`,
and **100% of it is backfill** (247 `heuristic_backfill` + 247 reconstructed `backfill`). There
is no live forward-emitted-then-settled prediction history yet.

---

## RULE 4/5/8 — BACKTESTS ON THE ONE LEDGER THAT EXISTS

`signal_outcomes`, settled subset: **283 correct / 211 incorrect → base-rate accuracy 57.3%.**

### Calibration (confidence as a probability)

| Metric | Value | Reading |
|---|---:|---|
| Brier (model) | 0.2481 | — |
| Brier (base rate) | 0.2447 | — |
| **Brier skill score** | **−0.014** | **worse than predicting the base rate** |

Confidence is **not calibrated**. The `conf=1.000` bin (n=35) resolves correct only 77.1% —
systematic overconfidence. This reproduces `CONFIDENCE_AUDIT.md`.

### Lift (confidence as a rank/triage signal)

| Conf bin | mean conf | n | accuracy |
|---|---:|---:|---:|
| 0.50–0.60 | 0.510 | 369 | 0.539 |
| 0.60–0.70 | 0.640 | 66 | 0.606 |
| 0.70–0.80 | 0.726 | 15 | 0.800 |
| 0.90–1.0 | 1.000 | 35 | 0.771 |

Pearson corr (non-degenerate subset, conf≠0.5, n=237): **+0.150**.

**Reading:** confidence has *weak directional lift* (low-conf 54% → high-conf 77%) but is
useless as a probability. It can triage/rank; it cannot price risk. And the data underneath is
degenerate — 251 of 494 rows sit at exactly conf=0.500.

### Source contribution (RULE 6)

From `trust_scores` (source rows, with realized n_outcomes):

| Source | accuracy | n_outcomes | Verdict |
|---|---:|---:|---|
| `hacker_news` | **0.645** | 31 | creates value (thin N) |
| `reddit` | 0.497 | 167 | **noise (coin-flip)** |
| `google_news` | 0.497 | 177 | **noise** |
| `bing_news` | 0.495 | 101 | **noise** |
| model `heuristic` | 0.358 | 53 | **anti-signal; Brier skill −1.57** |

This is the single clearest, most actionable finding in Phase E: three of the four highest-volume
sources resolve at coin-flip, and the heuristic model's stated confidence is *negatively*
correlated with truth.

---

## RULE 2 — DECISION AUTHORITY FRAMEWORK (per subsystem)

| Subsystem | Consumed? | Measured? | Predictive? | Improves outcomes? | **Authority** |
|---|---|---|---|---|---|
| Calibration | yes (runner gate) | yes | weakly (lift +0.15) | unproven (Brier −0.014) | **ADVISORY** |
| Trust (model) | yes | yes (n=53) | negatively | no | **REJECT as signal** |
| Source Memory | partial | yes (n=31–177) | yes (HN 0.645 vs 0.497) | plausibly, thin N | **ADVISORY → triage candidate** |
| Opportunity Memory | yes | mirror of outcomes | n/a | n/a | **ADVISORY** |
| Failure Memory | yes | yes (211) | descriptive only | unproven | **ADVISORY** |
| Entity Memory | no | **N=0** | — | — | **INSUFFICIENT_EVIDENCE** |
| RealityVerifier | advisory | **no realized GT** | unvalidated | unproven | **INSUFFICIENT_EVIDENCE** |
| Supplier Intelligence | no | **N=0** | — | — | **INSUFFICIENT_EVIDENCE** |
| Buyer Intelligence | no | **N=0** | — | — | **INSUFFICIENT_EVIDENCE** |
| FailureForecaster | no | **N=0 exec** | — | — | **INSUFFICIENT_EVIDENCE** |
| Execution Intelligence | no | **N=0** | — | — | **INSUFFICIENT_EVIDENCE** |
| Knowledge Graph | no | **N=0 edges** | — | — | **INSUFFICIENT_EVIDENCE** |

---

## RULE 9 — SELF-CRITIQUE (why this report could be wrong)

- **The 494 outcomes are backfill, not live.** Observed values are real signal history, but the
  predictions/confidences were reconstructed by a heuristic, not emitted live and then settled.
  Every lift number above is therefore *provisional* and may not survive on live forward data.
- **hacker_news n=31 is at the floor.** A 0.645 vs 0.497 gap is suggestive, not significant; a
  proportion test on n=31 vs base rate is borderline. Do not over-trust it.
- **trust_scores (5 rows) and source_profiles (6 rows) disagree** on per-source trust and
  n_outcomes — there are two competing source ledgers and they are not reconciled.
- **Missing variable:** category/horizon stratification. Source accuracy may be confounded by
  *what each source covers*, not source quality.

---

## RULE 12 — VERDICT

> Phase E succeeds if at least one advisory subsystem earns statistical decision authority, **OR**
> if Phase E proves no subsystem currently deserves authority. Both are acceptable.

**Outcome: the second.** On live data, **no subsystem has earned ENTER/REJECT/HOLD authority.**

- Every execution-side subsystem (Supplier, Buyer, FailureForecaster, Execution Intelligence,
  Knowledge Graph, Entity Memory) is `INSUFFICIENT_EVIDENCE` by definition — **N=0 realized
  outcomes.** Authority cannot be granted to a system that has never produced a settled result.
- Calibration/confidence: **ADVISORY only.** Negative Brier skill forbids probability authority;
  weak lift permits use as a *triage rank*, but only provisionally on backfill data.
- The one subsystem with genuine measured lift — **Source Memory** — earns a **provisional
  triage recommendation**, not full authority: down-weight `reddit`/`google_news`/`bing_news`
  (coin-flip) and the `heuristic` model's confidence (anti-signal), pending N≥100 per source of
  *live* (non-backfill) outcomes.

**Reality is more important than progress. No authority is fabricated here.**

---

## RULE 11 — ROLLOUT (what is justified; RULE 1 = zero theater)

No new subsystem, agent, database, or dashboard is justified — **no statistical evidence proves
necessity.** The single binding constraint on all twelve subsystems is the same: **there is no
live forward outcome data.** The only work that unlocks Phase E authority is closing that gap:

1. **Stop backfilling, start emitting.** Confirm the live claim→settle loop (signal_outcomes
   reality loop) is actually running forward and tag live rows distinctly from backfill, so a
   clean live-only backtest becomes possible.
2. **Reconcile `trust_scores` vs `source_profiles`** into one source ledger before consuming
   either for decisions.
3. **Provisional source triage (advisory):** apply the measured source ranking as a *weight*,
   logged and reversible, never as a hard gate, until live N≥100/source confirms it.
4. **Re-run this exact backtest** when `signal_outcomes` holds ≥200 *live* (non-backfill) settled
   rows and any `execution_records` exist. Until then every execution subsystem stays
   `INSUFFICIENT_EVIDENCE`.

### Phase E Scorecard

| Axis | State | Evidence |
|---|---|---|
| Reality Readiness | partial | 494 outcomes exist, but 100% backfill |
| Decision Quality | unproven | Brier skill −0.014 (≈ base rate) |
| Trustworthiness | measured-poor | heuristic Brier skill −1.57 |
| Execution Readiness | **none** | 0 execution_records |
| Learning Readiness | partial | loop wired, source lift detectable (thin) |
| Autonomy Readiness | **not earned** | no subsystem cleared for authority |
