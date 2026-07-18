# Phase 4 Integration Guide

## How Phase 2 + Phase 3 feed Phase 4

Phase 4 is fully **duck-typed**: the bridges declare runtime-checkable
Protocols, not hard imports of `aegis.agents` or `aegis.predict`. This
means a Phase 4-only checkout can build, lint, test, and run without the
other phases installed — and integration is a matter of pointing the
intake worker at the right Redis streams.

### Direct (in-process) integration

When Phases 2 / 3 are in the same Python process, just call:

```python
from aegis.execute.bridge.phase2 import from_graph_result
from aegis.execute.bridge.phase3 import from_inference_result
from aegis.execute.bridge.types import ComposerInput
from aegis.execute.pipeline import Pipeline
from aegis.execute.store.repository import AlertRepository

pipe = Pipeline(repository=AlertRepository(pool=shared_pool))

# Per trend
ci_p2 = from_graph_result(graph_result, tenant_id=tid)
ci_p3 = from_inference_result(inference_result, tenant_id=tid)

# Merge (Phase 4's IntakeWorker does this automatically when fed via streams)
merged = ComposerInput(
    tenant_id=tid, trend_id=graph_result.trend_id,
    phase2_verdict=ci_p2.phase2_verdict,
    phase2_score=ci_p2.phase2_score,
    phase2_confidence=ci_p2.phase2_confidence,
    phase2_priority=ci_p2.phase2_priority,
    phase2_halt_reason=ci_p2.phase2_halt_reason,
    phase2_blocked_by=ci_p2.phase2_blocked_by,
    phase3_p_breakout_24h=ci_p3.phase3_p_breakout_24h,
    phase3_p_decline_6h=ci_p3.phase3_p_decline_6h,
    phase3_p_saturation=ci_p3.phase3_p_saturation,
    phase3_expected_margin_usd=ci_p3.phase3_expected_margin_usd,
    phase3_loss_probability=ci_p3.phase3_loss_probability,
    phase3_confidence=ci_p3.phase3_confidence,
    phase3_policy_action=ci_p3.phase3_policy_action,
)

outcome = await pipe.submit(merged, unit_cost_usd=1.50)
```

### Decoupled (Redis Streams) integration — recommended

Phase 2 and Phase 3 publish JSON-encoded messages to two Redis streams:

```
aegis:phase2:graph_results
aegis:phase3:inference_results
```

Phase 4's `IntakeWorker` consumes both streams under consumer group
`aegis-execute-intake`. If a trend's Phase 2 and Phase 3 messages arrive
within 30 s of each other, the worker merges them. Otherwise it submits
the single side as soon as it arrives (or evicts after 30 s if no
counterpart appears).

#### Phase 2 message shape

```jsonc
{
  "trend_id": "string",
  "final_verdict": "ENTER" | "HOLD" | "EXIT" | "BLOCK" | "DEGRADED",
  "final_score": 0.0,
  "final_confidence": 0.0,
  "final_priority": 0,
  "halt_reason": null,
  "blocked_by": [],
  "title": "optional",
  "narrative": "optional",
  "correlation_id": "optional",
  "decision_window": "default"
}
```

#### Phase 3 message shape

```jsonc
{
  "trend_id": "string",
  "bundle": {
    "predictions": [
      {
        "horizon_hours": 24,
        "p_breakout": 0.85,
        "p_decline": 0.10,
        "p_saturation": 0.50,
        "expected_margin_usd": 3.20,
        "loss_probability": 0.18,
        "confidence": 0.72
      }
    ]
  },
  "policy_action": "enter",
  "correlation_id": "optional"
}
```

(Phase 3 `InferenceResult` already conforms to this; the bridge falls
back to a flat `predictions` list at the top level if `bundle` is absent.)

## Killswitch interaction

The killswitch lives in Redis at the key configured by
`AEGIS_EXECUTE_KILLSWITCH_KEY` (default `aegis:execute:killswitch`).

| Value | Meaning |
|---|---|
| Key absent | Switch is **ARMED** (normal operation) |
| `"TRIPPED"` | Switch is **TRIPPED** — pipeline returns early; drainer does not fan-out |

The killswitch is **fail-closed in production**: if Redis is configured
but the connection fails, `is_tripped()` returns `True`. In dev (no
Redis client at all), the default is fail-open so a laptop without Redis
still works.

## RLS contract

Every repository method calls
`SELECT set_config('app.current_tenant', $1, true)` before touching
tenant-scoped tables. This matches the Phase 1 RLS convention. The
`alerts`, `alert_outbox`, `alert_deliveries`, and `execution_intents`
tables all have RLS policies; `killswitch_audit` is intentionally
RLS-free for cross-tenant ops visibility.

## Shared pool pattern

`AlertRepository` accepts a `pool=` kwarg. If omitted, it lazy-imports
`aegis.db.pool.get_shared_pool()`. Set the pool once at startup with
`aegis.db.pool.set_shared_pool(my_pool)` per the Phase 1 convention.

In tests, pass an explicit `pool=stub` or use the in-memory
`FakeRepository` shipped under `tests/integration/execute/conftest.py`.

## Observability

| Source | Where |
|---|---|
| Structured logs | structlog → stderr (CLI) or stdout (server, captured by Docker) |
| Tracing | OpenTelemetry not enabled by default; add via FastAPI instrumentation |
| Metrics | `/metrics` Prometheus endpoint (returns 204 if `prometheus_client` not installed) |
| SSE events | `/stream` with `X-Aegis-Tenant` header |

## Error code conventions

All Phase 4 errors carry an `AEGIS-EXEC-NNNN` code. The mapping lives in
`aegis/execute/errors.py` and matching docs under `docs/errors/`.
