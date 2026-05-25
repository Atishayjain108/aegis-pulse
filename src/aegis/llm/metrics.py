"""
aegis.llm.metrics — Prometheus metrics for Phase 11
=====================================================

Exposes all LLM call metrics via Prometheus counters, histograms,
and gauges.  Gracefully no-ops when ``prometheus_client`` is not installed.

Metrics exported:
  aegis_llm_requests_total{provider, model, status}
  aegis_llm_request_latency_seconds{provider, model}
  aegis_llm_tokens_total{provider, model, direction}
  aegis_llm_cost_usd_total{provider}
  aegis_llm_provider_health{provider}
  aegis_llm_guardrail_blocks_total{rule}
  aegis_llm_circuit_breaker_trips_total{provider}
  aegis_llm_router_hits_total{route}
  aegis_llm_router_misses_total

Author: AEGIS Engineering
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("aegis.llm.metrics")

# ---------------------------------------------------------------------------
# Graceful no-op when prometheus_client is not installed
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter, Gauge, Histogram

    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    _log.debug("metrics.prometheus_not_installed", hint="pip install prometheus-client")


def _noop(*args: Any, **kwargs: Any) -> Any:
    """No-op callable for metric methods when prometheus is absent."""
    return lambda *a, **kw: None


class _NoOpMetric:
    """Dummy metric object that silently ignores all calls."""

    def labels(self, **kwargs: Any) -> _NoOpMetric:
        return self

    def inc(self, *args: Any, **kwargs: Any) -> None:
        pass

    def observe(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __call__(self, *args: Any, **kwargs: Any) -> _NoOpMetric:
        return self


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

if _PROMETHEUS_AVAILABLE:
    LLM_REQUESTS_TOTAL = Counter(
        "aegis_llm_requests_total",
        "Total LLM completion requests",
        ["provider", "model", "status"],
    )
    LLM_REQUEST_LATENCY = Histogram(
        "aegis_llm_request_latency_seconds",
        "LLM request latency in seconds",
        ["provider", "model"],
        buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
    )
    LLM_TOKENS_TOTAL = Counter(
        "aegis_llm_tokens_total",
        "Total tokens processed",
        ["provider", "model", "direction"],  # direction: input|output
    )
    LLM_COST_USD_TOTAL = Counter(
        "aegis_llm_cost_usd_total",
        "Total estimated LLM cost in USD",
        ["provider"],
    )
    LLM_PROVIDER_HEALTH = Gauge(
        "aegis_llm_provider_health",
        "Provider health status (1=healthy, 0=unhealthy)",
        ["provider"],
    )
    LLM_GUARDRAIL_BLOCKS = Counter(
        "aegis_llm_guardrail_blocks_total",
        "Total guardrail blocks by rule",
        ["rule"],
    )
    LLM_CIRCUIT_BREAKER_TRIPS = Counter(
        "aegis_llm_circuit_breaker_trips_total",
        "Total circuit breaker trips by provider",
        ["provider"],
    )
    LLM_ROUTER_HITS = Counter(
        "aegis_llm_router_hits_total",
        "Semantic router hits (LLM bypassed) by route name",
        ["route"],
    )
    LLM_ROUTER_MISSES = Counter(
        "aegis_llm_router_misses_total",
        "Semantic router misses (LLM required)",
    )
else:
    LLM_REQUESTS_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    LLM_REQUEST_LATENCY = _NoOpMetric()  # type: ignore[assignment]
    LLM_TOKENS_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    LLM_COST_USD_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    LLM_PROVIDER_HEALTH = _NoOpMetric()  # type: ignore[assignment]
    LLM_GUARDRAIL_BLOCKS = _NoOpMetric()  # type: ignore[assignment]
    LLM_CIRCUIT_BREAKER_TRIPS = _NoOpMetric()  # type: ignore[assignment]
    LLM_ROUTER_HITS = _NoOpMetric()  # type: ignore[assignment]
    LLM_ROUTER_MISSES = _NoOpMetric()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helper functions (thin wrappers — keep metrics code out of business logic)
# ---------------------------------------------------------------------------


def record_request(
    *,
    provider: str,
    model: str,
    status: str,  # "ok" | "error" | "timeout" | "circuit_open"
    latency_ms: float,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    """Record a completed LLM request in all relevant metrics."""
    LLM_REQUESTS_TOTAL.labels(provider=provider, model=model, status=status).inc()
    LLM_REQUEST_LATENCY.labels(provider=provider, model=model).observe(latency_ms / 1000)
    LLM_TOKENS_TOTAL.labels(provider=provider, model=model, direction="input").inc(input_tokens)
    LLM_TOKENS_TOTAL.labels(provider=provider, model=model, direction="output").inc(output_tokens)
    if cost_usd > 0:
        LLM_COST_USD_TOTAL.labels(provider=provider).inc(cost_usd)


def record_guardrail_block(rule: str) -> None:
    """Record a guardrail block event."""
    LLM_GUARDRAIL_BLOCKS.labels(rule=rule).inc()


def record_circuit_trip(provider: str) -> None:
    """Record a circuit breaker trip."""
    LLM_CIRCUIT_BREAKER_TRIPS.labels(provider=provider).inc()


def record_router_hit(route: str) -> None:
    """Record a semantic router hit (LLM bypassed)."""
    LLM_ROUTER_HITS.labels(route=route).inc()


def record_router_miss() -> None:
    """Record a semantic router miss."""
    LLM_ROUTER_MISSES.inc()


def update_provider_health(provider: str, *, healthy: bool) -> None:
    """Update the health gauge for a provider."""
    LLM_PROVIDER_HEALTH.labels(provider=provider).set(1.0 if healthy else 0.0)


def is_prometheus_available() -> bool:
    """Return True if prometheus_client is installed."""
    return _PROMETHEUS_AVAILABLE
