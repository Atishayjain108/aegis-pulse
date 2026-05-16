"""
FastAPI app — `predict` HTTP surface.

Endpoints:
    GET  /healthz          → 200 OK liveness
    GET  /readyz           → 200 OK if InferenceRunner can construct
    POST /predict          → single-trend inference
    POST /predict/batch    → batched inference (≤ PREDICT_BATCH_MAX)
    GET  /metrics          → Prometheus exposition (optional)

Why FastAPI and not Starlette/Flask
-----------------------------------
* Pydantic v2 first-class.
* Automatic OpenAPI 3.1 spec → drives our schemathesis fuzz tests.
* Async-native — InferenceRunner is async; sync frameworks would
  cost an event-loop hop per request.

Doctrine compliance
-------------------
* Imports are guarded — without `fastapi` installed the module raises
  a clean ServingError on `create_app()`. The runtime degrades to
  in-process callers (Phase 2 LangGraph, Phase 4 alerter).
* No singleton runner at module import time. The runner is built
  inside `create_app()` so multiple workers each own theirs.
* `correlation_id` is propagated from the request header (or freshly
  generated) and returned in the response — the entire trace is
  reconstructible from a single id.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..constants import (
    INFERENCE_LATENCY_BUDGET_MS,
    PREDICT_BATCH_MAX,
)
from ..errors import ServingError
from ..inference import InferenceConfig, InferenceRunner
from ..schemas import PredictionBundle

logger = logging.getLogger(__name__)


try:  # pragma: no cover — exercised only when fastapi installed
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse, Response

    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    Header = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment,misc]
    JSONResponse = None  # type: ignore[assignment]
    Response = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]
    _HAS_FASTAPI = False


try:  # pragma: no cover
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
    )

    _HAS_PROM = True
    _REQ_COUNT = Counter(
        "aegis_predict_requests_total",
        "Total prediction requests",
        ["endpoint", "outcome"],
    )
    _REQ_LATENCY = Histogram(
        "aegis_predict_latency_ms",
        "Prediction request latency (ms)",
        ["endpoint"],
        buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500),
    )
    _SLO_BREACH = Counter(
        "aegis_predict_slo_breach_total",
        "Requests that exceeded the latency budget",
        ["endpoint"],
    )
except ImportError:  # pragma: no cover
    _HAS_PROM = False
    CONTENT_TYPE_LATEST = "text/plain"  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Request / response envelopes
# ---------------------------------------------------------------------------
class PredictRequest(BaseModel):
    """One inference request.

    The caller can send either pre-built `signals` (dict rows) or an
    already-built `FeatureWindow`. We do not accept both — fail loud
    on ambiguity.

    `signals` rows must match the shape `build_window_from_rows()`
    expects (see `features/builder.py`).
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = "default"
    trend_id: str
    correlation_id: str | None = None
    signals: list[dict[str, Any]] | None = None
    feature_window: dict[str, Any] | None = None

    horizons: list[int] | None = None  # override default horizons


class PredictResponse(BaseModel):
    """One inference response.

    `bundle` is the canonical Phase 3 output. The remaining fields are
    operational metadata; production callers should treat anything
    outside `bundle` as advisory.
    """

    model_config = ConfigDict(extra="forbid")

    bundle: PredictionBundle
    correlation_id: str
    halt_reasons: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    graph_summary: dict[str, float] = Field(default_factory=dict)
    causal_top: list[dict[str, Any]] = Field(default_factory=list)
    is_heuristic_only: bool = True


class BatchPredictRequest(BaseModel):
    """Many inference requests bundled into one HTTP call."""

    model_config = ConfigDict(extra="forbid")

    requests: list[PredictRequest] = Field(..., min_length=1, max_length=PREDICT_BATCH_MAX)


class BatchPredictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responses: list[PredictResponse]
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def _require_fastapi() -> None:
    if not _HAS_FASTAPI:
        raise ServingError(
            "fastapi not installed — `pip install fastapi uvicorn[standard]` "
            "or use `aegis.predict.inference.InferenceRunner` in-process."
        )


def create_app(
    *,
    config: InferenceConfig | None = None,
    title: str = "AEGIS Predict",
    version: str = "3.0.0",
) -> FastAPI:
    """Build a fresh FastAPI app instance with `runner` baked in.

    A new `InferenceRunner` is constructed per app — call this once per
    worker process, not per request.
    """
    _require_fastapi()

    runner = InferenceRunner(config=config or InferenceConfig())

    @asynccontextmanager
    async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        # Pre-load both predictors so first request isn't penalised.
        runner._ensure_predictors()
        logger.info("aegis-predict serving ready")
        yield
        logger.info("aegis-predict serving shutting down")

    app = FastAPI(
        title=title,
        version=version,
        lifespan=_lifespan,
        # OpenAPI is at /openapi.json, Swagger UI at /docs.
    )

    @app.middleware("http")
    async def _latency_budget_middleware(request, call_next):  # type: ignore[no-untyped-def]
        # Skip middleware overhead for health/metrics — they're checked
        # by external systems and need to be cheap.
        path = request.url.path
        if path in ("/healthz", "/readyz", "/metrics"):
            return await call_next(request)
        t0 = time.monotonic()
        # Pin a single correlation_id for the entire request lifecycle:
        # if the caller supplied one, honour it; else mint here and put
        # it on `request.state` so endpoint code reuses the same value.
        cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        request.state.correlation_id = cid
        try:
            response = await call_next(request)
        except Exception:
            elapsed = (time.monotonic() - t0) * 1000.0
            if _HAS_PROM:
                _REQ_COUNT.labels(endpoint=path, outcome="error").inc()
                _REQ_LATENCY.labels(endpoint=path).observe(elapsed)
            raise
        elapsed = (time.monotonic() - t0) * 1000.0
        response.headers["x-correlation-id"] = cid
        response.headers["x-duration-ms"] = f"{elapsed:.2f}"
        if _HAS_PROM:
            _REQ_COUNT.labels(endpoint=path, outcome=str(response.status_code)).inc()
            _REQ_LATENCY.labels(endpoint=path).observe(elapsed)
            if elapsed > INFERENCE_LATENCY_BUDGET_MS:
                _SLO_BREACH.labels(endpoint=path).inc()
        if elapsed > INFERENCE_LATENCY_BUDGET_MS:
            logger.warning(
                "slo_breach: path=%s duration_ms=%.1f budget_ms=%.0f cid=%s",
                path,
                elapsed,
                INFERENCE_LATENCY_BUDGET_MS,
                cid,
            )
        return response

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "ts": datetime.now(UTC).isoformat()}

    @app.get("/readyz")
    async def readyz() -> dict:
        try:
            runner._ensure_predictors()
            return {"status": "ready"}
        except Exception as exc:  # pragma: no cover
            raise HTTPException(status_code=503, detail=f"not_ready: {exc}") from exc

    @app.post("/predict", response_model=PredictResponse)
    async def predict_endpoint(
        request: Request,
        body: PredictRequest,
    ) -> PredictResponse:
        cid = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
        return await _predict_one(runner, body, cid)

    @app.post("/predict/batch", response_model=BatchPredictResponse)
    async def predict_batch_endpoint(
        request: Request,
        body: BatchPredictRequest,
    ) -> BatchPredictResponse:
        import asyncio as _asyncio

        cid = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
        t0 = time.monotonic()
        results: list[PredictResponse] = await _asyncio.gather(
            *[_predict_one(runner, req, cid) for req in body.requests]
        )
        return BatchPredictResponse(
            responses=list(results),
            duration_ms=(time.monotonic() - t0) * 1000.0,
        )

    if _HAS_PROM:

        @app.get("/metrics")
        async def metrics() -> Response:
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
    else:  # pragma: no cover

        @app.get("/metrics")
        async def metrics_disabled() -> dict:
            return {"status": "disabled", "reason": "prometheus_client_not_installed"}

    return app


async def _predict_one(
    runner: InferenceRunner,
    body: PredictRequest,
    correlation_id_header: str | None,
) -> PredictResponse:
    """Adapter between PredictRequest and runner.run()."""
    if body.signals and body.feature_window:
        raise HTTPException(
            status_code=400,
            detail="provide either `signals` or `feature_window`, not both",
        )
    if not body.signals and not body.feature_window:
        raise HTTPException(
            status_code=400,
            detail="provide either `signals` or `feature_window`",
        )

    correlation_id = body.correlation_id or correlation_id_header or str(uuid.uuid4())
    # If the caller supplied a `feature_window` dict, we currently
    # require them to send signals (binding `feature_window` JSON →
    # FeatureWindow happens in a future enhancement; for now reject).
    if body.feature_window is not None:
        raise HTTPException(
            status_code=501,
            detail="feature_window input is not yet implemented; send `signals` instead",
        )

    # Ensure each signal row's `captured_at` is parsed to datetime.
    rows = list(body.signals or [])
    for r in rows:
        if isinstance(r.get("captured_at"), str):
            try:
                r["captured_at"] = datetime.fromisoformat(r["captured_at"])
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid captured_at in signal id={r.get('id')!r}",
                ) from exc

    # Bind correlation_id by patching the runner config for THIS call.
    cfg = InferenceConfig(
        horizons=tuple(body.horizons) if body.horizons else runner.config.horizons,
        window_size=runner.config.window_size,
        temporal_model=runner.config.temporal_model,
        relational_model=runner.config.relational_model,
        fusion_weights=runner.config.fusion_weights,
        enable_causal=runner.config.enable_causal,
        latency_budget_ms=runner.config.latency_budget_ms,
        hard_timeout_s=runner.config.hard_timeout_s,
        correlation_id=correlation_id,
    )
    one_off = InferenceRunner(
        config=cfg, _temporal=runner._temporal, _relational=runner._relational
    )
    try:
        result = await one_off.run(tenant_id=body.tenant_id, trend_id=body.trend_id, signals=rows)
    except Exception as exc:
        logger.exception("predict failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"predict_failed: {exc}") from exc

    causal_top = [
        {"feature": a.feature, "contribution": a.contribution, "method": a.method}
        for a in result.causal[:5]
    ]
    return PredictResponse(
        bundle=result.bundle,
        correlation_id=correlation_id,
        halt_reasons=list(result.halt_reasons),
        duration_ms=result.duration_ms,
        graph_summary=dict(result.graph_summary),
        causal_top=causal_top,
        is_heuristic_only=result.bundle.is_heuristic_only,
    )
