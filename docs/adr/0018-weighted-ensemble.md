# ADR 0018 — Feedback-Weighted Agent Ensemble (BRAIN-4)

- **Status**: Accepted
- **Date**: 2026-06-13
- **Audit ID**: BRAIN-4 (AEGIS_AUDIT.md), GODMODE PASS 2-2D

## Context

The Phase 2 supervisor originally aggregated the 10 agent decisions with
**equal weight** — every faculty's vote counted the same regardless of its
historical track record. In an arbitrage system, some agents are
systematically more reliable than others on realised outcomes; treating a
consistently-wrong agent the same as a consistently-right one dilutes signal
quality.

We wanted the supervisor to learn which faculties to trust, using the
ground-truth feedback already captured by the Phase 9 evolution loop
(`prediction_outcomes`), without destabilising the deterministic
heuristic-first doctrine.

## Decision

**Replace equal-weight aggregation with accuracy-weighted voting.**

For each agent over the last 30 days of settled outcomes:

```
correct   = (voted ENTER  AND roi > 0)
          + (voted HOLD/BLOCK AND (roi <= 0 OR no trade))
total     = outcomes where the agent cast a vote
accuracy  = correct / total            (default 0.5 with no history)
weight    = 0.5 + accuracy             (range 0.5 .. 1.5)

final_score = Σ(confidence_i × weight_i) / Σ(weight_i)
```

- Weights are stored in the Redis hash `aegis:agents:accuracy_weights`
  (TTL 7 days) and refreshed nightly by `job_weight_update()` in
  `aegis.scheduler.autonomous`.
- `GraphResult` gained `agent_weights: dict[str, float]` and
  `weight_update_ts: datetime | None` for full auditability of which weights
  produced a given verdict.
- **Redis unavailable → all weights default to 1.0** (equal weighting), so
  the system reduces exactly to the previous behaviour. This is the
  regression guarantee: with no learned weights, output is unchanged.

## Consequences

- **Positive**: the supervisor now amplifies historically-accurate agents
  and damps unreliable ones, improving precision on the ENTER decisions that
  matter most for capital deployment.
- **Positive**: bounded weights (0.5–1.5) prevent any single agent from
  dominating or being silenced entirely — every faculty retains influence.
- **Positive**: fully reversible and observable — weights live in Redis with
  a TTL and are surfaced on every `GraphResult`.
- **Negative**: introduces a dependency on outcome volume; early in a
  deployment (few settled trades) weights stay near the 0.5 default and the
  ensemble behaves like the equal-weight baseline until data accumulates.
- **Negative**: a feedback loop risk — if an agent's votes influence which
  trades execute, its accuracy estimate is conditioned on its own past
  votes. Mitigated by the wide default prior (0.5) and the conservative
  weight range.

## Alternatives Considered

- **Softmax / learned logistic weights**: more expressive but opaque and
  harder to bound; rejected in favour of the transparent linear
  `0.5 + accuracy` map.
- **Per-topic-type weights**: deferred — insufficient outcome volume per
  topic type today; the global weight is the pragmatic first step.
- **Keep equal weights**: rejected — leaves known signal quality on the
  table once outcome data exists.
