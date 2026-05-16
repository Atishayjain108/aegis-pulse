"""
HTTP serving layer for Phase 3 predictions.

`app.py` defines a FastAPI app exposing:
    GET  /healthz        — liveness
    GET  /readyz         — readiness (checks Phase 1 pool if present)
    POST /predict        — synchronous inference for one trend
    POST /predict/batch  — batched inference (up to PREDICT_BATCH_MAX)
    GET  /metrics        — Prometheus exposition (when prometheus_client present)

The server is intentionally thin — no business logic lives here. It
adapts HTTP requests into `InferenceRunner.run()` calls and returns
the resulting `PredictionBundle` plus audit sidecar fields.

A latency-budget middleware enforces the p99 < 500 ms SLA: any request
exceeding `INFERENCE_LATENCY_BUDGET_MS` is logged with a `slo_breach`
counter and the response includes a `latency_budget_exceeded` halt
reason — but the prediction is still returned (Phase 4 needs the
fallback).

Doctrine: when FastAPI is missing, importing `app` raises a clean
ServingError with the install hint. Every other module continues to
work; serving is one optional surface among many.
"""

from .app import (
    PredictRequest,
    PredictResponse,
    create_app,
)

__all__ = [
    "PredictRequest",
    "PredictResponse",
    "create_app",
]
