# ADR-0014: Observability & SLO Framework

## Status

Accepted — 2026-05-31

## Context

Phase 6 (Capital Execution) requires full auditability. Every arbitrage signal
must be replayed via distributed traces. Every anomaly must be alertable within
seconds. The existing ad-hoc `structlog` + raw `prometheus_client` usage covers
individual services but provides no end-to-end trace context, no cross-service
latency SLOs, and no centralised log aggregation.

## Decision

1. **OpenTelemetry SDK** in every service — traces exported via OTLP gRPC to
   Jaeger (already running on port 4317). Auto-instrumentation for FastAPI,
   asyncpg, httpx, and Redis.
2. **Prometheus** for metrics scrape (already configured at :9091); all new
   counters/histograms/gauges use `prometheus_client` directly — consistent with
   existing Phase 11 metrics and the project-wide no-OTel-metrics-API rule.
3. **Loki** for log aggregation (labels-first, no full-text index on content).
   Promtail ships Docker container logs to Loki; Grafana queries both Prometheus
   and Loki from a single UI.
4. **Explicit SLI/SLO/error-budget** per critical service — defined in
   `docs/slo/aegis-slos.md` and enforced via Prometheus alerting rules.
5. **Sentry** for exception tracking + release correlation. Disabled when
   `SENTRY_DSN` is unset (default in dev).
6. All observability code lives in `src/aegis/observability/` — a direct
   subpackage of the main `aegis` namespace (no workspace member needed; no
   state-mutating DB or I/O at import time).

## Rationale

- **OpenTelemetry is vendor-neutral** — switching from Jaeger to Tempo/Grafana
  Cloud is a config change, not a code change.
- **OTLP gRPC (existing dep)** is used instead of the deprecated Jaeger Thrift
  exporter — Jaeger has supported OTLP since v1.35.
- **prometheus_client directly** avoids the `opentelemetry-exporter-prometheus`
  adapter and its double-registration footgun; the existing Phase 11 metrics
  already set the precedent.
- **Loki** is free, disk-efficient (gzip-compressed chunks), and integrates
  natively with Grafana without a separate index service.
- **Graceful degradation** — every `init_*` function catches exceptions and logs
  a warning rather than crashing the service. Observability must never be the
  reason a production service fails to start.

## Trade-offs

| Concern | Impact |
|---------|--------|
| CPU overhead | +3–5% per service (BatchSpanProcessor is async) |
| Memory overhead | +30–50 MB per service (OTel SDK buffers) |
| Operational surface | 2 new containers (Loki, Promtail) |
| Sampling | Default 100% in dev; set `OTEL_TRACES_SAMPLE_RATE=0.1` in prod |
| Payoff | <5 min MTTR on production incidents; full Phase 6 trade replay |

## Alternatives Rejected

- **Zipkin** — narrower ecosystem; Jaeger already deployed.
- **Elastic APM** — expensive at scale; not self-hostable for free.
- **Datadog** — SaaS-only; vendor lock-in unacceptable.
- **OTel metrics API + prometheus exporter** — double-registration issues with
  existing `prometheus_client` usage in Phase 11; more complexity, same result.
