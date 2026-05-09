"""Prometheus metrics registry.

Every metric name in the project catalogue (Phase 14 of the master prompt)
lives here. Rationale for centralisation:

- A single place to audit metric cardinality — no call site can silently
  introduce an unbounded label dimension (`user_id`, `url`, ...).
- Lazy initialisation: we create the metric on first access so unit tests
  that never import a given subsystem pay no overhead.
- ``resilient_call_observe()`` is the exact hook ``core.resilience`` looks
  up by name — the resilience module works **without** this module loaded
  (the lookup silently no-ops), but when both are present they compose.
- Histograms use the buckets declared in ``constants.METRICS_HISTOGRAM_BUCKETS_SECONDS``
  to give consistent latency resolution across services.

Usage::

    from aegis.core.metrics import scrape_signals_ingested, scrape_latency

    scrape_signals_ingested.labels(platform="reddit", tier="T1_intent").inc()
    with scrape_latency.labels(platform="reddit").time():
        ...

Author: AEGIS Pulse Team
Relationship: imported by scrapers, feature pipelines, agents, execution
engine. Exposed to Prometheus via ``aiohttp_prometheus`` in the API layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client import (
    REGISTRY as DEFAULT_REGISTRY,  # the default global registry — we use it
)

from aegis.constants import METRICS_HISTOGRAM_BUCKETS_SECONDS

# =============================================================================
# Registry management
# =============================================================================

# Public registry. Tests call ``reset_registry()`` to swap this for a clean
# CollectorRegistry; all metrics below rebuild themselves against the swap.
_registry: CollectorRegistry = DEFAULT_REGISTRY

# Cache of created metrics so reset_registry() can rebuild them.
_metrics: dict[str, Counter | Gauge | Histogram] = {}


def get_registry() -> CollectorRegistry:
    """Return the currently-active registry."""
    return _registry


def reset_registry() -> None:
    """Replace the registry with a fresh one. Intended for tests.

    After this call, any cached metric objects held by callers are STALE and
    will write to the old registry (which is now orphaned). Callers should
    re-import the metric symbols from this module if they need a reference.
    """
    global _registry  # noqa: PLW0603
    _registry = CollectorRegistry()
    _metrics.clear()
    # Rebuild the well-known metrics so module-level symbols still resolve.
    _build_all_metrics()


def render() -> tuple[bytes, str]:
    """Render the current registry in Prometheus exposition format.

    Returns:
        ``(body_bytes, content_type_header)`` suitable for returning from a
        ``/metrics`` HTTP endpoint.
    """
    return generate_latest(_registry), CONTENT_TYPE_LATEST


# =============================================================================
# Metric factories (deduplicating — Prometheus refuses duplicate names)
# =============================================================================


def _counter(name: str, description: str, labelnames: tuple[str, ...] = ()) -> Counter:
    if name in _metrics:
        m = _metrics[name]
        if not isinstance(m, Counter):
            raise TypeError(f"metric {name!r} already exists with different type")
        return m
    c = Counter(name, description, labelnames=labelnames, registry=_registry)
    _metrics[name] = c
    return c


def _gauge(name: str, description: str, labelnames: tuple[str, ...] = ()) -> Gauge:
    if name in _metrics:
        m = _metrics[name]
        if not isinstance(m, Gauge):
            raise TypeError(f"metric {name!r} already exists with different type")
        return m
    g = Gauge(name, description, labelnames=labelnames, registry=_registry)
    _metrics[name] = g
    return g


def _histogram(
    name: str,
    description: str,
    labelnames: tuple[str, ...] = (),
    buckets: tuple[float, ...] = METRICS_HISTOGRAM_BUCKETS_SECONDS,
) -> Histogram:
    if name in _metrics:
        m = _metrics[name]
        if not isinstance(m, Histogram):
            raise TypeError(f"metric {name!r} already exists with different type")
        return m
    h = Histogram(
        name, description, labelnames=labelnames, buckets=buckets, registry=_registry,
    )
    _metrics[name] = h
    return h


# =============================================================================
# Metric catalogue — per Phase 14 of the master prompt
# =============================================================================


def _build_all_metrics() -> None:
    """Create every catalogue metric on the current registry.

    Called once at import time AND whenever ``reset_registry()`` is invoked.
    The module-level symbols below are (re)bound to the freshly-created
    objects via ``globals()`` — this is the only clean way to keep Python
    identifiers pointing at the live metrics across a registry swap.
    """
    # Each entry is (symbol_name, factory_call).
    # fmt: off
    defs: list[tuple[str, Counter | Gauge | Histogram]] = [
        # --- Ingestion -------------------------------------------------------
        ("ingest_signals_total",
         _counter("aegis_ingest_signals_total",
                  "Total signals ingested, by platform + tier.",
                  ("platform", "tier"))),
        ("ingest_errors_total",
         _counter("aegis_ingest_errors_total",
                  "Ingestion errors, labeled by platform and error category.",
                  ("platform", "category"))),
        ("ingest_latency_seconds",
         _histogram("aegis_ingest_latency_seconds",
                    "Time from upstream event to signal persistence.",
                    ("platform",))),

        # --- Scrape / anti-bot -----------------------------------------------
        ("scrape_proxy_ban_total",
         _counter("aegis_scrape_proxy_ban_total",
                  "Proxies flagged as banned.",
                  ("platform",))),
        ("scrape_captcha_total",
         _counter("aegis_scrape_captcha_total",
                  "CAPTCHA encounters during scrape.",
                  ("platform", "resolved"))),
        ("scrape_requests_total",
         _counter("aegis_scrape_requests_total",
                  "Outbound scrape requests, by platform and method.",
                  ("platform", "method"))),

        # --- resilient_call observability ------------------------------------
        ("resilient_attempts_total",
         _counter("aegis_resilient_attempts_total",
                  "resilient_call attempts, by policy and outcome.",
                  ("policy", "outcome"))),
        ("resilient_attempt_duration_seconds",
         _histogram("aegis_resilient_attempt_duration_seconds",
                    "Per-attempt duration for resilient_call operations.",
                    ("policy", "outcome"))),

        # --- Model inference -------------------------------------------------
        ("model_inference_latency_seconds",
         _histogram("aegis_model_inference_latency_seconds",
                    "Model inference latency. SLO: p99 < 500 ms.",
                    ("model", "version"),
                    # Tighter buckets for the 500ms SLO.
                    (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0))),
        ("model_precision_7d",
         _gauge("aegis_model_precision_7d",
                "Rolling 7-day precision of the production model.",
                ("model", "version"))),
        ("model_drift_score",
         _gauge("aegis_model_drift_score",
                "Evidently-computed drift score (higher = more drift).",
                ("model", "feature"))),

        # --- Agent / LLM cost ------------------------------------------------
        ("agent_task_duration_seconds",
         _histogram("aegis_agent_task_duration_seconds",
                    "Per-agent task duration.",
                    ("agent", "status"))),
        ("agent_task_retry_total",
         _counter("aegis_agent_task_retry_total",
                  "Retries for agent tasks.",
                  ("agent",))),
        ("agent_llm_tokens_total",
         _counter("aegis_agent_llm_tokens_total",
                  "LLM tokens consumed, by agent + provider + direction.",
                  ("agent", "provider", "direction"))),
        ("agent_llm_cost_usd_total",
         _counter("aegis_agent_llm_cost_usd_total",
                  "Estimated LLM cost in USD (float counter).",
                  ("agent", "provider"))),

        # --- Alerts ----------------------------------------------------------
        ("alert_delivery_latency_seconds",
         _histogram("aegis_alert_delivery_latency_seconds",
                    "Alert trigger → delivery latency.",
                    ("channel",))),
        ("alert_ack_latency_seconds",
         _histogram("aegis_alert_ack_latency_seconds",
                    "Alert delivery → human-ack latency.",
                    ("channel",))),

        # --- Execution (Phase 6 hooks wired here early) ----------------------
        ("execution_revenue_usd_total",
         _counter("aegis_execution_revenue_usd_total",
                  "Cumulative execution revenue in USD.",
                  ("channel",))),
        ("execution_margin_ratio",
         _gauge("aegis_execution_margin_ratio",
                "Current rolling margin ratio (revenue - cost) / revenue.",
                ("channel",))),
        ("execution_stop_loss_total",
         _counter("aegis_execution_stop_loss_total",
                  "Stop-loss events triggered.",
                  ("tier",))),

        # --- Saturation & compliance ----------------------------------------
        ("saturation_index",
         _histogram("aegis_saturation_index",
                    "Distribution of SaaS saturation index across active trends.",
                    (),
                    (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0))),
        ("compliance_block_total",
         _counter("aegis_compliance_block_total",
                  "Compliance-engine blocks by risk category.",
                  ("category",))),

        # --- DB / cache hygiene ---------------------------------------------
        ("db_pool_size",
         _gauge("aegis_db_pool_size",
                "Live size of the asyncpg connection pool.")),
        ("db_pool_in_use",
         _gauge("aegis_db_pool_in_use",
                "Currently-borrowed asyncpg connections.")),
        ("db_query_duration_seconds",
         _histogram("aegis_db_query_duration_seconds",
                    "Query duration by operation class.",
                    ("op",))),
        ("cache_ops_total",
         _counter("aegis_cache_ops_total",
                  "Redis cache ops, by op type and hit/miss.",
                  ("op", "result"))),
    ]
    # fmt: on

    # Bind module-level symbols. After reset_registry(), imports that did
    # `from aegis.core.metrics import scrape_requests_total` will still hold
    # a reference to the OLD metric. Tests that need the new one must re-import.
    for symbol_name, metric_obj in defs:
        globals()[symbol_name] = metric_obj


# Build once at import time.
_build_all_metrics()


# =============================================================================
# Hooks consumed by other modules
# =============================================================================


def resilient_call_observe(
    *,
    policy: str,
    outcome: str,
    attempt: int,   # kept for signature parity; counter increments per call anyway
    duration: float | None = None,
) -> None:
    """Emit metrics for one ``resilient_call`` attempt.

    ``core.resilience`` imports this lazily and falls through if unavailable.
    Keeping the function here (not in ``resilience``) breaks what would
    otherwise be a circular import.
    """
    # attempt label not used (would balloon cardinality). We keep the kwarg
    # anyway so the resilience module stays loosely coupled to the metric
    # shape — future changes don't need to touch both sides.
    _ = attempt
    # Resolve the metrics via globals() — after reset_registry() these symbols
    # are rebound; reading _metrics by the Prom-name key would also work but
    # is one more indirection.
    globals()["resilient_attempts_total"].labels(policy=policy, outcome=outcome).inc()
    if duration is not None:
        globals()["resilient_attempt_duration_seconds"].labels(
            policy=policy, outcome=outcome,
        ).observe(duration)


# =============================================================================
# Module-level symbols are bound dynamically inside _build_all_metrics().
# We cannot annotate them as Final because mypy requires Final names to be
# initialised at the annotation site. Instead, we expose stub declarations
# under TYPE_CHECKING so callers (and the type-checker) see the correct
# Counter/Gauge/Histogram types, while the runtime binding stays dynamic.
# =============================================================================

if TYPE_CHECKING:
    ingest_signals_total: Counter
    ingest_errors_total: Counter
    ingest_latency_seconds: Histogram
    scrape_proxy_ban_total: Counter
    scrape_captcha_total: Counter
    scrape_requests_total: Counter
    resilient_attempts_total: Counter
    resilient_attempt_duration_seconds: Histogram
    model_inference_latency_seconds: Histogram
    model_precision_7d: Gauge
    model_drift_score: Gauge
    agent_task_duration_seconds: Histogram
    agent_task_retry_total: Counter
    agent_llm_tokens_total: Counter
    agent_llm_cost_usd_total: Counter
    alert_delivery_latency_seconds: Histogram
    alert_ack_latency_seconds: Histogram
    execution_revenue_usd_total: Counter
    execution_margin_ratio: Gauge
    execution_stop_loss_total: Counter
    saturation_index: Histogram
    compliance_block_total: Counter
    db_pool_size: Gauge
    db_pool_in_use: Gauge
    db_query_duration_seconds: Histogram
    cache_ops_total: Counter


__all__ = [
    # Functions
    "get_registry",
    "render",
    "reset_registry",
    "resilient_call_observe",
    # Catalogue
    "agent_llm_cost_usd_total",
    "agent_llm_tokens_total",
    "agent_task_duration_seconds",
    "agent_task_retry_total",
    "alert_ack_latency_seconds",
    "alert_delivery_latency_seconds",
    "cache_ops_total",
    "compliance_block_total",
    "db_pool_in_use",
    "db_pool_size",
    "db_query_duration_seconds",
    "execution_margin_ratio",
    "execution_revenue_usd_total",
    "execution_stop_loss_total",
    "ingest_errors_total",
    "ingest_latency_seconds",
    "ingest_signals_total",
    "model_drift_score",
    "model_inference_latency_seconds",
    "model_precision_7d",
    "resilient_attempt_duration_seconds",
    "resilient_attempts_total",
    "saturation_index",
    "scrape_captcha_total",
    "scrape_proxy_ban_total",
    "scrape_requests_total",
]
