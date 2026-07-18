# ADR 0019 — Self-Evolution Feedback Loop Closure (BRAIN-1)

- **Status**: Accepted
- **Date**: 2026-06-13
- **Audit ID**: BRAIN-1 (AEGIS_AUDIT.md), GODMODE PASS 3

## Context

Phases 6 (capital execution), 9 (self-evolution), and 3 (predictive core)
each existed but were **not connected end-to-end**. Settled trades were not
fed back as training labels, drift detection had no automatic remediation,
retrained models were promoted without a shadow evaluation period, and the
RL pricing policy did not consume realised outcomes. The "learning system"
was a collection of capable but disconnected organs.

The required loop is:

```
trade → settle → record → drift check → retrain → promote → price adapt
```

## Decision

**Close the loop with four best-effort, never-blocking bridges.**

1. **Settlement → Outcome (3A)**:
   `SettlementManager.settle_plan()` calls
   `_record_outcome_for_evolution()`, which writes a `TradeOutcome` via
   `aegis.evolve.OutcomeRecorder` and publishes `outcome_recorded` to
   `aegis:phase9:evolve_events`. Wrapped in `try/except` — settlement must
   succeed even if evolution is unavailable. Zero-cost trades are floored at
   `Decimal("0.01")` to avoid division by zero.

2. **Drift → Auto-Rollback (3B)**:
   `DriftDetector.run_all_checks()` triggers `_attempt_auto_rollback()` when
   `drift_score > 2× threshold`. It reactivates the previous champion via
   `ModelStore.get_previous_champion()`/`activate()`, publishes
   `auto_rollback`, and **trips the killswitch** when drift exceeds
   `3× threshold` — halting all execution until a human intervenes.

3. **Retrain → Shadow (3C)**:
   `RetrainingPipeline._register_shadow()` registers an AUC-improved
   candidate as a shadow model for a **72-hour parallel evaluation** before
   promotion. `job_shadow_evaluate()` (every 6h) promotes the shadow only if
   it beats the champion by ≥ 2% AUC on the last 72h of outcomes.
   `AEGIS_EVOLVE_FAST_PROMOTE=true` skips the shadow window (dev/testing).

4. **Policy → Pricing (3D)**:
   `PricingStrategy` loads `OnlinePricingPolicy` weights (class-level cache,
   refreshed every 6h) so daily pricing decisions adapt from realised
   outcome signals.

All four bridges are best-effort: every import and cross-phase call is
guarded so a missing optional dependency degrades to the prior static
behaviour rather than raising.

## Consequences

- **Positive**: AEGIS now improves itself without human intervention — every
  settled trade is a label, drift is self-correcting, and bad models roll
  back automatically (with a killswitch backstop on extreme drift).
- **Positive**: the 72h shadow window prevents a single lucky retrain from
  displacing a proven champion; promotion requires a real, sustained edge.
- **Positive**: the killswitch trip on `>3× threshold` drift makes
  catastrophic model regression fail safe (stop) rather than fail open.
- **Negative**: more moving parts in the nightly/6-hourly scheduler; each
  job is independently logged and degrades gracefully, but operational
  surface grew.
- **Negative**: auto-rollback depends on `ModelStore` retaining a previous
  champion; a fresh deployment with a single model logs
  `drift.rollback.no_previous_champion` and relies on the killswitch.

## Alternatives Considered

- **Immediate promotion on AUC improvement (no shadow)**: rejected —
  overfits to the retrain window; the 72h shadow is the safety margin.
- **Manual rollback only**: rejected — drift can appear between human
  review cycles; automatic rollback + killswitch is the fail-safe.
- **Coupling settlement to evolution synchronously**: rejected — settlement
  correctness must never depend on evolution availability; hence the
  best-effort, swallow-and-log bridge.
