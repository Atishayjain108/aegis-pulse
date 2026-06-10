"""Prometheus metric definitions for all AEGIS phases.

All metrics use raw ``prometheus_client`` — consistent with Phase 11 metrics
and the project-wide decision to avoid the OTel metrics API / exporter stack.

Usage:
    from aegis.observability.metrics import ingest_signals_total, start_metrics_server

    ingest_signals_total.labels(source="reddit-rss").inc()
    start_metrics_server(port=8001)  # exposes /metrics

Graceful degradation: if ``prometheus_client`` is absent every object is a
``_NoOpMetric`` stub. Import always succeeds.
"""

from __future__ import annotations

import threading
from typing import Any

import structlog

_log = structlog.get_logger("aegis.observability.metrics")

AEGIS_OBSERVABILITY_VERSION = "14.0.0"

METRICS_PORT = 8001

# ── Optional prometheus_client — graceful degradation ────────────────────────

try:
    from prometheus_client import Counter as _Counter
    from prometheus_client import Gauge as _Gauge
    from prometheus_client import Histogram as _Histogram
    from prometheus_client import start_http_server as _start_http_server

    _PROMETHEUS_AVAILABLE = True
except Exception:  # pragma: no cover — no prometheus_client in some envs
    _Counter = _Gauge = _Histogram = _start_http_server = None  # type: ignore[assignment, misc]
    _PROMETHEUS_AVAILABLE = False


# ── No-op stubs for environments without prometheus_client ───────────────────


class _NoOpMetric:
    """Swallows all metric operations silently."""

    def labels(self, **_kwargs: object) -> _NoOpMetric:
        return self

    def inc(self, _amount: float = 1) -> None:
        pass

    def dec(self, _amount: float = 1) -> None:
        pass

    def set(self, _value: float) -> None:
        pass

    def observe(self, _value: float) -> None:
        pass

    def time(self) -> _NoOpMetric:
        return self

    def __enter__(self) -> _NoOpMetric:
        return self

    def __exit__(self, *_: object) -> None:
        pass


# ── Metric factory ────────────────────────────────────────────────────────────

def _lookup_existing(name: str) -> Any:
    """Return the already-registered prometheus collector for ``name``, or None.

    Used when another module (e.g. aegis.core.metrics) has already registered
    the same metric name before this module was imported. Avoids a duplicate-
    timeseries ValueError by returning the existing collector so callers can
    still increment/observe it.
    """
    if not _PROMETHEUS_AVAILABLE:
        return None
    try:
        from prometheus_client import REGISTRY

        collectors = REGISTRY._names_to_collectors  # type: ignore[attr-defined]
        # Counter appends _total; check both the bare name and _total-suffixed form.
        for candidate in (name, f"{name}_total", name.removesuffix("_total")):
            if candidate in collectors:
                return collectors[candidate]
    except Exception:  # pragma: no cover
        pass
    return None


def _counter(name: str, doc: str, labels: list[str] | None = None) -> Any:
    if _PROMETHEUS_AVAILABLE and _Counter is not None:
        try:
            return _Counter(name, doc, labels or [])
        except ValueError:
            existing = _lookup_existing(name)
            return existing if existing is not None else _NoOpMetric()
        except Exception:  # pragma: no cover
            pass
    return _NoOpMetric()


def _gauge(name: str, doc: str, labels: list[str] | None = None) -> Any:
    if _PROMETHEUS_AVAILABLE and _Gauge is not None:
        try:
            return _Gauge(name, doc, labels or [])
        except ValueError:
            existing = _lookup_existing(name)
            return existing if existing is not None else _NoOpMetric()
        except Exception:  # pragma: no cover
            pass
    return _NoOpMetric()


def _histogram(
    name: str,
    doc: str,
    labels: list[str] | None = None,
    buckets: list[float] | None = None,
) -> Any:
    if _PROMETHEUS_AVAILABLE and _Histogram is not None:
        try:
            kwargs: dict[str, Any] = {}
            if buckets is not None:
                kwargs["buckets"] = buckets
            return _Histogram(name, doc, labels or [], **kwargs)
        except ValueError:
            existing = _lookup_existing(name)
            return existing if existing is not None else _NoOpMetric()
        except Exception:  # pragma: no cover
            pass
    return _NoOpMetric()


# ── Phase 1 — Ingest ─────────────────────────────────────────────────────────

ingest_signals_total = _counter(
    "aegis_obs_ingest_signals_total",  # obs-namespace avoids collision with aegis.core.metrics
    "Total signals ingested by source adapter (Phase 14 observability view)",
    ["source"],
)

ingest_errors_total = _counter(
    "aegis_obs_ingest_errors_total",  # obs-namespace avoids collision with aegis.core.metrics
    "Total ingest errors by source adapter and error type (Phase 14 observability view)",
    ["source", "error_type"],
)

ingest_latency_ms = _histogram(
    "aegis_ingest_latency_ms",
    "End-to-end ingest latency: scrape → dedup → Postgres commit (ms)",
    ["source"],
    buckets=[10, 50, 100, 500, 1_000, 5_000, 10_000],
)

signals_per_source = _gauge(
    "aegis_signals_per_source",
    "Latest signal count per source adapter",
    ["source"],
)

dedup_ratio = _gauge(
    "aegis_dedup_ratio",
    "Fraction of signals removed as duplicates in the last batch (0–1)",
    ["source"],
)

# ── Phase 2 — Agents ─────────────────────────────────────────────────────────

agent_task_duration_ms = _histogram(
    "aegis_agent_task_duration_ms",
    "Agent node execution time (ms)",
    ["node"],
    buckets=[100, 500, 1_000, 5_000, 30_000, 120_000],
)

agent_task_errors_total = _counter(
    "aegis_agent_task_errors_total",
    "Agent node errors by node name and error type",
    ["node", "error_type"],
)

llm_requests_total = _counter(
    "aegis_obs_llm_requests_total",  # obs-namespace avoids collision with aegis.llm.metrics
    "LLM API requests by provider and model (Phase 14 observability view)",
    ["provider", "model"],
)

llm_tokens_total = _counter(
    "aegis_obs_llm_tokens_total",  # obs-namespace avoids collision with aegis.llm.metrics
    "Total LLM tokens consumed (input + output) by provider (Phase 14 observability view)",
    ["provider", "direction"],
)

llm_cost_usd = _gauge(
    "aegis_obs_llm_cost_usd",  # obs-namespace avoids collision with aegis_llm_cost_usd_total in Phase 11
    "Cumulative estimated LLM cost in USD by provider (Phase 14 observability view)",
    ["provider"],
)

# ── Phase 3 — Predictions ─────────────────────────────────────────────────────

model_inference_latency_ms = _histogram(
    "aegis_model_inference_latency_ms",
    "Model inference latency (ms) — p99 SLA: < 500 ms",
    ["model_type"],
    buckets=[10, 50, 100, 200, 500, 1_000, 2_000],
)

model_prediction_precision = _gauge(
    "aegis_model_prediction_precision",
    "Rolling 7-day precision on holdout set (0–1)",
)

model_drift_score = _gauge(
    "aegis_obs_model_drift_score",  # obs-namespace avoids collision with aegis.core.metrics
    "Data drift KL-divergence score — alert if > 0.1 (Phase 14 observability view)",
    ["feature"],
)

# ── Phase 4 — Execution ───────────────────────────────────────────────────────

alert_delivered_total = _counter(
    "aegis_alert_delivered_total",
    "Alerts successfully delivered by channel",
    ["channel"],
)

alert_delivery_latency_ms = _histogram(
    "aegis_alert_delivery_latency_ms",
    "Alert delivery latency: trigger → user notification (ms) — p99 SLA: < 10 000 ms",
    ["channel"],
    buckets=[500, 1_000, 2_000, 5_000, 10_000, 30_000],
)

killswitch_engaged_total = _counter(
    "aegis_killswitch_engaged_total",
    "Number of killswitch trip events",
)

# ── Cross-cutting ─────────────────────────────────────────────────────────────

database_query_duration_ms = _histogram(
    "aegis_database_query_duration_ms",
    "Postgres query latency (ms)",
    ["operation"],
    buckets=[1, 5, 10, 50, 100, 500, 1_000],
)

redis_operation_duration_ms = _histogram(
    "aegis_redis_operation_duration_ms",
    "Redis command latency (ms)",
    ["command"],
    buckets=[0.5, 1, 5, 10, 50, 100, 500],
)

cache_hit_ratio = _gauge(
    "aegis_cache_hit_ratio",
    "LLM cache hit ratio in the last measurement window (0–1)",
)

# ── Metrics HTTP server ───────────────────────────────────────────────────────

_metrics_server_started = False
_metrics_lock = threading.Lock()


def start_metrics_server(port: int = METRICS_PORT) -> None:
    """Start the Prometheus HTTP metrics server on ``port``.

    Thread-safe and idempotent — safe to call from multiple services.
    No-ops if prometheus_client is unavailable.
    """
    global _metrics_server_started  # noqa: PLW0603

    with _metrics_lock:
        if _metrics_server_started:
            return
        if _PROMETHEUS_AVAILABLE and _start_http_server is not None:
            try:
                _start_http_server(port)
                _metrics_server_started = True
                _log.info("metrics.server_started", port=port)
            except Exception:  # pragma: no cover
                _log.warning("metrics.server_start_failed", port=port)
        else:  # pragma: no cover
            _log.warning("metrics.server_start_failed", port=port)
