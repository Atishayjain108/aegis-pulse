# Phase 4 — Observability config

```
config/
├── prometheus.yml                              ← scrape targets for api + drain
└── grafana/
    ├── dashboards/
    │   └── aegis-execute.json                   ← 8-panel overview dashboard
    └── provisioning/
        ├── datasources/prometheus.yml           ← auto-wires Prometheus
        └── dashboards/dashboards.yml            ← auto-loads dashboards/
```

These files are mounted **read-only** into the Prometheus and Grafana
containers by `docker/docker-compose.execute.yml`:

| Container | Mount |
|---|---|
| `prometheus` | `config/prometheus.yml` → `/etc/prometheus/prometheus.yml` |
| `grafana`    | `config/grafana/provisioning` → `/etc/grafana/provisioning` |
| `grafana`    | `config/grafana/dashboards`   → `/var/lib/grafana/dashboards` |

## Dashboard panels (8)

| Panel | Query (Prometheus) |
|---|---|
| Killswitch | `max(aegis_execute_killswitch_tripped)` |
| Outbox pending | `sum(aegis_execute_outbox_pending)` |
| Delivery success (1h) | `success-rate over rate(aegis_execute_deliveries_total)` |
| Compose latency p95 | `histogram_quantile(0.95, aegis_execute_compose_latency_ms_bucket)` |
| Alert rate by verdict | `sum(rate(aegis_execute_alerts_total[1m])) by (verdict)` |
| Delivery rate by channel & status | `sum(rate(aegis_execute_deliveries_total[1m])) by (channel, status)` |
| Delivery latency p50/p95 by channel | `histogram_quantile over aegis_execute_delivery_latency_ms_bucket` |
| Risk gate blocks by reason | `sum(increase(aegis_execute_gate_blocks_total[5m])) by (reason)` |

## Metrics the application is expected to emit

All metric names live under the `aegis_execute_` prefix.

| Metric | Type | Labels | Source |
|---|---|---|---|
| `aegis_execute_alerts_total` | counter | `verdict`, `source`, `priority` | `pipeline.submit` |
| `aegis_execute_compose_latency_ms` | histogram | — | `policy.composer.compose` |
| `aegis_execute_deliveries_total` | counter | `channel`, `status` | `outbox.drainer._dispatch_one` |
| `aegis_execute_delivery_latency_ms` | histogram | `channel` | `notifiers.*.send` |
| `aegis_execute_outbox_pending` | gauge | `tenant` | periodic poll from drainer |
| `aegis_execute_killswitch_tripped` | gauge (0/1) | — | `killswitch.switch.state()` |
| `aegis_execute_gate_blocks_total` | counter | `reason` | `risk.gates.GateChain.run` |

These are stable names — if the Python instrumentation is renamed,
update this catalog and the dashboard queries together.

## Adding a panel

1. Open Grafana → AEGIS Pulse → Execute → "Add panel".
2. Use the metrics catalog above to pick a query.
3. Save the dashboard.
4. Click the "share" icon → "Export" → "Save to file" → drop the JSON
   into `config/grafana/dashboards/` next to `aegis-execute.json`.
5. Restart Grafana (or wait 30 s — the provisioner polls).

## Adding an alert

Prometheus rule files live under `config/prometheus.rules.d/` (create
the directory) and are wired in via `prometheus.yml`'s `rule_files:`
section. Example rule:

```yaml
groups:
  - name: aegis-execute
    rules:
      - alert: ExecuteOutboxBacklog
        expr: sum(aegis_execute_outbox_pending) > 200
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "Outbox backlog > 200 for 5m on Phase 4"
```

Reload Prometheus (`docker compose restart prometheus`) to pick it up.
