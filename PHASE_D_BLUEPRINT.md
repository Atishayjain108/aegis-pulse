# PROJECT OMEGA — PHASE D BLUEPRINT
## AEGIS Prime Execution Intelligence Protocol

> **Status:** Discipline phase (Rule 11). No implementation until this document is
> reviewed. Every feature below is gated by Rule 12 (Reality Gate): if it cannot
> name the real decision it improves and how reality verifies it, it is not built.
>
> **Objective:** Execution *Intelligence*, NOT autonomous execution. AEGIS must
> *observe, predict, verify, remember, learn, plan* about how execution succeeds
> and fails — it must never place an order on its own as a result of Phase D.

Date: 2026-06-16

---

## 1. CURRENT STATE ASSESSMENT

Phase D does **not** start from zero. The relevant substrate already exists and is verified:

| Capability Phase D needs | Already exists | Location | Reuse verdict |
|---|---|---|---|
| Entity memory w/ trust from outcomes | ✅ `EntityMemory` (`EntityKind.SUPPLIER`, `MARKETPLACE`) | `src/aegis/memory/entity.py` | **Reuse for supplier/buyer identity + trust** |
| Failure knowledge + categories | ✅ `FailureMemory`, `FailureCategory` | `src/aegis/memory/failure.py`, `taxonomy.py` | **Extend with execution failure categories** |
| Calibrated trust scoring | ✅ `trust/` (scores, calibrator) | `src/aegis/trust/` | **Reuse for supplier/buyer trust calibration** |
| Real supplier verification | ✅ `_get_verified_unit_cost`, `NO_VERIFIED_SUPPLIER` | `aegis-phase4/.../execute/engine.py` | **Reuse — this is the reality anchor for Rule 1/3** |
| Real inventory/cost check | ✅ `PrintfulClient.verify_inventory/get_real_cost` | `src/aegis/fulfillment/printful.py` | **Reuse as supplier reliability evidence** |
| Risk gates (margin/loss/confidence/compliance) | ✅ `risk/gates.py` `GateChain` | `aegis-phase4/.../execute/risk/` | **Reuse as components of the Risk Engine (Rule 6)** |
| Settlement / PnL reconciliation | ✅ `SettlementManager` | `aegis-phase4/.../execute/settlement.py` | **Reuse as execution-outcome source** |
| Self-audit reporting | ✅ `memory/report.py` | `src/aegis/memory/report.py` | **Extend with execution-focused weekly report (Rule 10)** |
| Cross-market arbitrage + "why it exists" data | ✅ `geo/arbitrage.py` | `src/aegis/geo/` | **Reuse for arbitrage intelligence (Rule 7)** |
| Reality verifier (advisory gate) | ✅ `RealityVerifier` | `src/aegis/memory/verify.py` | **Pattern to copy for execution survivability** |

**What does NOT exist yet (the genuine Phase D gap):**
- No durable **execution memory** linking *plan → outcome → cost → assumption → failure*.
  Settlement records PnL but does not capture execution assumptions, risk, or failure mode.
- No **buyer** intelligence at all (we have supplier verification but never model the demand side).
- No **execution simulation** producing a *Survivability Score* before recommending.
- No **separated metric vector** (Rule 6): today risk/confidence/trust are entangled in ad-hoc places.
- No **execution failure forecasting** with stored predictions measured against outcomes (Rule 8).
- No **per-recommendation execution explanation** (why now / why this supplier / why this buyer).

---

## 2. GAP ANALYSIS (mapped to the 12 rules)

| Rule | Requirement | Gap | Build vs Reuse |
|---|---|---|---|
| 1 Reality First | Never assume supplier/buyer/margin/inventory/demand; else `UNVERIFIED` | Partial — supplier verified, buyer/demand assumed | **Build** an `ExecutionAssumption` ledger + `UNVERIFIED` enum everywhere |
| 2 Execution Knowledge Engine | Store plan/outcome/risk/failure/cost/assumptions | Missing the unified record | **Build** `execution_records` table + `ExecutionMemory` |
| 3 Supplier Intelligence | Identity/trust/reliability/inventory/fulfillment/response history → Supplier Trust Score | Identity+trust exist via Entity; histories missing | **Extend** EntityMemory with supplier reliability rollup |
| 4 Buyer Intelligence | Identity/behavior/demand/order/fulfillment → Buyer Trust Score | Fully missing | **Build** on EntityMemory (add `BUYER` kind) |
| 5 Execution Simulation | Model inventory/supplier/shipping/compliance/payment/demand failure → Survivability Score | Missing | **Build** `ExecutionSimulator` (deterministic, reuses gates + entity trust) |
| 6 Risk Engine | risk / confidence / trust / evidence / execution / survivability — **separated, never merged** | Entangled | **Build** `ExecutionScoreVector` (frozen Pydantic, 6 distinct fields) |
| 7 Arbitrage Intelligence | 6 arb types; why exists / why uncaptured / what destroys it | Partial (geo only) | **Extend** taxonomy + add 3-question rationale to opportunity |
| 8 Failure Prediction | Predict execution/supplier/buyer/logistics/compliance failure; store; measure; learn | Missing | **Build** `FailureForecaster` + stored-prediction → outcome loop (reuse trust calibrator pattern) |
| 9 Decision Explainability | Why now/this opp/this supplier/this buyer/this region/this risk | Missing | **Build** `ExecutionExplanation` generator |
| 10 Self Audit | Weekly: best/worst plans, reliable suppliers/buyers, profitable opps, common failures | Partial (`report.py`) | **Extend** report.py with execution sections |
| 11 Discipline | This document | — | **This file** |
| 12 Reality Gate | Per-feature reality justification | — | Embedded per-feature below |

---

## 3. DEPENDENCY ANALYSIS

```
ExecutionMemory (Rule 2)  ─┬─ depends on → Settlement outcomes (exists)
                           ├─ depends on → ExecutionAssumption ledger (Rule 1, new)
                           └─ feeds → SelfAudit (Rule 10), FailureForecaster (Rule 8)

SupplierIntelligence (Rule 3) ── reuses → EntityMemory + Printful verification (exist)
BuyerIntelligence   (Rule 4) ── reuses → EntityMemory (+ BUYER kind, new)
                                  ⚠ depends on a REAL buyer signal source — see Risk R1

ExecutionSimulator (Rule 5) ── depends on → SupplierIntel + BuyerIntel + risk/gates (exist)
                            └─ produces → survivability field of ExecutionScoreVector

ExecutionScoreVector (Rule 6) ── pure aggregation; depends on all of the above but MERGES NOTHING
FailureForecaster (Rule 8)  ── depends on → ExecutionMemory history + trust calibrator
ExecutionExplanation (Rule 9) ── read-only over the vector + memories
SelfAudit (Rule 10) ── read-only over ExecutionMemory + entity trust
```

Critical path: **Rule 1 ledger → Rule 2 memory → Rules 3/4 intel → Rule 5 sim → Rule 6 vector → Rules 8/9/10.**

---

## 4. RISK ANALYSIS

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| **R1** | **Buyer Intelligence has no real data source.** We scrape signals, not orders/customers. Building buyer trust on synthetic data violates Rule 1 + the project's anti-theater doctrine. | **High** | Model buyer intel as **demand-proxy only**, every field defaults to `UNVERIFIED`; trust score returns `None` until real order data exists. Do NOT fabricate buyers. |
| R2 | Execution simulation could become "architectural theater" — a Monte Carlo that looks scientific but has no calibrated inputs. | High | Survivability inputs must be **measured** (supplier reliability from real verification calls, failure base-rates from settled outcomes), or the component returns `UNVERIFIED` and abstains. |
| R3 | Metric entanglement (Rule 6 violation) creeping back in. | Med | `ExecutionScoreVector` is frozen with 6 independent fields and **no composite**; explanation cites each separately. Add an invariant test. |
| R4 | Coverage floor (78%) — new modules drag it down. | Med | TDD; every new module ships with tests (project precedent: Phase C added ~120 tests). |
| R5 | Scope explosion — 12 rules ≈ many weeks if built monolithically. | High | Stage delivery (Roadmap §6); each stage independently shippable + green. |
| R6 | Accidentally enabling autonomous execution. | High | Phase D writes **memory + advisory scores only**. No new code path calls `engine.execute` in live mode. Invariant test asserts this. |

---

## 5. ROI ANALYSIS (Rule 12 per stage)

| Stage | Real decision improved | Success measured by | Failure measured by | Reality verified by |
|---|---|---|---|---|
| S1 Execution memory + assumptions | "Did our recommendation survive contact with execution?" | settled execution_records grow; assumptions logged | unlogged outcomes | join to real Settlement PnL |
| S2 Supplier intel | "Which supplier do we trust to fulfill?" | supplier trust tracks real fulfillment success | trust diverges from outcomes | Printful verify/cost API calls |
| S3 Buyer intel (proxy) | "Is there verified demand, or are we guessing?" | demand marked verified vs UNVERIFIED honestly | fabricated buyers | signal-derived demand only |
| S4 Simulation + survivability | "Will this plan likely fail before it profits?" | survivability predicts realized failures | sim says safe, reality fails | back-test sim vs settled outcomes |
| S5 Risk vector + forecaster + explain | "Why this, why now, at what separated risk?" | forecast Brier skill > 0 OOS | forecasts no better than base rate | stored-prediction vs outcome loop |
| S6 Self-audit | "What are our best/worst plans & suppliers?" | weekly report generated from real records | empty/stale report | reads execution_records |

If any stage cannot show the "reality verified by" column with real data, it ships as **advisory + UNVERIFIED** rather than as a confident score.

---

## 6. EXECUTION ROADMAP

Each stage is independently shippable, fully tested, leaves the suite green at ≥78% coverage,
0 ruff violations, and adds an `aegis exec ...` CLI surface + dashboard read-only panel.

- **S1 — Execution Knowledge Engine (Rules 1, 2).** New `src/aegis/execution_intel/` package +
  migration `0021_execution_intel.sql` (`execution_records`, `execution_assumptions`).
  `ExecutionMemory.record_plan/record_outcome`; assumptions carry a `verified: bool` + `UNVERIFIED` sentinel.
  Hook into `SettlementManager` (best-effort, never raises).
- **S2 — Supplier Intelligence (Rule 3).** `SupplierIntel` over EntityMemory; reliability rollup from
  real Printful verify/cost calls + settled fulfillment outcomes; `SupplierTrustScore`.
- **S3 — Buyer Intelligence (Rule 4). ✅ DONE.** `BuyerIntel` + `buyer_demand` (migration 0022);
  demand-proxy keyed by (region, category) — no fake buyers; `buyer_trust=None`/UNVERIFIED until
  real `record_order` data exists; `demand_is_proxy` always True (honest about the gap).
- **S4 — Execution Simulation (Rule 5). ✅ DONE.** `ExecutionSimulator` → `SurvivabilityScore`;
  deterministic (NOT Monte Carlo); per-mode survival from measured supplier trust + learned failure
  base rates (`ExecutionMemory.failure_base_rates`); **abstains** (`overall=None`) when the critical
  supplier input is unverified; each mode flagged `measured`/`proxy`/`assumed`/`unverified` in `basis`.
- **S5 — Risk Engine + Failure Forecaster + Explainability (Rules 6, 8, 9). ✅ DONE.**
  `ExecutionScoreVector` (6 SEPARATED fields: risk/confidence/trust/evidence/execution/survivability —
  NO composite; invariant test asserts no merged field); `ScoreVectorBuilder` assembles each axis
  independently (None=UNVERIFIED). `FailureForecaster` (migration 0023 `execution_forecasts`):
  forecast = 1−survival, `record_forecast` stores it, `measure_accuracy` scores stored forecasts vs
  settled outcomes (Brier skill, no look-ahead). `explain_plan` answers the six Rule 9 questions,
  citing measured vs UNVERIFIED.
- **S6 — Self Audit (Rule 10) + arbitrage rationale (Rule 7). ✅ DONE.** `ExecutionAuditor.weekly_report`
  (best/worst plans, most-reliable suppliers, most-demanded markets, most profitable, common failure
  causes — all from real settled records, empty when no data). `arbitrage_rationale(type)` returns the
  three questions (why exists / why uncaptured / what destroys it) as ADVISORY UNVERIFIED hypotheses.

**PHASE D COMPLETE (S1–S6).** New package `src/aegis/execution_intel/` (12 modules), migrations
0021/0022/0023, `aegis exec` CLI (9 subcommands), settlement bridge, 45 unit tests. Doctrine held
throughout: UNVERIFIED never guessed (R1/R2); metrics never merged (R6); no code path triggers live
execution (R6) — this is execution *intelligence*, not autonomous execution.

**Invariants enforced by tests throughout:** no Phase D code triggers live execution (R6);
`ExecutionScoreVector` exposes no merged composite (R3); every unverifiable field is `UNVERIFIED`, never faked (R1, R2).

---

## OPEN DECISION FOR REVIEW

Rule 11 says "only then implement." Two questions gate the build:
1. **Buyer Intelligence (R1):** we have no real order/customer data. Proceed with an honest
   *demand-proxy, UNVERIFIED-by-default* buyer model, or defer Rule 4 until a real order source exists?
2. **Sequencing:** build all six stages now, or land S1+S2 (highest-reality, fully grounded) first and review?
