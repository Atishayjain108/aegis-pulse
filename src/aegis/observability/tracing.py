"""OpenTelemetry tracing initialisation for AEGIS services.

Usage:
    from aegis.observability.tracing import init_tracing, get_tracer, instrument_fastapi

    # At service startup (e.g. FastAPI lifespan):
    init_tracing(service_name="aegis-dashboard")
    instrument_fastapi(app)

    # In any module:
    tracer = get_tracer()
    with tracer.start_as_current_span("my-operation") as span:
        span.set_attribute("trend_id", trend_id)
        ...

Graceful degradation: if the OTLP endpoint is unreachable or OTel packages are
absent, every function returns a no-op tracer — the service never crashes.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    import fastapi

_log = structlog.get_logger("aegis.observability.tracing")

# ── Environment variables ────────────────────────────────────────────────────

OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() == "true"
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "0.3.0")
DEPLOYMENT_ENV = os.getenv("AEGIS_ENV", "dev")

# ── Module-level singleton ───────────────────────────────────────────────────

_tracer: Any = None  # opentelemetry.trace.Tracer | _NoOpTracer


class _NoOpSpan:
    """Returned by _NoOpTracer when OTel is unavailable."""

    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def set_attribute(self, _key: str, _value: object) -> None:
        pass

    def set_status(self, *_: object) -> None:
        pass

    def record_exception(self, *_: object) -> None:
        pass


class _NoOpTracer:
    """Minimal tracer shim used when OpenTelemetry cannot be initialised."""

    def start_as_current_span(self, name: str, **_kwargs: object) -> _NoOpSpan:
        return _NoOpSpan()

    def start_span(self, name: str, **_kwargs: object) -> _NoOpSpan:
        return _NoOpSpan()


def init_tracing(service_name: str = "aegis") -> Any:
    """Initialise OTel tracing with OTLP gRPC export.

    Safe to call multiple times — subsequent calls return the existing tracer.
    Returns a no-op tracer if OTEL_ENABLED=false or if the SDK is unavailable.
    """
    global _tracer  # noqa: PLW0603

    if _tracer is not None:
        return _tracer

    if not OTEL_ENABLED:
        _log.info("tracing.disabled", reason="OTEL_ENABLED=false")
        _tracer = _NoOpTracer()
        return _tracer

    try:
        from opentelemetry import trace  # noqa: PLC0415
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import (  # noqa: PLC0415
            DEPLOYMENT_ENVIRONMENT,
            SERVICE_NAME,
            Resource,
        )
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415

        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                DEPLOYMENT_ENVIRONMENT: DEPLOYMENT_ENV,
                "service.version": SERVICE_VERSION,
            }
        )

        exporter = OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _tracer = trace.get_tracer(f"aegis.{service_name}", SERVICE_VERSION)
        _log.info(
            "tracing.initialised",
            service=service_name,
            otlp_endpoint=OTLP_ENDPOINT,
        )

    except Exception:  # pragma: no cover — infrastructure not present in unit tests
        _log.warning("tracing.init_failed", reason="falling back to no-op tracer")
        _tracer = _NoOpTracer()

    return _tracer


def get_tracer() -> Any:
    """Return the process-singleton tracer, initialising with defaults if needed."""
    if _tracer is None:
        init_tracing()
    return _tracer


def instrument_fastapi(app: fastapi.FastAPI) -> None:  # type: ignore[name-defined]
    """Auto-instrument a FastAPI application with OTel request tracing."""
    try:
        from opentelemetry.instrumentation.fastapi import (  # noqa: PLC0415
            FastAPIInstrumentor,  # type: ignore[import-not-found]
        )

        FastAPIInstrumentor.instrument_app(app)
        _log.info("tracing.fastapi_instrumented")
    except Exception:  # pragma: no cover
        _log.warning("tracing.fastapi_instrument_failed")


def instrument_asyncpg() -> None:
    """Auto-instrument asyncpg with OTel query tracing."""
    try:
        from opentelemetry.instrumentation.asyncpg import (  # noqa: PLC0415
            AsyncPGInstrumentor,  # type: ignore[import-not-found]
        )

        AsyncPGInstrumentor().instrument()
        _log.info("tracing.asyncpg_instrumented")
    except Exception:  # pragma: no cover
        _log.warning("tracing.asyncpg_instrument_failed")


def instrument_redis() -> None:
    """Auto-instrument Redis with OTel command tracing."""
    try:
        from opentelemetry.instrumentation.redis import (  # noqa: PLC0415
            RedisInstrumentor,  # type: ignore[import-not-found]
        )

        RedisInstrumentor().instrument()
        _log.info("tracing.redis_instrumented")
    except Exception:  # pragma: no cover
        _log.warning("tracing.redis_instrument_failed")


def instrument_httpx() -> None:
    """Auto-instrument httpx with OTel HTTP client tracing."""
    try:
        from opentelemetry.instrumentation.httpx import (  # noqa: PLC0415
            HTTPXClientInstrumentor,  # type: ignore[import-not-found]
        )

        HTTPXClientInstrumentor().instrument()
        _log.info("tracing.httpx_instrumented")
    except Exception:  # pragma: no cover
        _log.warning("tracing.httpx_instrument_failed")


def instrument_all() -> None:
    """Convenience: instrument all supported libraries at once."""
    instrument_asyncpg()
    instrument_redis()
    instrument_httpx()


def reset_tracer_for_testing() -> None:
    """Reset the singleton so unit tests get a fresh no-op tracer."""
    global _tracer  # noqa: PLW0603
    _tracer = None
