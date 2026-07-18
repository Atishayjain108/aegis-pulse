"""AEGIS Observability — Phase 14.

Exports:
  - init_tracing   — configure OpenTelemetry traces (OTLP → Jaeger)
  - init_metrics   — expose Prometheus /metrics endpoint
  - init_sentry    — configure Sentry error tracking
  - get_tracer     — return the process-singleton tracer
  - instrument_fastapi — auto-instrument a FastAPI app instance
"""

from __future__ import annotations

from aegis.observability.metrics import (
    AEGIS_OBSERVABILITY_VERSION,
    agent_task_duration_ms,
    agent_task_errors_total,
    alert_delivered_total,
    alert_delivery_latency_ms,
    cache_hit_ratio,
    database_query_duration_ms,
    dedup_ratio,
    ingest_errors_total,
    ingest_latency_ms,
    ingest_signals_total,
    killswitch_engaged_total,
    llm_cost_usd,
    llm_requests_total,
    llm_tokens_total,
    model_drift_score,
    model_inference_latency_ms,
    model_prediction_precision,
    redis_operation_duration_ms,
    signals_per_source,
    start_metrics_server,
)
from aegis.observability.sentry_init import init_sentry
from aegis.observability.tracing import get_tracer, init_tracing, instrument_fastapi

__all__ = [
    "AEGIS_OBSERVABILITY_VERSION",
    "agent_task_duration_ms",
    "agent_task_errors_total",
    "alert_delivered_total",
    "alert_delivery_latency_ms",
    "cache_hit_ratio",
    "database_query_duration_ms",
    "dedup_ratio",
    "get_tracer",
    "ingest_errors_total",
    "ingest_latency_ms",
    "ingest_signals_total",
    "init_sentry",
    "init_tracing",
    "instrument_fastapi",
    "killswitch_engaged_total",
    "llm_cost_usd",
    "llm_requests_total",
    "llm_tokens_total",
    "model_drift_score",
    "model_inference_latency_ms",
    "model_prediction_precision",
    "redis_operation_duration_ms",
    "signals_per_source",
    "start_metrics_server",
]
