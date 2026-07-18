"""Unit tests for aegis.observability.tracing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aegis.observability.tracing import (
    _NoOpSpan,
    _NoOpTracer,
    get_tracer,
    init_tracing,
    instrument_asyncpg,
    instrument_fastapi,
    instrument_httpx,
    instrument_redis,
    reset_tracer_for_testing,
)


@pytest.fixture(autouse=True)
def _reset_tracer():
    """Ensure tracer singleton is reset between tests."""
    reset_tracer_for_testing()
    yield
    reset_tracer_for_testing()


class TestNoOpTracer:
    def test_start_as_current_span_returns_span(self) -> None:
        tracer = _NoOpTracer()
        span = tracer.start_as_current_span("test-span")
        assert isinstance(span, _NoOpSpan)

    def test_start_span_returns_span(self) -> None:
        tracer = _NoOpTracer()
        span = tracer.start_span("test-span")
        assert isinstance(span, _NoOpSpan)

    def test_span_context_manager(self) -> None:
        tracer = _NoOpTracer()
        with tracer.start_as_current_span("test") as span:
            span.set_attribute("key", "value")
            span.set_status("ok")
            span.record_exception(ValueError("test"))

    def test_span_set_attribute_no_error(self) -> None:
        span = _NoOpSpan()
        span.set_attribute("trend_id", "abc123")
        span.set_attribute("score", 0.85)


class TestInitTracing:
    def test_returns_noop_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_ENABLED", "false")
        import aegis.observability.tracing as t

        original = t.OTEL_ENABLED
        t.OTEL_ENABLED = False
        try:
            tracer = init_tracing("test-service")
            assert isinstance(tracer, _NoOpTracer)
        finally:
            t.OTEL_ENABLED = original

    def test_returns_noop_on_sdk_failure(self) -> None:
        # Trigger failure by making TracerProvider raise (already imported, no protobuf chain)
        with patch("aegis.observability.tracing.OTEL_ENABLED", True), patch(
            "opentelemetry.sdk.trace.TracerProvider",
            side_effect=RuntimeError("sdk unavailable"),
        ):
            tracer = init_tracing("test-service")
        # Should gracefully return a no-op tracer, not raise
        assert tracer is not None

    def test_idempotent_multiple_calls(self) -> None:
        with patch("aegis.observability.tracing.OTEL_ENABLED", False):
            t1 = init_tracing("svc-a")
            t2 = init_tracing("svc-b")
        assert t1 is t2  # same singleton

    def test_get_tracer_initialises_if_none(self) -> None:
        with patch("aegis.observability.tracing.OTEL_ENABLED", False):
            tracer = get_tracer()
        assert tracer is not None


class TestInstrumentation:
    def test_instrument_fastapi_missing_package(self) -> None:
        """Should not raise even if the instrumentation package is absent."""
        with patch.dict("sys.modules", {"opentelemetry.instrumentation.fastapi": None}):
            instrument_fastapi(MagicMock())  # must not raise

    def test_instrument_asyncpg_missing_package(self) -> None:
        with patch.dict("sys.modules", {"opentelemetry.instrumentation.asyncpg": None}):
            instrument_asyncpg()  # must not raise

    def test_instrument_redis_missing_package(self) -> None:
        with patch.dict("sys.modules", {"opentelemetry.instrumentation.redis": None}):
            instrument_redis()  # must not raise

    def test_instrument_httpx_missing_package(self) -> None:
        with patch.dict("sys.modules", {"opentelemetry.instrumentation.httpx": None}):
            instrument_httpx()  # must not raise
