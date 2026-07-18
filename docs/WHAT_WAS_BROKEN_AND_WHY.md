# What Was Broken And Why (Phase Zero Remediation)

Plain-English explanation of the four critical problems the forensic audit found
and exactly what was changed to fix them. Written for a total beginner — no ML
background needed.

---

## FIX-1 — The retraining pipeline was cheating (target leakage)

**What it pretended to do:** "We retrained the model on past trades and it got
smarter — look, 99% accuracy!"

**What it was actually doing:** It fed the *answer* into the question. To predict
whether a trade would succeed, it used `actual_roi_pct`, `pnl_usd`, and
`units_sold` as inputs — but those numbers only exist *after* the trade is over.
That's like grading a student on an exam after handing them the answer key. The
99% score was meaningless, and the "champion promotion" gate was choosing models
based on noise.

**What the fix does:** `src/aegis/evolve/retrain.py` `_preprocess_outcomes` now
builds the feature matrix from **pre-trade signals only** — the prediction score,
the prediction confidence, and the market snapshot captured *before* we entered
(velocity, signal count, author diversity, sentiment, …). Every post-trade field
is on an explicit blocklist (`_LEAKED_FIELDS`) and can never be used as a feature.

**How to verify:** `tests/unit/evolve/test_retrain.py::TestPreprocessOutcomes`
passes, and the feature matrix no longer contains any realized-outcome column.
A new JSONB column `feature_snapshot` (migration `0013`) stores the pre-trade
snapshot at execution time.

---

## FIX-2 — The "learning" policy wasn't learning (fake REINFORCE)

**What it pretended to do:** "A REINFORCE agent learns which pricing levers make
money."

**What it was actually doing:** On a profitable trade it multiplied *all four*
weights by the same number, then rescaled them to sum to 1. Multiplying
everything by the same number and then renormalizing cancels out — the weights
barely moved, and the system never figured out *which* lever actually caused the
profit.

**What the fix does:** `src/aegis/evolve/rl_policy.py` now contains a real
`LinUCBPricingPolicy` — a contextual bandit (LinUCB, Li et al. 2010). Each
"arm" is a pricing strategy; the policy attributes the realized margin **only to
the arm it actually played** and provably balances trying new things vs.
exploiting what works. The old `OnlinePricingPolicy` is kept but marked
DEPRECATED. State persists to `lin_ucb_state` (migration `0014`).

**How to verify:** `tests/unit/evolve/test_lin_ucb_learning.py` — in particular
`test_lin_ucb_actually_learns` rewards arm 2 for 60 rounds and asserts the policy
then picks arm 2 the majority of the time, and `test_reward_attributed_only_to_played_arm`
proves an update to one arm leaves the others untouched.

---

## FIX-3 — Drift detection compared against a made-up baseline

**What it pretended to do:** "The system notices when the market changes and
corrects itself."

**What it was actually doing:** It compared live data against `zeros(24)` and
`ones(24)` — arbitrary numbers with no connection to where the model was actually
trained. Almost anything looked like "drift", so the auto-rollback and killswitch
could fire on a meaningless signal.

**What the fix does:** `src/aegis/evolve/drift.py` `_initialize_baseline` now
loads the **real** per-feature mean and standard deviation that the champion
model was trained on (stored on `model_candidates` at promotion time, migration
`0013`). Drift is measured against that real distribution. When no champion
exists yet (cold start), it falls back to a neutral prior and sets
`_baseline_initialized = False` plus logs a warning, so operators know the signal
is decorative until the first real model is promoted.

**How to verify:** `tests/unit/evolve/test_drift.py` passes; promotion in
`retrain.py` now writes `training_feature_mean` / `training_feature_std`.

---

## FIX-4 — The supplier/buyer pipeline was fictional

**What it pretended to do:** "Found a supplier, verified the margin, created an
execution plan."

**What it was actually doing:** It posted orders to Printful with a hardcoded
product id `1` ("generic T-shirt") and a fake recipient (`address1: "TBD"`,
`zip: "00000"`). Unit cost was invented as `expected_margin × 2`. No real money
could ever flow, and every margin number on top of it was fiction.

**What the fix does:**
- `src/aegis/fulfillment/printful.py` removes the mock product id and placeholder
  address. It adds real catalog methods — `find_matching_product`,
  `get_real_cost` (real `/orders/estimate-costs`), `verify_inventory` — and
  **refuses to place an order** unless given a concrete variant id and a real
  recipient (a `_is_real_recipient` guard rejects "TBD"/"00000").
- `aegis-phase4/src/aegis/execute/engine.py` adds `_get_verified_unit_cost`,
  which gets a real cost from Printful (then CJ Dropshipping) and returns `None`
  when no supplier can be verified. In staging/live mode, a plan with no verified
  supplier is **blocked at zero quantity** (`PlanStatus.NO_VERIFIED_SUPPLIER`)
  instead of being sized on a fabricated constant. Advisory mode still produces a
  clearly-labelled paper estimate (nothing is placed).

**How to verify:** `tests/unit/fulfillment/test_real_supplier.py` proves orders
are refused without a real variant/recipient, and the Phase 4 engine suite
(`aegis-phase4/tests/unit/execute/test_engine.py`) stays green.

---

## Scope note

This document covers the four **Phase Zero** critical-bug fixes that were
implemented and verified (full evolve suite 195 passed, Phase 4 execute 183
passed, new supplier + LinUCB tests green, ruff clean). The broader OMEGA v3
program — the standalone `src/aegis/training/` XGBoost/LightGBM pipeline, the
`src/aegis/intelligence/` chain-of-thought engine, Platt-scaling calibration, and
the new dashboard panels — is a separate, larger build that depends on training
data and infrastructure not yet present, and is **not** included in this pass.
