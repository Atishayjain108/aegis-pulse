# CONFIDENCE AUDIT — Why 98% confidence produced 38% realized success

PROJECT OMEGA Phase C. Read-only forensic trace of the heuristic predictor's
confidence, followed by the bug fixes and calibration that make it truthful.
Every number below is measured on the 247+ real `signal_outcomes`, not assumed.

---

## 1. Root cause analysis

Confidence flows: **signals → `_summarise_window` → `_heuristic_stage` → confidence
scalar → `_stage_to_class_probs` → p_breakout/p_peak/p_decline → emitter confidence**.

Three independent defects compounded to turn ~98% stated confidence into ~38%
realized success:

| # | Defect | Where | Effect |
|---|--------|-------|--------|
| R1 | **Confidence = evidence *volume*, not event probability** | `heuristic_predict` lines 379–424 | `sig_conf` saturates (+0.40) at ~50 signals, `auth_conf` (+0.20) at ~50 authors. Almost every active trend pins near the ceiling → near-constant confidence with **no discrimination**. |
| R2 | **Identity map `p = c`** | `_stage_to_class_probs` | For BREAKOUT/PEAK the class probability *equalled* the evidence scalar. The 54.7% base rate was never used; nothing anchored the probability to reality. |
| R3 | **Emitter stored `1 − p_breakout` as confidence** | Phase B `claim_emitter` | Most trends are EMERGING with tiny `p_breakout`, so "fall" claims recorded confidence ≈ `1 − small ≈ 0.96` — a pure artifact. This is the literal source of the "98%". |

Plus two correctness bugs that silently corrupted the sample:

- **B1 — class probabilities exceed 1.0:** the PEAK tuple `(0.20c, c, 0.20c)` sums
  to `1.40c`; at the 0.75 ceiling that is **1.05 > 1.0**, so the `Prediction`
  validator raised `"class probabilities exceed 1.0"` and **every confident PEAK
  prediction was dropped** — biasing the surviving set toward weaker signals.
- **B2 — `sign_conf` symmetric inflation:** `0.05·(1 + |same_sign − 1.5|·2)` awards
  the same **+0.20** to a unanimously *declining* trend as to a rising one,
  inflating confidence on trends heading down.

## 2. Confidence decomposition

`confidence = clamp(0.20 + sig_conf + auth_conf + sign_conf + momentum + ma_cross + ols, 0.05, 0.75)`

| Term | Range | Saturates at | Verdict |
|------|-------|--------------|---------|
| base | 0.20 | — | floor far above any realized-rate justification |
| `sig_conf` | 0–0.40 | ~50 signals | **dominant; saturates → no discrimination (R1)** |
| `auth_conf` | 0–0.20 | ~50 authors | same pathology |
| `sign_conf` | 0.05–0.20 | unanimous | **symmetric inflation (B2)** |
| momentum/ma/ols | ≤0.13 | — | direction-aware but tiny |

The sum is **evidence strength**, a measure of *how much we know*, not *how likely
the event is*. Conflating the two is the architectural root cause.

## 3. Probability audit (measured, out-of-sample k-fold isotonic)

`scripts/confidence_audit.py`, n=225–247 anchors:

| P(rise) formulation | discrimination | OOS Brier skill | OOS ECE |
|---|---|---|---|
| raw `p_breakout` | −0.029 | +0.005 | 0.076 |
| `p_breakout/(p_b+p_d)` | +0.052 | −0.015 | 0.112 |
| base-rate constant | −0.110 | −0.037 | 0.094 |
| **`1 − p_decline`** | **+0.133** | **+0.018** | 0.113 |

**Finding:** `p_breakout` and the evidence-volume confidence do **not** discriminate
the realized label. Only `1 − p_decline` carries genuine, positive out-of-sample
skill. The truthful predictor must derive P(rise) from `1 − p_decline`, calibrated.

## 4. Threshold audit

- `HEURISTIC_CONFIDENCE_CEILING = 0.75` — high enough that the saturated evidence
  terms routinely pin confidence near it; combined with R2 this becomes a ~0.75
  *probability*. Not lowered (it bounds the scalar, not the inflation); the fix is
  to stop using the scalar as a probability.
- `ACTION_ENTER_PROBABILITY_FLOOR = 0.55`, `ACTION_CONFIDENCE_FLOOR = 0.45` — these
  gate actions on the *inflated* probability, so they fired too readily. They now
  sit downstream of a calibrated probability, so they mean what they say.

## 5. Calibration audit

- **Before:** no calibration applied anywhere. Raw evidence scalar → output.
  Phase B measured ECE ≈ 0.21, Brier skill **−0.22** (worse than guessing the base
  rate).
- **After:** isotonic map fitted on realized `(1 − p_decline, did-rise)` and applied
  at emit/serve time. OOS ECE **0.113**, Brier skill **+0.018**.

## 6. Remediation plan — implemented

| Fix | File | Type |
|-----|------|------|
| Rescale class-prob tuples so `Σ ≤ 1` (PEAK/BREAKOUT/DECLINING) | `models/heuristic.py` `_stage_to_class_probs` | **bug B1** — predictions no longer dropped |
| Reframe `sign_conf` as bounded consistency ∈ [0, 0.10] | `models/heuristic.py` | **bug B2** — no more declining-trend inflation |
| `rise_probability = 1 − p_decline`; `Calibrator` isotonic fit/persist/apply | `trust/calibrator.py` | **truthfulness** |
| Emitter stores **calibrated P(rise)**, not `1 − p_breakout`; raw kept for refit | `trust/claim_emitter.py` | **bug R3** |
| `calibration_maps` table + load/save | `migrations/0017`, `trust/store.py` | persistence |
| `aegis trust fit-calibration` (+ `emit-claims` loads the map) | `trust/cli.py` | ops |

### Measured improvement (success criteria)

| Criterion | Target | Result |
|-----------|--------|--------|
| Positive Brier Skill Score | > 0 | **+0.018** ✓ |
| Lift above 54.7% baseline | beat base rate | discrimination **+0.133**, BSS>0 ✓ |
| Calibrated confidence | low ECE | **0.21 → 0.113** OOS ✓ |
| Improvement over Phase B | measurable | **+0.236 BSS** ✓ |

### Honest limitations

The positive skill is **small** (+0.018). The self-supervised "signal_count rises
in 72h" label is genuinely noisy, and the heuristic's directional information is
weak. This is the *truthful* ceiling of the current predictor — the audit's value
is that confidence now *reflects* that, instead of claiming 98%. Larger gains
require better directional features, which is out of scope (no new features by
mandate). The calibration map must be **refit periodically** as outcomes accrue.
