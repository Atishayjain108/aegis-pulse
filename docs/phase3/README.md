# AEGIS Pulse — Phase 3 Documentation

Welcome. Start here.

## For operators

- **[QUICKSTART.md](QUICKSTART.md)** — 5-minute install-to-prediction.
- **[OPERATIONS.md](OPERATIONS.md)** — daily runbook, promotion,
  rollback, DR.

## For developers

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — layered design, data flow,
  doctrine.
- **[MODELS.md](MODELS.md)** — model catalog and how to add new ones.
- **[INTEGRATION_PHASE2.md](INTEGRATION_PHASE2.md)** — wiring Phase 3
  into Phase 2's LangGraph.

## For curious readers

- **[../CHANGELOG.md](../CHANGELOG.md)** — what shipped in v3.0.0.
- **[../README.md](../README.md)** — top-level overview.
- **Phase status table:** `aegis-pulse/CLAUDE.md` (root of the
  monorepo).

## Source-of-truth files

When the docs and the code disagree, the code wins. Here are the
files that define the contracts:

| Concept | File | Why it matters |
|---|---|---|
| All schemas | `src/aegis/predict/schemas.py` | Pydantic v2 models — frozen, `extra="forbid"` |
| Magic numbers | `src/aegis/predict/constants.py` | Every threshold has a `# rationale:` comment |
| Error codes | `src/aegis/predict/errors.py` | AEGIS-PREDICT-NNNN typed exceptions |
| Action mapping | `src/aegis/predict/models/heuristic.py` | Stage → action rule table |
| Verdict mapping | `src/aegis/agents_phase3_glue/bridge.py` | PredictionAction → Phase 2 verdict |
| Promotion rules | `src/aegis/predict/registry/promotion.py` | F1 margin + precision tie-break |
| DB tables | `db/migrations/0002_predictions.sql` | RLS, indices, hypertable |

## Conventions

- Every magic number lives in `constants.py` with a `# rationale:`
  comment. No bare numbers in business logic.
- Every error has a code (`AEGIS-PREDICT-NNNN`). Codes are stable
  across releases; messages may change.
- Every datetime is timezone-aware UTC. Naive datetimes are rejected
  by the schemas.
- Every async function is wrapped by `resilient_call` if it touches
  an external system.
- Every keyword that affects safety (e.g. position size) is keyword-
  only in its function signature.

## Doctrine

Phase 3's overarching design rule: **heuristic-first, neural-as-augmentation**.

> Every component computes a deterministic verdict from numeric
> features. LLMs and neural models may only:
> 1. Multiply confidence by a factor in `[0.5, 1.0]` (only reduce);
> 2. Append reasoning text;
> 3. Add structured detail to `metadata`.
>
> They MUST NOT flip the verdict, raise confidence, or change the
> stage classification.

This guarantees:

- Tests pass with zero ML deps and zero API keys.
- Latency is bounded by the heuristic floor (~12 ms p99).
- Graceful degradation when neural models fail to load.
- Reproducible audit trails — every prediction is replayable from
  `(seed, feature_window_hash, model_id)`.

When extending Phase 3, ask yourself:

1. Can this run with no torch / no Ray / no DoWhy / no LLM keys?
   If yes, ship it.
   If no, add a heuristic floor first.
2. Does this need to mutate the verdict produced by a heuristic?
   If yes, you're violating the doctrine — find another way.
3. Is there a magic number? Move it to `constants.py` with a
   rationale comment.
4. Is there an external call? Wrap it in `resilient_call`.
