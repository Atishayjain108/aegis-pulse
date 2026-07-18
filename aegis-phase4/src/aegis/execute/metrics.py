"""Prometheus metrics for Phase 4.

This module is deliberately tolerant: if ``prometheus_client`` is not
installed, every metric becomes a silent no-op. That way the package
still imports and the test suite runs cleanly in minimal environments.

All metric names live under the ``aegis_execute_`` prefix. The metric
catalog matches `config/README.md`; any rename must update both the
dashboard JSON queries and that catalog.

Usage::

    from aegis.execute.metrics import metrics

    metrics.alerts_total.labels(verdict="ENTER", source="phase2_and_phase3",
                                priority="1").inc()
    with metrics.compose_latency_ms.time():
        alert = compose(ci)

Histograms expose a ``.time()`` context manager that records elapsed
**milliseconds** (not seconds — matches the dashboard's ms axes).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Final

try:
    from prometheus_client import (  # type: ignore[import-not-found]
        REGISTRY,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
    )

    _HAVE_PROM = True
except ImportError:  # pragma: no cover - exercised by the no-prom path
    CollectorRegistry = None  # type: ignore[assignment]
    Counter = Gauge = Histogram = None  # type: ignore[assignment]
    REGISTRY = None  # type: ignore[assignment]
    _HAVE_PROM = False


# Histogram bucket boundaries (milliseconds). Tuned for Phase 4:
#   compose: pure-CPU, expect <50ms
#   delivery: network calls, expect <2s
_LATENCY_BUCKETS_MS: Final = (
    1.0,
    2.5,
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1000.0,
    2500.0,
    5000.0,
    10000.0,
    30000.0,
)


# ---------------------------------------------------------------------------
# No-op fallbacks (used when prometheus_client is absent OR when explicitly
# constructed with `enabled=False`)
# ---------------------------------------------------------------------------
class _NoopMetric:
    """A drop-in stand-in for Counter/Gauge/Histogram. Every op is silent."""

    __slots__ = ()

    def labels(self, *_args, **_kwargs) -> _NoopMetric:
        return self

    def inc(self, _amount: float = 1.0) -> None:
        return None

    def dec(self, _amount: float = 1.0) -> None:
        return None

    def set(self, _value: float) -> None:
        return None

    def observe(self, _value: float) -> None:
        return None

    @contextmanager
    def time(self):
        # Yields nothing useful; pure no-op context manager.
        yield self


# ---------------------------------------------------------------------------
# Real metric construction helpers
# ---------------------------------------------------------------------------
def _make_counter(name: str, doc: str, labels: list[str], registry) -> object:
    if _HAVE_PROM:
        return Counter(name, doc, labelnames=labels, registry=registry)
    return _NoopMetric()


def _make_gauge(name: str, doc: str, labels: list[str], registry) -> object:
    if _HAVE_PROM:
        return Gauge(name, doc, labelnames=labels, registry=registry)
    return _NoopMetric()


def _make_histogram(name: str, doc: str, labels: list[str], registry) -> object:
    if _HAVE_PROM:
        return _MsHistogram(
            Histogram(
                name,
                doc,
                labelnames=labels,
                buckets=_LATENCY_BUCKETS_MS,
                registry=registry,
            )
        )
    return _NoopMetric()


class _MsHistogram:
    """Wraps a prometheus_client Histogram so ``.time()`` records ms."""

    __slots__ = ("_h",)

    def __init__(self, h) -> None:
        self._h = h

    def labels(self, *args, **kwargs) -> _MsHistogram:
        return _MsHistogram(self._h.labels(*args, **kwargs))

    def observe(self, value_ms: float) -> None:
        self._h.observe(float(value_ms))

    @contextmanager
    def time(self):
        start = time.monotonic()
        try:
            yield self
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            self._h.observe(elapsed_ms)


# ---------------------------------------------------------------------------
# Metric catalog
# ---------------------------------------------------------------------------
class _ExecuteMetrics:
    """Container of every Phase 4 metric, instantiated against one registry."""

    __slots__ = (
        "alerts_total",
        "compose_latency_ms",
        "deliveries_total",
        "delivery_latency_ms",
        "gate_blocks_total",
        "intake_handle_failed_total",
        "killswitch_tripped",
        "outbox_pending",
        "registry",
    )

    def __init__(self, registry=None) -> None:
        if _HAVE_PROM:
            self.registry = registry if registry is not None else CollectorRegistry()
        else:
            self.registry = None

        self.alerts_total = _make_counter(
            "aegis_execute_alerts_total",
            "Total alerts produced by the Phase 4 composer.",
            ["verdict", "source", "priority"],
            self.registry,
        )
        self.compose_latency_ms = _make_histogram(
            "aegis_execute_compose_latency_ms",
            "End-to-end latency of policy.composer.compose (milliseconds).",
            [],
            self.registry,
        )
        self.deliveries_total = _make_counter(
            "aegis_execute_deliveries_total",
            "Notifier deliveries by channel and status (success/failure/timeout/skipped).",
            ["channel", "status"],
            self.registry,
        )
        self.delivery_latency_ms = _make_histogram(
            "aegis_execute_delivery_latency_ms",
            "Notifier per-call latency by channel (milliseconds).",
            ["channel"],
            self.registry,
        )
        self.outbox_pending = _make_gauge(
            "aegis_execute_outbox_pending",
            "Outbox rows in 'pending' state, by tenant.",
            ["tenant"],
            self.registry,
        )
        self.killswitch_tripped = _make_gauge(
            "aegis_execute_killswitch_tripped",
            "Killswitch state: 0=ARMED, 1=TRIPPED.",
            [],
            self.registry,
        )
        self.gate_blocks_total = _make_counter(
            "aegis_execute_gate_blocks_total",
            "Risk-gate downgrades to BLOCK by reason code.",
            ["reason"],
            self.registry,
        )
        # audit P1-6/P3-1: intake handler failures left pending for reclaim. A
        # rising rate here means verdicts are repeatedly failing to compose —
        # the Prometheus IntakeHandlerFailing alert fires on it.
        self.intake_handle_failed_total = _make_counter(
            "aegis_execute_intake_handle_failed_total",
            "Intake stream messages whose handler raised (left pending for retry).",
            ["stream"],
            self.registry,
        )


# Process-wide singleton bound to the default registry (or a fresh one).
metrics: Final = _ExecuteMetrics(registry=REGISTRY if _HAVE_PROM else None)


def new_metrics_for_test() -> _ExecuteMetrics:
    """Return a metrics instance bound to a fresh registry — for tests."""
    if _HAVE_PROM:
        return _ExecuteMetrics(registry=CollectorRegistry())
    return _ExecuteMetrics(registry=None)


__all__ = ["metrics", "new_metrics_for_test"]
