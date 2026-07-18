# ADR-0009: Autonomous Self-Evolution & Online Learning

## Status

Accepted — implemented in Phase 9 (`src/aegis/evolve/`)

## Context

Static ML models decay within weeks due to market drift.  A model trained
30 days ago is 50% less predictive today because:

- Product trend lifecycles average 14–21 days on social platforms.
- Seasonal and geopolitical events shift demand signals unpredictably.
- New scrape adapters change the signal distribution over time.

Retraining every day is expensive and risks overfitting to noise.
A bad retrained model silently destroying profitability is worse than
a stale-but-stable one.

## Decision

### 1. Weekly retraining (not daily)

- Accumulate settled trade outcomes every day as ground truth labels.
- Retrain every Sunday 2 AM UTC using the last 30 days of outcomes.
- Require ≥ 100 outcomes before attempting retraining.
- Train candidates for multiple architectures (PatchTS, Autoformer, heuristic).
- **Shadow deployment**: new model evaluated on a 7-day holdout set before promotion.
- **Promotion gate**: promote only if test AUC improvement > 2% AND recall does not regress.

### 2. Drift detection (continuous)

- Kolmogorov-Smirnov distance between recent feature distribution and training baseline.
- If drift score > 0.15, trigger early retraining (before Sunday schedule).
- If precision drops > 5% week-over-week, trigger auto-rollback to previous champion.
- All drift snapshots persisted to `drift_snapshots` table for audit.

### 3. Hyperparameter optimisation (weekly via Optuna)

- 30 trials per architecture using Bayesian TPE sampler.
- Metric: AUC on validation holdout.
- Optuna is an optional dependency (`evolve` extra); falls back to hand-tuned defaults when absent.
- Lightweight logistic regression proxy used for fast HPO; final candidate replaces it.

### 4. Online learning (daily via reward signals)

- Every settled trade outcome feeds the `OnlinePricingPolicy` RL agent.
- Four policy weights: `[cost_based, demand_based, inventory_based, competitor_based]`.
- REINFORCE-style update: profitable trade amplifies weights, loss-making trade shrinks them.
- Weights always re-normalised to sum to 1.0 (unit simplex).
- Persistence to `rl_policy_state` table every N updates (default: 100).
- Heavy model retraining is NOT triggered by policy updates — only the lightweight weights shift.

## Architecture

```
Trade Settlement ──► OutcomeRecorder ──► prediction_outcomes (TimescaleDB)
                                                │
                          ┌─────────────────────┤
                          │                     │
                    DriftDetector         RetrainingPipeline
                    (continuous)         (Sunday 2 AM UTC)
                          │                     │
                    drift_snapshots      HPO (Optuna TPE)
                          │                     │
                    alert / early        ModelCandidate[]
                    retrain trigger             │
                                        Champion comparison
                                        (+2% AUC required)
                                                │
                                        model_candidates
                                        (is_champion=TRUE)
                                                │
                                        Phase 3 InferenceRunner
                                        (loads champion at startup)
```

## Trade-offs

| Factor | Choice | Alternative rejected |
|--------|--------|---------------------|
| Retraining frequency | Weekly | Daily — overfits to noise |
| Promotion gate | +2% AUC | No gate — risks silent regressions |
| HPO sampler | Bayesian TPE | Random search — 3× worse sample efficiency |
| RL update | REINFORCE (policy gradient) | Q-learning — requires state replay buffer |
| Drift detector | KS-distance proxy | Full NannyML — 10× heavier, requires reference dataset |

## Consequences

**Positive:**
- Expected +3–5% AUC improvement over 3 months vs static model.
- Auto-rollback prevents prolonged silent performance degradation.
- RL policy captures pricing dynamics without full retraining.
- Zero capital at risk during shadow evaluation period.

**Negative:**
- Separate training pipeline adds operational complexity.
- Retraining takes 1–2 hours off-peak (acceptable for weekly cadence).
- Requires ≥ 100 settled outcomes — first retraining may be delayed in early deployment.

## Implementation Notes

- `src/aegis/evolve/__init__.py` — package root, version `9.0.0`
- `src/aegis/evolve/outcomes.py` — `OutcomeRecorder` (asyncpg, `prediction_outcomes` table)
- `src/aegis/evolve/hpo.py` — `optimize_hyperparameters()` (Optuna TPE; graceful fallback)
- `src/aegis/evolve/drift.py` — `DriftDetector` (KS-distance + precision monitoring)
- `src/aegis/evolve/rl_policy.py` — `OnlinePricingPolicy` (4-weight REINFORCE agent)
- `src/aegis/evolve/retrain.py` — `RetrainingPipeline` (full weekly retrain + promotion)
- `src/aegis/evolve/api.py` — FastAPI `/evolve/*` REST endpoints
- `src/aegis/evolve/cli.py` — `aegis evolve` Click commands
- `db/migrations/0011_evolve.sql` — `prediction_outcomes`, `model_candidates`, `drift_snapshots`, `rl_policy_state`, `retrain_audit`

## Related ADRs

- [ADR-0003: Phase 3 Heuristic-First Doctrine](0003-heuristic-first.md) — champion model always starts heuristic
- [ADR-0006: Capital Execution Engine](0006-capital-execution.md) — Phase 6 outcomes are the ground truth source
