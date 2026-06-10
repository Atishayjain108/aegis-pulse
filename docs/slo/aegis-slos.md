# AEGIS SLO Framework

Updated: 2026-05-31  
Owner: AEGIS Pulse Team  
Review cadence: monthly (error-budget review), quarterly (SLO target revision)

---

## Service Level Indicators (SLIs) and Objectives (SLOs)

### Phase 1 — Ingest Pipeline

| SLI | Measurement | SLO Target | Error Budget (30 days) |
|-----|------------|------------|------------------------|
| Scrape availability | signals ingested per hour > 0 | 99% | 7.2 h / month |
| Ingest latency p99 | scrape start → Postgres commit | < 5 s | 1% of requests |
| Data quality precision | post-dedup signal precision | > 95% | 5% false-positive rate |
| Adapter success rate | adapters returning ≥ 1 signal | > 80% | 20% adapter failures tolerated |

### Phase 2 — Agent Intelligence

| SLI | Measurement | SLO Target | Error Budget (30 days) |
|-----|------------|------------|------------------------|
| Pipeline SLA | scout start → herald emit | < 2 min | 2.88 h / month |
| LLM completion success | successful LLM calls (all providers) | > 99% | 3.6 min / day |
| Decision throughput | `graph_results` stream entries/hour | > 10 | 0% zero-output hours |
| Stream backpressure | `aegis:phase2:graph_results` length | < 8 000 entries | alert at 80% cap |

### Phase 3 — Predictions

| SLI | Measurement | SLO Target | Error Budget (30 days) |
|-----|------------|------------|------------------------|
| Inference latency p99 | `InferenceRunner.run()` wall time | < 500 ms | 1% of requests |
| Heuristic availability | fallback-free verdict rate | > 99.9% | 43 min / month |
| Model freshness | age of deployed model | < 7 days | re-train trigger at 7 days |
| Prediction AUC | rolling 7-day holdout AUC | > 0.92 | 0.02 drift tolerance |

### Phase 4 — Execution & Alerts

| SLI | Measurement | SLO Target | Error Budget (30 days) |
|-----|------------|------------|------------------------|
| Alert delivery latency p99 | Redis XADD → user notification | < 10 s | 1% of alerts |
| Outbox drain lag | oldest undelivered outbox row age | < 60 s | alert at 30 s |
| Killswitch operability | trip/arm round-trip < 500 ms | 99.99% | 2.6 s / month |
| Audit write success | audit log entries written | 100% | no tolerance |

### Phase 10 — Data Lake

| SLI | Measurement | SLO Target | Error Budget (30 days) |
|-----|------------|------------|------------------------|
| Daily lake refresh success | Prefect flow `daily-lake-refresh` success | > 99% | 7.2 h / month |
| DuckDB query latency p95 | `/datalake/query` endpoint | < 5 s | 5% of queries |
| Gold layer freshness | age of latest Gold partition | < 26 h | 2 h tolerance past midnight |

### Phase 11 — LLM Orchestration

| SLI | Measurement | SLO Target | Error Budget (30 days) |
|-----|------------|------------|------------------------|
| Provider fallback rate | requests requiring > 1 provider attempt | < 20% | alert at 15% |
| Gateway latency p99 | first-token time (all providers) | < 3 s | 1% of requests |
| Guardrail block rate | `GuardrailBlock` fraction of completions | < 0.1% | alert at 0.05% |
| Cache hit rate | LLM cache hits / total requests | > 30% | alert if drops below 20% |

---

## Error Budget Policy

```
error_budget_minutes = (1 - slo_target) × 43_200   # minutes in 30-day month
spent_percent = actual_downtime_minutes / error_budget_minutes
```

| Spent % | Action |
|---------|--------|
| > 50% | Alert on-call; hold non-critical feature work |
| > 80% | Incident declared; postmortem required within 48 h |
| 100% | Feature freeze for that service until budget recovers |

---

## Prometheus Alerting Rules

```yaml
# --- SLO breach warnings (>50% error budget spent in a 5-min window) ---
groups:
  - name: aegis-slo
    rules:

      - alert: IngestAvailabilityBreach
        expr: rate(aegis_ingest_signals_total[1h]) == 0
        for: 5m
        labels: { severity: warning, phase: "1" }
        annotations:
          summary: "No signals ingested in the last hour"

      - alert: Phase3InferenceLatencySLO
        expr: histogram_quantile(0.99, rate(aegis_model_inference_latency_ms_bucket[5m])) > 500
        for: 5m
        labels: { severity: warning, phase: "3" }
        annotations:
          summary: "Model inference p99 latency > 500 ms (SLA breach)"

      - alert: Phase4AlertDeliveryLatencySLO
        expr: histogram_quantile(0.99, rate(aegis_alert_delivery_latency_ms_bucket[5m])) > 10000
        for: 5m
        labels: { severity: critical, phase: "4" }
        annotations:
          summary: "Alert delivery p99 latency > 10 s (SLA breach)"

      - alert: KillswitchEngaged
        expr: aegis_killswitch_engaged_total > 0
        for: 0m
        labels: { severity: critical, phase: "4" }
        annotations:
          summary: "AEGIS killswitch has been tripped"

      - alert: LLMAllProvidersFailed
        expr: increase(aegis_llm_all_providers_failed_total[5m]) > 0
        for: 0m
        labels: { severity: critical, phase: "11" }
        annotations:
          summary: "All LLM providers failed — agent pipeline degraded"
```

---

## Trace-based SLO (Phase 6 prerequisite)

Every arbitrage signal must carry a `trace_id` end-to-end:

```
scrape_topic()           → trace_id injected at harvest start
  → run_trend()          → trace propagated via W3C baggage
    → InferenceRunner    → child span per model
      → alert pipeline   → delivery span with trace_id logged
```

Replay procedure: given `trend_id`, query Jaeger for all spans with
`trace_id` from the associated `graph_results` stream entry. Full causal
chain reconstructable within 30 days (Jaeger retention).
