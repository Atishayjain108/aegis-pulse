# Phase 3 Architecture

## Layered design

Phase 3 is structured as a strict dependency stack. Each layer below
can be imported independently; layers above only depend on layers
below. There are no cycles.

```
┌─────────────────────────────────────────────────────────────────┐
│  Outer rim — operator-facing surfaces                           │
│  ┌────────────┐  ┌────────────┐  ┌──────────────────────────┐   │
│  │ serving/   │  │  cli/      │  │ agents_phase3_glue/      │   │
│  │ FastAPI    │  │  Typer     │  │ Phase 2 LangGraph bridge │   │
│  └────────────┘  └────────────┘  └──────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Orchestration                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ inference/                                               │   │
│  │   InferenceRunner — pipeline orchestrator                │   │
│  │   AuditRecord     — sidecar audit doc                    │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Decision logic — augmentation that cannot flip verdicts        │
│  ┌─────────────┐  ┌─────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ models/     │  │ causal/ │  │ rl/      │  │ backtest/     │  │
│  │  fusion     │  │ attribs │  │ Kelly    │  │ walk-forward  │  │
│  │  uncertainty│  │ counter │  │ env      │  │ purged k-fold │  │
│  └─────────────┘  └─────────┘  └──────────┘  └───────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Predictors — heuristic floor + optional neural augmentation    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ models/                                                  │   │
│  │   heuristic.py    — Heuristic{Temporal,Relational}       │   │
│  │   patchtst.py     — optional torch                       │   │
│  │   autoformer.py   — optional torch                       │   │
│  │   timesnet.py     — optional torch                       │   │
│  │   hgt.py          — optional torch_geometric             │   │
│  │   factory.py      — name → predictor with fallback       │   │
│  │   base.py         — Predictor protocol                   │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Feature engineering                                            │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ features/                                                │   │
│  │   velocity.py     — multi-window log-velocity            │   │
│  │   graph.py        — creator+platform adjacency           │   │
│  │   builder.py      — Phase 1 signals → FeatureWindow      │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Foundation                                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ schemas.py        — pydantic v2, frozen, extra=forbid    │   │
│  │ constants.py      — every magic number with rationale    │   │
│  │ errors.py         — typed AEGIS-PREDICT-NNNN codes       │   │
│  │ resilience.py     — timeout / retry / circuit breaker    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Doctrine: heuristic-first, neural-augmentation

Phase 3 is **always functional with zero ML dependencies installed
and zero LLM API keys configured.** Concretely:

| Component | Floor (always works) | Augmentation (optional) |
|---|---|---|
| Temporal predictor | log-velocity heuristic | PatchTST / Autoformer / TimesNet |
| Relational predictor | graph-density heuristic | HGT |
| Uncertainty | conformal interval scaling | deep ensembles |
| Causal attribution | weighted-coefficient floor | DoWhy linear backdoor |
| Execution policy | fractional Kelly | PPO via Ray RLlib |

Augmentation is **constrained** so it cannot dominate the floor:

* a neural prediction can multiply confidence by `[0.5, 1.0]` (only
  *reduce* it);
* a neural prediction can append reasoning text;
* a neural prediction **cannot flip the verdict** (ENTER → AVOID etc).

This gives us:

1. **Zero-API tests pass** — the test suite never hits a network.
2. **Bounded latency** — the heuristic floor is sub-millisecond.
3. **Graceful degradation** — losing torch / Ray / DoWhy degrades
   smoothly to floor performance with audit trail flagging.
4. **Reproducible audit trails** — every prediction can be replayed
   from `feature_window_hash` + `model_id` + `seed`.

## Data flow

```
Phase 1 signals (raw)
       │
       ▼
build_window_from_rows(window_end, size=168)
       │
       ▼
  ┌────────────┐         ┌─────────────┐
  │FeatureWindow│  ───►  │CreatorGraph │
  │ 168×20      │         │ authors,    │
  │ pydantic v2 │         │ platforms,  │
  │ frozen      │         │ edges       │
  └─────┬──────┘         └─────────────┘
        │
        ├──────────────────┐
        ▼                  ▼
  Heuristic         Heuristic
  Temporal          Relational
   predict           predict
        │                  │
        ▼                  ▼
  PredictionBundle    PredictionBundle
  4 horizons          4 horizons
        │                  │
        └────► fuse() ◄────┘
                │
                ▼
       Fused PredictionBundle
                │
                ▼
       DeterministicAttributor
                │
                ▼
         InferenceResult
         ├── bundle
         ├── record  (DB-bound)
         ├── audit   (MinIO-bound)
         ├── causal[]
         └── halt_reasons[]
```

## Crossing layer boundaries

* `inference.runner` is the **only** place that calls predictors,
  fusion, causal, and the latency-budget gate together. Phase 4
  (alerts) and Phase 2 (LangGraph SCOUT/SENTINEL) call
  `InferenceRunner.run()`, never individual predictors.

* `agents_phase3_glue` is in a separate top-level package — not
  inside `aegis.predict` — so Phase 3 imports cleanly without
  LangGraph. Phase 2's graph builder loads the bridge on demand.

* `serving` re-uses one long-lived `InferenceRunner` per worker.
  Models stay warm between requests; first-request latency is
  paid once at process start.

## Determinism

Every prediction can be replayed bit-for-bit:

```
correlation_id  → join key for traces, audits, predictions
seed            → numpy / torch RNG seed (recorded in bundle)
feature_window_hash → sha256 of the window (recorded in bundle)
model_id        → registry key, includes architecture + version
                  + sha256-prefix of weights
```

Given these four fields, an offline replayer can:

1. Load the manifest by `model_id` from the registry.
2. Verify the artifact sha256 matches.
3. Re-construct the `FeatureWindow` from the stored hash (signals
   are kept in Phase 1's TimescaleDB).
4. Re-run inference with the recorded seed.
5. Compare to the persisted bundle — they must match.

## Tenancy

Every persisted record carries a `tenant_id` (UUID) and the SQL
schema enforces RLS via `current_setting('app.current_tenant')`.
Phase 1 sets the tenant on every connection; Phase 3 inherits.

## Failure modes

| Failure | Behaviour |
|---|---|
| Missing torch | Heuristic floor; `is_heuristic_only=True` in bundle |
| Predictor timeout | `halt_reasons += ["predictor_timeout"]`; heuristic re-run |
| Predictor exception | `halt_reasons += ["predictor_error:Foo"]`; heuristic re-run |
| Latency budget breached | `halt_reasons += ["latency_budget_exceeded:Xms>Yms"]`; bundle still returned |
| Causal attribution fails | `halt_reasons += ["causal_failed"]`; empty `causal=()` |
| Phase 1 unreachable | Empty signals → empty `FeatureWindow` → DORMANT prediction |
