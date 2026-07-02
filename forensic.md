# AEGIS PULSE — FORENSIC AUDIT REPORT

# ⟢ REMEDIATION PASS — PROJECT OMEGA (Phase E prerequisite) — 2026-06-17 (evening)

**Mode:** Remediation (Block 2). Verified every finding against current source
before acting; cited file:line. Did NOT close capital-dependent loops (no real
execution exists) — labeled them DORMANT instead of faking them. 78% floor + 0
ruff held on all touched files.

| # | Item | Before | After | Files |
|---|------|--------|-------|-------|
| E1 | Redis hardcode in health job | `redis://localhost:6380/0` literal | `_cfg.redis_url_str` (config-driven) | `scheduler/autonomous.py` |
| E2 | Hardcoded scrape topics | module-level `TOPICS` list | `_topics()` reads `AEGIS_AUTONOMOUS_TOPICS` (comma-sep), default fallback | `scheduler/autonomous.py` |
| E3 | Scheduler orphaned (nothing starts it) | no compose service | added profile-gated `autonomous` service (`docker compose --profile autonomous up -d autonomous`) | `docker-compose.yml` |
| A1 | Calibration loop DORMANT (no emitter job) | emit/refit only via manual CLI | added scheduled `job_emit_claims` (hourly, loads fitted map) + `job_refit_calibration` (daily 01:30 UTC) → loop now self-runs | `scheduler/autonomous.py` |
| C1 | CJ orders use mock SKU + "TBD"/"00000" address | `_MOCK_PRODUCT_SKU`, hardcoded TBD recipient | `create_orders` REQUIRES real `product_vid` + complete `recipient`; refuses (returns `[]`) otherwise — no synthetic fallback exists | `fulfillment/cjdropshipping.py` |
| C2 | CJ dispatch passed no recipient | always "TBD" | `_dispatch_dropship` passes `_supplier_recipient()` + resolved vid; refuses when absent | `execute/engine.py` |
| C3 | No recipient config; live mode could ship to placeholder | fields read via `getattr`, never defined | added 7 `fulfillment_recipient_*` fields + `model_validator` that REJECTS non-advisory mode without a complete recipient | `execute/config.py` |
| B1 | `settle_daily()` 0 callers, read as broken | undocumented silent no-op | labeled **DORMANT-UNTIL-REAL-EXECUTION** in docstring; capital loop gated on a real live-mode integration test | `execute/settlement.py` |

**Corrected false finding (re-verification):** the prior audit listed the
`capital` router mount at `api/main.py:31` as a "DEAD MOUNT importing a
non-existent module." **This is wrong as of today** — `aegis.execute.api.routes.
capital` imports cleanly and exposes `router` (verified: `hasattr(c, 'router')
== True`). The mount is live, not dead.

**What is now REAL:** scheduler is deployable and fires 14 jobs incl. the
calibration emit/refit loop; CJ can never place an order against fake reality;
live mode is structurally blocked without a real recipient; Redis + topics are
config-driven.

**What remains DORMANT-by-design (no real capital execution):** capital
settlement → `prediction_outcomes` → retrain champion serving → FailureForecaster
accuracy → buyer/supplier trust → drawdown breaker. Honestly labeled, not faked.

**Second remediation increment (same day, evening) — A-deep + D + G:**
- **A (deep) — DONE.** Added `runner._calibrate_confidence()`: the published
  `final_confidence` is now the calibrated value when a REAL fitted map exists in
  `calibration_maps`, with `confidence_raw` preserving the original and
  `confidence_calibrated` / `confidence_basis` (`"calibrated"` vs
  `"UNVERIFIED_raw"`) labelling it. Fail-OPEN: no shared pool / identity
  (unfitted) map → raw value + `UNVERIFIED_raw` label. Never fabricates
  calibration. 238 agents tests pass. (`agents/runner.py`)
- **D — DONE (via A-deep, no decorative gate).** Verified the wire: runner →
  `bridge/phase2.py:94` (`phase2_confidence`) → `composer._combined_confidence`
  → ENTER floor at `composer.py:149` (`combined_confidence >= ENTER_MIN_CONFIDENCE`).
  So a raw 0.98 that calibrates to ~0.38 now FAILS the ENTER floor and is
  downgraded — **one measured score changes one real Phase-4 decision.**
- **G — PARTIAL DONE.** Added `tests/unit/test_omega_reality_invariants.py` (8
  falsifiable invariants): CJ refuses without vid/recipient; placeholder constants
  provably gone; live mode rejects blank recipient; calibration fails open without
  a pool; identity calibrator carries no knots. A real-Postgres integration CI
  lane is still **not** added (deferred).

**Third increment (2026-06-18) — F + G-infra + pre-existing cleanup:**
- **F — DONE (quarantine + correction).** `MarketMemory` (verified ZERO
  production callers) marked `DORMANT = True` with a ROADMAP banner;
  `KnowledgeGraph.neighbors/discover` labeled ROADMAP (edges ARE written in prod
  via `relate_opportunity()` so the class is write-only, not deletable). New
  `docs/DORMANT_SYSTEMS.md` is the source of truth. Added an invariant test
  asserting the `DORMANT` marker. **Corrected a FALSE prior claim:** the audit
  said "only Entity Memory is read into a decision" — verified wrong.
  `OpportunityMemory.patterns()` and `SourceMemory.trust_for()` ARE read into the
  RealityVerifier gate (`memory/verify.py:90,132`); `EntityMemory` is actually
  WRITE-ONLY in prod. See `docs/DORMANT_SYSTEMS.md`.
- **G (infra) — DONE.** Added `.github/workflows/integration.yml`: ephemeral
  TimescaleDB + Redis, applies real schema via `aegis migrate`, runs
  `AEGIS_INTEGRATION_TEST=1 pytest tests/integration/` + the falsifiable invariant
  tests. (Workflow validity is verified locally; first real run happens on CI.)
- **Pre-existing ruff — FIXED.** `execute/pricing.py:35-36` PLW0127
  self-assignments removed (the `import` already re-exports them). **Full-repo
  `ruff check .` is now clean (0 violations).**

**Still deferred (next pass):**
- **D (RealityVerifier gate)**: RealityVerifier is now confirmed to READ
  Opportunity+Source memory, but its `passed`/scores remain advisory (surfaced via
  `aegis memory verify` CLI) — wiring it to gate the agent/alert path is a Phase-E
  item. (Note: the A-deep calibration path already provides one measured score
  that changes one decision — see increment 2.)
- **F (deletion)**: MarketMemory is quarantined/labeled, not deleted (deletion
  deferred to avoid breaking the `aegis.memory` export surface + tests).

---

> **REFRESH STATE BANNER**
> - **Latest audit:** PROJECT OMEGA — REALITY ASSAULT II (post-Phase-D) — **2026-06-17 15:21 IST**
> - **Prior audit:** Reality Assault I (pre-Phase-D) — 2026-06-14 13:28 IST (preserved below the `══` divider)
> - **Latest verdict:** ⚠️ **PROCEED TO PHASE E WITH CONDITIONS**
> - **Method:** 7 independent adversarial sub-audits against source (file:line verified). Trust nothing.

---

# ⟢ CURRENT AUDIT — REALITY ASSAULT II — 2026-06-17 15:21 IST

**Auditor stance:** zero-trust. Verified against source through Phases A, B, B.5, C, D.

## THE ONE STRUCTURAL TRUTH THAT GOVERNS EVERYTHING

AEGIS runs in **`advisory` mode by default**, and *every* learning, execution, and intelligence loop is gated on **settled real outcomes that advisory mode structurally cannot produce.**

```
advisory mode (default)
  → execute_plan() returns "advisory_mode" immediately        [engine.py:243-248]
  → no real orders placed
  → settle_daily() is NEVER called anywhere in the repo       [0 callers found]
  → execution_records.outcome frozen at 'pending' forever
  → prediction_outcomes table stays EMPTY
  → ⇒ Retraining starved · FailureForecaster n=0 · supplier/buyer trust None
  → ⇒ Drift perf-drop path has no data · calibration has no live stream
```

The **only** escaping loop is the **signal_outcomes self-supervised backfill (Phase A)** — settles hourly against real rescraped signal-count deltas, no capital needed (`settlement_loop.py:94-164`). It is the **single real feedback signal in the system.** Everything Phase B→D built on capital outcomes is architecturally sound and functionally inert.

## PHASE 1 — SYSTEM TRUTH MAP

| Subsystem | Class | Evidence |
|---|---|---|
| Scrape / Swarm | **PROVEN** | 30+ adapters, real harvests |
| Signal-outcome loop (Phase A) | **PROVEN** | Settles hourly vs real signal deltas |
| Drift detection | **PROVEN (mostly)** | Real champion baseline (FIX-3); auto-rollback wired `drift.py:268-358` |
| Retraining pipeline | **LIKELY → starved** | Real LR/shadow/promotion, but champion **never served by predict path**; fed by empty `prediction_outcomes` |
| Entity Memory (supplier identity) | **WRITE-ONLY in prod** *(corrected 2026-06-18)* | `upsert()` written by SupplierIntel; only `top_entities()` read, CLI-only. NOT the sole decision-reader. |
| Opportunity Memory | **DECISION-READER** *(corrected 2026-06-18)* | `patterns()` read by RealityVerifier gate `verify.py:90` |
| Source Memory | **DECISION-READER** *(corrected 2026-06-18)* | `trust_for()` read by RealityVerifier gate `verify.py:132` |
| Failure Memory | **REPORT-ONLY** | Read into SelfAudit weekly report only |
| Calibration | **UNPROVEN / DISCREPANCY** | See note below |
| Source Memory / Reality Verifier | **DECORATIVE** | `passed` flag never gates anything |
| Market Memory | **DEAD** | `compare_to_history()` zero callers |
| Knowledge Graph | **DEAD/SCAFFOLDING** | `neighbors()`/`discover()` zero callers |
| Phase D Execution Intel (S1–S6) | **~95% DECORATIVE** | Records post-hoc; never gates a decision |
| Buyer Intelligence (S3) | **DEAD** | `record_order()` never called; `buyer_trust` stays None |
| FailureForecaster (S5) | **DECORATIVE** | Accuracy never measured (n=0) |
| ScoreVectorBuilder (S6) | **DECORATIVE** | CLI-only; never in engine/routes |
| RL / LinUCB pricing | **DECORATIVE** | Built+persisted, never invoked; deprecated `OnlinePricingPolicy` still wired |
| Autonomous scheduler (12 jobs) | **ORPHANED** | Robust + complete — but **nothing starts it** (no compose service, no API lifespan hook) |
| `comply/` vs `compliance/` | **NOT duplicate** | Intentional Phase-8 vs Phase-6 split; both imported |
| `capital` router `api/main.py:31` | **DEAD MOUNT** | Imports non-existent module; fails gracefully |
| ~24 DB tables | **UNUSED** | Created by migrations, zero code references |

**⚠️ Calibration discrepancy (resolve before Phase E):** Project memory claims a calibrated `1-p_decline` predictor wired with OOS Brier skill +0.018. The learning-loop audit found `claim_emitter.py:86` uses `Calibrator.identity()` (no-op) and `calibration_maps` never loaded at inference. **One of these is wrong** — highest-value manual verification.

## PHASE 2 — DECISION FORENSICS
Real decision inputs: **scout `p_breakout`, sentinel `p_decline`, confidence gate, compliance BLOCK overrides.** That's it.
Metric inflation (computed/stored, never read by a gate): SurvivabilityScore, 6-axis ExecutionScoreVector, FailureForecast prob, RealityVerifier composite scores, MarketMemory epochs, KnowledgeGraph weights, buyer demand-proxy, LinUCB UCB. Dominant Phase C–D pattern: **compute-and-store theater.**

## PHASE 3 — KNOWLEDGE UTILIZATION RATE: **12.5%** (Entity Memory only; ~20% counting advisory reads). Knowledge layer 70–80% decorative.

## PHASE 4 — EXECUTION INTELLIGENCE: improves **zero** decisions today. Latent audit system waiting for real execution data advisory mode never produces. Supplier verification real only when keys present + mode≠advisory — **untested**.

## PHASE 5 — LEARNING: Learning = signal-outcome loop, drift. Simulated = retraining (empty data, champion unserved). Static/Decorative = calibration, LinUCB, OnlinePricingPolicy.

## PHASE 6 — BUSINESS REALITY (red team, if `live` switched on):
1. **CJ Dropshipping orders to FAKE addresses + MOCK SKU** (`cjdropshipping.py:27,73-79`) — **CRITICAL**.
2. Recipient address config fields don't exist → Printful refuses, plan reports "executed" (status/reality mismatch).
3. `settle_daily()` never called → drawdown breaker never sees PnL.
4. Live mode never exercised by any test.
5. Pricing A/B ±5% can dip below margin floor.

## PHASE 7 — AUTONOMY: **Founder Dependency Index 8/10.** Scheduler excellent but never started; hardcoded topics; manual killswitch re-arm; `localhost:6380` hardcode bug `autonomous.py:201`.

## PHASE 8 — MOAT: only defensible asset = accumulated real signal corpus + self-supervised signal-outcome ledger. Everything else latent, not realized.

## PHASE 9 — PHASE E READINESS

| Dimension | /10 |
|---|---|
| Reality | 4 |
| Intelligence | 3 |
| Execution | 2 |
| Learning | 4 |
| Autonomy | 3 |
| Business | 2 |
| Arbitrage | 3 |

**Improvements vs Reality Assault I:** real drift baseline, real signal-outcome loop, honest UNVERIFIED labeling, supplier verification gate. **Remaining weakness:** entire stack above the signal-outcome loop gated on capital outcomes that never materialize.

---

# FINAL VERDICT — ⚠️ PROCEED TO PHASE E WITH CONDITIONS

Architecture is honest where it counts (labels UNVERIFIED as UNVERIFIED, abstains correctly, invents no buyers) and one real loop exists. But **Phases C and D are predominantly decorative** — sound scaffolding wired to a data source the default mode cannot produce. Conditions below convert the latent system into a real one.

### Top 20 Weaknesses
1. `settle_daily()` zero callers — capital learning loop open. 2. Calibration likely not applied (identity calibrator) — contradicts memory; resolve first. 3. Retrain champion never served by predict path. 4. Scheduler never started. 5. Phase D scores gate no decision. 6. Knowledge graph + market memory dead. 7. RealityVerifier `passed` advisory only. 8. Buyer intel proxy never updates from orders. 9. LinUCB never invoked; deprecated policy wired. 10. Live mode untested. 11. CJ fake addresses + mock SKU. 12. Recipient config undefined → silent non-execution. 13. Drawdown breaker never receives PnL. 14. `prediction_outcomes` empty by construction. 15. Dead `capital` router mount. 16. `localhost:6380` hardcode. 17. ~24 unused tables. 18. Test theater (fake pools return what tests set up). 19. Integration tests skipped by default. 20. Hardcoded scrape topics.

### Top 20 Dead Systems
Market Memory · Knowledge Graph · Buyer Intel `record_order` · FailureForecaster accuracy path · ScoreVectorBuilder (non-CLI) · LinUCB policy · OnlinePricingPolicy (deprecated, wired) · RealityVerifier gate · Self-Audit (report-only) · `capital` router mount · 24 unused tables · `lin_ucb_state` · `model_manifest` · `prediction_audit` · `killswitch_audit` · `geo_*_snapshots` · `compliance_assessments`/`sanction_hits` · `arbitrage_opportunity` · `daily_settlements` · `swarm_agent_health`.

### Top 20 Highest-ROI Fixes
1. Verify/fix calibration application at inference. 2. Wire `settle_daily()` (EOD job + webhook). 3. Add `aegis-autonomous` service / API lifespan to run scheduler. 4. Serve retrained champion (or delete pipeline). 5. Make RealityVerifier `passed` gate high-confidence claims. 6. Feed settled signal-outcomes into RL/threshold loop. 7. Consume ≥1 Phase D score in execution gate, or mark D dormant. 8. Fix `localhost:6380` → `cfg.redis_url_str`. 9. Remove dead `capital` router mount. 10. Gate live mode behind a real integration test. 11. Quarantine Market Memory + Knowledge Graph until a reader exists. 12. Replace CJ fake-address/mock-SKU or disable CJ. 13. Add required `fulfillment_recipient_*` config + validation. 14. CI lane with `AEGIS_INTEGRATION_TEST=1` against ephemeral PG/Redis. 15. Real-PG fixtures for execution_intel/memory tests. 16. Falsifiable invariant tests. 17. Config-driven scrape topics. 18. Killswitch auto-escalation/notification. 19. Drop/​document 24 unused tables. 20. Remove deprecated OnlinePricingPolicy or finish LinUCB.

### Top 20 Production Risks
CJ fake addresses · undefined recipient config · settle_daily uncalled · untested live mode · plan-status vs actual mismatch · no supplier rate limiting · order-ID extraction fragility · A/B price below floor · Kelly on heuristic cost · supplier keys empty (silent) · scheduler not running · killswitch manual re-arm · drift cold-start zeros baseline · calibration miscalibration in prod · empty prediction_outcomes masks decay · Redis hardcode breaks health job · demand proxy mistaken for real demand · static OFAC/FATF drift · no alert on >50% adapter failure · integration paths never CI-verified.

### Top 20 Unnecessary Complexities
6-axis ScoreVector · Knowledge Graph · Market Memory · LinUCB + deprecated policy coexisting · FailureForecaster scoring · buyer-intel module · RealityVerifier 4-score composite · dual-compliance overlap · ~24 speculative tables · 23 migrations for reader-less features · ExecutionAuditor weekly audit · arbitrage_rationale · per-node calibration knobs unfed · shadow-model machinery (no champion served) · Phase D CLI (9 subcommands) for inert system · multiple trust scores across packages · self-audit reports nobody consumes · entity memory secondary to supplier_reliability (two stores) · prediction_audit/model_manifest tables.

### Exact Phase E Prerequisites
- **P0** Resolve calibration discrepancy; prove applied confidence is the calibrated one.
- **P0** Close one full loop on **non-capital** ground truth (signal-outcome → calibration/threshold/RL update → measurably better next prediction). Demonstrate one score that *changes a decision*.
- **P0** Start the scheduler in deployment and prove jobs fire.
- **P0** Serve the retrained champion or formally freeze the retrain pipeline.
- **P1** Add real-infra integration CI lane; replace critical fake-pool tests with real-PG + falsifiable invariant tests.
- **P1** Before any `live`: fix CJ addresses, define recipient config, wire `settle_daily()`, add live-mode integration test. Keep capital execution explicitly dormant until then.
- **P2** Garbage-collect dead systems or label them roadmap.

**Bottom line:** AEGIS graduated from "fabricates confidence" to "honestly inert." Real progress. But it still *records* knowledge it never *reads*. Make one full loop actually change one decision, and Phase E is earned.

---
---

# ══════════════════════════════════════════════════════════════
# ⟢ PRIOR AUDIT — REALITY ASSAULT I — 2026-06-14 13:28 IST (ARCHIVED)
# ══════════════════════════════════════════════════════════════

**Auditor stance:** zero-trust. Every claim below is tied to executable code. Documentation/CLAUDE.md claims were treated as unverified until matched against source.

---

## EXECUTIVE SUMMARY (THE BRUTAL TRUTH)

AEGIS Pulse is a **well-engineered deterministic heuristic scoring system wrapped in the vocabulary of ML, RL, and autonomous learning.** The scraping, deduplication, agent-routing, and alert-pipeline layers are real and functional. But the four subsystems that justify the "intelligence" branding — neural prediction, autonomous self-evolution (Phase 9), confidence calibration, and capital execution — are **either heuristic stand-ins, statistically invalid, or unverified against reality.**

The single most damning finding: **the "retraining pipeline" learns on leaked target data**, making its reported AUC meaningless. The second: **nothing in the capital path verifies a real-world supplier, buyer, or fulfillment outcome before sizing a position.**

Verdict on the central question — *Can AEGIS make money?* — **BUSINESS VALUE NOT PROVEN.**

---

## PHASE 1 — THE BRAIN & SILENT FALLBACK MAP

### Execution path of a signal → action (PROVEN)
1. **SCOUT heuristic** — [scout.py:50-132](src/aegis/agents/nodes/scout.py#L50). Score = `0.40·breakout + 0.25·commercial_intent + 0.15·novelty + 0.10·|sentiment| + 0.10·breadth − coord_penalty − astroturf_penalty` ([scout.py:82-90](src/aegis/agents/nodes/scout.py#L82)). Thresholds `0.70→PROCEED, 0.45→HOLD, else BLOCK` ([scout.py:43-44,93-98](src/aegis/agents/nodes/scout.py#L93)).
2. **Phase 3 augmentation** — replaces `score` with `p_breakout` but **cannot flip the verdict** ([scout.py:173-211](src/aegis/agents/nodes/scout.py#L173), enforced doctrine in [base.py:76-90](src/aegis/agents/nodes/base.py#L76)).
3. **Supervisor aggregation** — weighted blend `scout .35 / auditor .30 / narrative .15 / geo .10 / red_team .10` ([supervisor.py:34-40](src/aegis/agents/supervisor.py#L34)). Final verdict honors hard vetoes ([supervisor.py:286-300](src/aegis/agents/supervisor.py#L286)).

### Where is mathematical expectancy actually calculated?
**Only in Kelly sizing**, and only when capital execution runs: `f* = (p·b − q)/b` at [kelly.py:46-57](aegis-phase4/src/aegis/execute/sizing/kelly.py#L46). The agent "score" (0–1) is **not an expectancy** — it is a weighted feature sum. There is **no EV in dollars anywhere in the Phase 2 decision** that produces ENTER/HOLD/BLOCK. **CONFIDENCE NOT SUPPORTED** for any claim that the verdict reflects expected profit.

### Silent fallbacks returning confident "Clear"/"Proceed"
- **Missing LLM (no keys / unreachable):** the LLM only refines text/confidence and is **only consulted on HOLD** ([scout.py:219-221](src/aegis/agents/nodes/scout.py#L219)). A clear PROCEED never touches an LLM. **Functionally fine** — but means "LLM-reasoned" verdicts in the UI are heuristic. Provenance *is* tracked honestly via `reasoning_source` heuristic/llm/mixed ([supervisor.py:435-443](src/aegis/agents/supervisor.py#L435)) — credit where due.
- **Phase 3 inference failure** → bridge returns `verdict:"hold", halt:True, confidence:0.0` ([bridge.py:291-303](src/aegis/agents_phase3_glue/bridge.py#L291)) and scout silently drops the augmentation ([scout.py:158-171](src/aegis/agents/nodes/scout.py#L158)). The heuristic floor still emits a verdict — **no error surfaces to the operator.** This is graceful-but-invisible degradation.
- **Gateway create failure** raises ([agents_bridge.py:79-88](src/aegis/llm/bridge/agents_bridge.py#L79)); callers catch and keep heuristic. Net effect: **the system is heuristic-complete and never blocks on missing AI** — accurate to doctrine, but the "10-node multi-agent LLM" framing overstates what drives outputs.

---

## PHASE 2 — DECISION FORENSICS / FOOLABILITY MATRIX

### Giggle-metric vulnerability (PROVEN)
SCOUT consumes `velocity_1h/6h/24h`, `commercial_intent`, `novelty`, `signal_count`, `unique_authors` straight from the candidate. The **only** manipulation defenses are:
- astroturf penalty `0.20` if `unique_authors/signal_count < 0.10 AND signal_count ≥ 20` ([scout.py:78](src/aegis/agents/nodes/scout.py#L78))
- coordination penalty `0.30·coordination_risk` ([scout.py:80](src/aegis/agents/nodes/scout.py#L80))

**Attack:** an adversary posting from ≥10% distinct (cheap, sockpuppet) author IDs with high velocity completely evades the astroturf gate and scores PROCEED. Velocity is a raw count-derivative with no provenance weighting. **The system cannot distinguish organic velocity from fabricated velocity** beyond the crude diversity ratio. `coordination_risk` itself is an upstream input, not independently verified here.

### Poison-data resilience of the heuristic
The heuristic is hardened against *crashes* (`max(0.0, …)` guards negative counts, [heuristic.py:385-386](src/aegis/predict/models/heuristic.py#L385)) — it "NEVER raises." But hardening against crashing ≠ hardening against *being fooled*. A crafted window with high `velocity_24h` + matching counts deterministically yields `BREAKOUT` ([heuristic.py:242-248](src/aegis/predict/models/heuristic.py#L242)).

### Impact on Kelly positioning
Fabricated velocity → high `p_breakout` → high agent score, but Kelly's `loss_probability` comes from `intent.loss_probability` ([kelly.py:120](aegis-phase4/src/aegis/execute/sizing/kelly.py#L120)). And **`unit_cost` is fabricated**: `_derive_unit_cost` returns `expected_margin·2` or a hardcoded `10.0` ([engine.py:319-325](aegis-phase4/src/aegis/execute/engine.py#L319)); `unit_price = cost + margin` or `cost·2` ([engine.py:328-331](aegis-phase4/src/aegis/execute/engine.py#L328)). **The Kelly inputs are circular synthetic constants, not market quotes.** Position sizing on poison data is therefore sized on fiction regardless of the 0.25× fraction and 10% cap (real safety rails, [engine.py:128-131](aegis-phase4/src/aegis/execute/engine.py#L128)).

---

## PHASE 3 — CONFIDENCE CALIBRATION & REINFORCE FORENSICS

### Confidence generation (PROVEN, classified WEAK)
Heuristic confidence = `0.20 + log-volume bonuses + momentum/OLS bonuses`, ceiling 0.75 ([heuristic.py:420-424](src/aegis/predict/models/heuristic.py#L420)). SCOUT confidence = `min(1, signal_count/50)·0.7 + 0.3` ([scout.py:101](src/aegis/agents/nodes/scout.py#L101)).
- **It is a function of evidence volume, not of historical correctness.** No calibration curve, no reliability diagram, no Brier score. **Confidence does not correlate with realized outcomes anywhere.** Classification: **WEAK** (honestly bounded, but uncalibrated). The 0.75 ceiling honesty comment is a genuine good practice.

### The Phase 9 REINFORCE loop — is it learning? **LEARNING NOT PROVEN.**
[rl_policy.py:82-114](src/aegis/evolve/rl_policy.py#L82): the update rule is
```
reward = clip(roi/100, -1, 1)
if reward>0.05:  weights += lr·reward·weights      # scale up
else:            weights *= max(0.1, 1 - lr·|reward|)  # scale down all
weights = normalize(weights)
```
**This is not policy gradient.** There is **no action, no log-probability, no gradient of a policy w.r.t. its parameters.** Every weight is multiplied by the *same* scalar on a profitable trade, then renormalized to the simplex — which means **a uniform scaling followed by renormalization leaves the relative weights essentially unchanged.** The four weights cannot meaningfully differentiate which pricing signal caused profit, because the update never attributes reward to a specific weight. It is a decorative regression-free heuristic. Calling it "4-weight REINFORCE policy" is **architectural theater**. Overfitting is not even the risk — **it barely moves.**

### Evidence Ratio (Evidence-backed facts / total supporting claims)
| Pathway | Evidence-backed | Total claims | Ratio |
|---|---|---|---|
| SCOUT verdict | feature math is real (1) | "intelligent trend scoring" (1) | **1.0** (it does what it says — it's just shallow) |
| Phase 9 "learning" | 0 (no proven improvement) | retrain + drift + RL (3) | **0.0** |
| Confidence = calibrated | 0 | "calibrated" doc claim (1) | **0.0** |
| Kelly EV sizing | formula real (1) | inputs real (0) | **0.5** (math valid, inputs fake) |

---

## PHASE 5 — PREDICTION AUDIT (most serious finding)

### TARGET LEAKAGE in the retraining pipeline — INVALIDATES ALL REPORTED AUC
`retrain.py _preprocess_outcomes`: the feature matrix is built from
```
X[2] = actual_roi_pct/100      # ← POST-OUTCOME
X[3] = pnl_usd/1000            # ← POST-OUTCOME
X[4] = units_sold             # ← POST-OUTCOME
y    = 1 if resolution_status=='successful' else 0
```
`actual_roi_pct`, `pnl_usd`, and `units_sold` are **realized results of the very trade being predicted**, and the label `successful` is a near-deterministic function of `roi/pnl`. Feeding outcomes as features to predict the outcome guarantees a trivially high AUC. **Every `test_auc` the champion-promotion gate compares (`retrain.py` `best_candidate.test_auc`) is statistically meaningless.** This is textbook **data leakage** + the promotion gate (`+0.02 AUC`) is gating on noise.

### The "neural models" actually train as LogisticRegression
`retrain.py _train_and_evaluate`: *"Uses logistic regression as the training proxy."* PatchTST/Autoformer/TimesNet/HGT are **never trained** in this path; `architecture` only selects HPO search bounds, then a `LogisticRegression(max_iter=300)` is fit for all of them. The saved "artifact" is a JSON of hparams, not weights (`retrain.py _save_model_artifact`). **The neural prediction core is UNPROVEN to ever run in evolution.**

### Ground truth / drift
- Ground truth *is* stored (`prediction_outcomes` hypertable) — real.
- Drift baseline is **synthetic zeros/ones**: `_baseline_mean=np.zeros, _baseline_std=np.ones` ([drift.py:354-363](src/aegis/evolve/drift.py#L354)). KS-distance is a mean-abs-deviation proxy ([drift.py:365-380](src/aegis/evolve/drift.py#L365)), not a real KS test. So drift "detection" measures deviation from an arbitrary origin, not from the training distribution. **Drift detection is decorative until a real baseline is populated** (the code's own docstring admits this, [drift.py:355-360](src/aegis/evolve/drift.py#L355)).
- The auto-rollback + killswitch trip on critical drift ([drift.py:262-352](src/aegis/evolve/drift.py#L262)) is real wiring — but triggered by an invalid signal.

**Prediction Reliability Score: ~15/100** (ground-truth capture and plumbing exist; the actual model and its validation are invalid).

---

## PHASE 7 — BUSINESS EXECUTION AUDIT (Reality Gaps)

| Business step | Verified in code? | Evidence |
|---|---|---|
| Supplier exists | **NO** | `_dispatch_pod` posts to Printful with `_MOCK_PRODUCT_ID=1` ("generic T-shirt") and recipient `address1:"TBD", zip:"00000"` ([printful.py:28,89-95](src/aegis/fulfillment/printful.py#L89)) |
| Supplier validated | **NO** | client returns `[]` silently when no key ([printful.py:56-58](src/aegis/fulfillment/printful.py#L56)); engine records `failed`, no validation |
| Buyer exists / authenticated | **NO** | no buyer entity anywhere in engine.py |
| Margin estimated | **Synthetic** | `_derive_unit_cost/price` are constants ([engine.py:319-331](aegis-phase4/src/aegis/execute/engine.py#L319)) |
| Logistics verified | **NO** | `competition_risk = 0.10` hardcoded constant ([engine.py:346](aegis-phase4/src/aegis/execute/engine.py#L346)) |
| Compliance real-time | Partial | compliance engine has real API checkers (not audited line-by-line here) but Phase 6 gate **fails open** when absent (per CLAUDE.md `gate_execution_plan` bypass) |
| Capital risk measured | Partial | Kelly rails real ([engine.py:128-131](aegis-phase4/src/aegis/execute/engine.py#L128)), inputs fake |

**Business Reality Gaps:** the system assumes a supplier will fulfill, a buyer will purchase at `unit_price`, and that `units_sold` will materialize — **none is programmatically verified before a plan is sized.** **BUSINESS VALUE NOT PROVEN.** The advisory-mode default ([engine.py:203-208](aegis-phase4/src/aegis/execute/engine.py#L203)) is the only reason this is safe today — it places nothing.

---

## PHASE 8 — ADVERSARIAL: Can AEGIS detect deception?

- **Fake demand/velocity:** mostly NO (Phase 2 above).
- **Prompt injection:** LLM output is constrained to JSON `{reasoning, confidence_factor 0.5..1.0}` and can only *multiply confidence in [0.5,1.0]* ([base.py:88-90](src/aegis/agents/nodes/base.py#L88)) — so an injected prompt **cannot flip a verdict**. This is a genuine structural defense. ✅
- **Data poisoning of the learning loop:** trivially effective — since features = outcomes, a few fabricated `prediction_outcomes` rows with high ROI directly poison the leaked-feature model. ❌
- **Can it explain suspicion?** Reasoning strings are templated, deterministic, and traceable ([heuristic.py:468-478](src/aegis/predict/models/heuristic.py#L468)) — explainability is real for the heuristic. ✅

---

## PHASE 10 — TRUSTWORTHINESS SCORECARD (0–100)

| Subsystem | Score | Basis |
|---|---|---|
| Scrape / ingest | 80 | real, functional, adapter status honestly documented |
| Agent routing / supervisor | 72 | deterministic, transparent, honest provenance |
| Alert pipeline / killswitch | 78 | real Redis-backed, advisory-safe |
| Kelly sizing math | 60 | formula correct, inputs synthetic |
| Phase 3 prediction (heuristic) | 55 | works, uncalibrated, bounded |
| Phase 3 neural core | 20 | unproven it ever trains in evolution |
| Phase 9 retraining | **10** | target leakage invalidates it |
| Phase 9 RL policy | **8** | not actually learning |
| Drift detection | 25 | synthetic baseline |
| Confidence calibration | 20 | volume-proxy, uncalibrated |
| Business execution | 18 | no real-world verification |
| Manipulation resistance | 35 | LLM-injection safe; velocity-spoof exposed |

---

## PHASE 11 — CEO REPORT (condensed)

**What actually works:** scraping/dedup/swarm; deterministic agent graph with honest fallback and provenance; advisory-safe alert+killswitch infra; LLM-injection-resistant augmentation contract; Kelly *formula* and capital rails.

**What only appears to work:** Phase 9 "autonomous self-evolution," neural prediction, drift detection, confidence calibration, end-to-end "arbitrage profit" claim.

**Most dangerous illusions (ranked):**
1. **Retraining AUC is real** — it is leakage-driven noise (`retrain.py _preprocess_outcomes`). If anyone trusts champion promotion, they ship worse models believing they're better.
2. **"The system learns"** — the REINFORCE policy is a near-no-op ([rl_policy.py:101-112](src/aegis/evolve/rl_policy.py#L101)).
3. **"Verdicts reflect profit expectancy"** — they reflect a weighted feature sum; EV only appears in fictional-input Kelly.
4. **Drift/killswitch protects capital** — fires off a synthetic baseline ([drift.py:354-363](src/aegis/evolve/drift.py#L354)).

**Top failure/business/technical risks (highest-priority subset):**
- Leakage in `_preprocess_outcomes` → invalid model selection.
- Synthetic unit-cost/price → meaningless position sizing.
- No supplier/buyer verification → fulfillment failures in live mode.
- Velocity-spoof bypasses astroturf gate → manipulated ENTER.
- `gate_execution_plan` fails open when compliance absent.

---

## EXECUTION & CIRCUIT-BREAKER ROADMAP

**Tier gates (capital unlock criteria):**
1. **Advisory → Staging/Paper:** require (a) leakage removed — features must be *pre-trade* only (`prediction_score`, `confidence`, feature-window stats; **never** `roi/pnl/units_sold`); (b) a real held-out backtest AUC > 0.60 on **non-leaked** labels; (c) Prometheus: `aegis_predict_brier_score`, `aegis_predict_auc_holdout`, calibration-bin gauges; Jaeger span coverage 100% on `scout→supervisor→engine`.
2. **Staging → Live:** require (a) ≥100 *paper* settlements with realized vs predicted tracked; (b) real `unit_cost`/`unit_price` sourced from Phase 7 quotes, not constants; (c) supplier+buyer existence checks return non-empty before `create_plan`; (d) drift baseline populated from champion training stats (replace `np.zeros/np.ones`).
3. **Live:** keep 0.25× Kelly + 10% cap + daily-loss breaker ([engine.py:266-269](aegis-phase4/src/aegis/execute/engine.py#L266)), already correct.

**Zero-tolerance recovery protocol:**
- **Drawdown breach** (`daily_pnl < −limit`, [engine.py:266](aegis-phase4/src/aegis/execute/engine.py#L266)): halt dispatch (already returns `halted_drawdown`), require human re-arm of killswitch, freeze RL/retrain promotion until post-mortem.
- **RPO/RTO breach** (Phase 15): the DR plumbing exists; tie `aegis dr health` RPO-drift gauge to a Prometheus alert that auto-trips the Phase 4 killswitch (reuse `_trip_killswitch`, [drift.py:331-352](src/aegis/evolve/drift.py#L331)).

**Fastest path to revenue:** ignore Phase 9 entirely; run advisory alerts to a human operator who sources real supplier/buyer quotes manually. The scrape→score→alert spine is the only trustworthy revenue surface today.

**Fastest path to trustworthy intelligence:** (1) fix leakage, (2) populate a real drift baseline, (3) add a calibration layer (isotonic/Platt) keyed on stored outcomes, (4) replace the RL no-op with either real contextual-bandit attribution or delete it and stop claiming learning.

---

*All findings above are tied to specific source lines. Items marked UNPROVEN / NOT PROVEN reflect absence of executable evidence, not absence of intent. The compliance external-API checkers and full graph edge map were sampled, not line-audited — treat those as "partially audited" rather than cleared.*
