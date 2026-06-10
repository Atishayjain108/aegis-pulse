"""
Prometheus metrics. Falls back to no-op stubs when `prometheus_client` is
not installed — same pattern as `aegis.execute.metrics` in Phase 4.

Public surface:
  * `M.fingerprint_picks_total{profile, source}` — Counter
  * `M.playbook_matches_total{name}` — Counter
  * `M.playbook_misses_total` — Counter
  * `M.honeypot_blocks_total{reason}` — Counter
  * `M.honeypot_warns_total` — Counter
  * `M.smoothing_runs_total{agrees}` — Counter
  * `M.smoothing_latency_seconds` — Histogram
  * `M.poisoning_reports_total{decision}` — Counter
  * `M.harden_verdicts_total{source, verdict}` — Counter
"""

from __future__ import annotations

from typing import Any, Protocol


class _CounterLike(Protocol):
    def labels(self, **kwargs: str) -> Any: ...
    def inc(self, amount: float = 1.0) -> None: ...


class _HistogramLike(Protocol):
    def labels(self, **kwargs: str) -> Any: ...
    def observe(self, amount: float) -> None: ...


class _NoOp:
    """Drop-in stub for Counter/Histogram/Gauge."""

    def labels(self, **_kwargs: str) -> _NoOp:  # type: ignore[override]
        return self

    def inc(self, amount: float = 1.0) -> None:
        return None

    def observe(self, amount: float) -> None:
        return None

    def set(self, amount: float) -> None:
        return None


try:  # pragma: no cover — environmental
    from prometheus_client import Counter, Histogram

    _HAS_PROM = True
except ImportError:  # pragma: no cover
    Counter = Histogram = None  # type: ignore[assignment]
    _HAS_PROM = False


def _counter(name: str, doc: str, labels: tuple[str, ...] = ()) -> _CounterLike:
    if not _HAS_PROM:
        return _NoOp()  # type: ignore[return-value]
    return Counter(name, doc, labels)  # type: ignore[no-any-return]


def _hist(
    name: str, doc: str, labels: tuple[str, ...] = (), buckets: tuple[float, ...] | None = None
) -> _HistogramLike:
    if not _HAS_PROM:
        return _NoOp()  # type: ignore[return-value]
    if buckets is None:
        return Histogram(name, doc, labels)  # type: ignore[no-any-return]
    return Histogram(name, doc, labels, buckets=buckets)  # type: ignore[no-any-return]


class M:
    """Bag of Phase 5 metrics. Importable as `from aegis.harden.metrics import M`."""

    fingerprint_picks_total = _counter(
        "aegis_harden_fingerprint_picks_total",
        "TLS/HTTP2 fingerprint selections.",
        ("profile", "source"),
    )
    playbook_matches_total = _counter(
        "aegis_harden_playbook_matches_total",
        "Per-source playbook matches.",
        ("name",),
    )
    playbook_misses_total = _counter(
        "aegis_harden_playbook_misses_total",
        "URLs that found no matching playbook.",
    )
    honeypot_blocks_total = _counter(
        "aegis_harden_honeypot_blocks_total",
        "URLs blocked as honeypots.",
        ("reason",),
    )
    honeypot_warns_total = _counter(
        "aegis_harden_honeypot_warns_total",
        "URLs flagged but not blocked.",
    )
    smoothing_runs_total = _counter(
        "aegis_harden_smoothing_runs_total",
        "Randomized-smoothing inference runs.",
        ("agrees",),
    )
    smoothing_latency_seconds = _hist(
        "aegis_harden_smoothing_latency_seconds",
        "Randomized-smoothing wall-clock latency.",
        (),
        (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
    poisoning_reports_total = _counter(
        "aegis_harden_poisoning_reports_total",
        "Training batches scored for poisoning.",
        ("decision",),
    )
    harden_verdicts_total = _counter(
        "aegis_harden_verdicts_total",
        "Unified Phase 5 verdicts emitted.",
        ("source", "verdict"),
    )


HAS_PROMETHEUS = _HAS_PROM

__all__ = ["M", "HAS_PROMETHEUS"]
