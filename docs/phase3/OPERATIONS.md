# Operations Runbook

How to run, observe, and recover Phase 3 in production.

## Daily checks

```bash
# 1. Service health
curl -fs http://localhost:8000/healthz

# 2. Inference latency rollup (last hour)
psql "$AEGIS_PG_DSN" -c "
SELECT
    model_id,
    percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50,
    percentile_cont(0.90) WITHIN GROUP (ORDER BY duration_ms) AS p90,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms) AS p99,
    COUNT(*) AS n
FROM predictions
WHERE finished_at >= now() - INTERVAL '1 hour'
GROUP BY model_id
ORDER BY n DESC;
"

# 3. Halt-reason rollup (last 24h)
psql "$AEGIS_PG_DSN" -c "
SELECT
    unnest(halt_reasons) AS reason,
    COUNT(*) AS n
FROM prediction_audit
WHERE created_at >= now() - INTERVAL '24 hours'
GROUP BY reason
ORDER BY n DESC;
"

# 4. Production model per name
psql "$AEGIS_PG_DSN" -c "
SELECT name, version, trained_at, n_train_samples
FROM model_manifest
WHERE stage = 'production'
ORDER BY name;
"
```

## Promoting a new model

```python
import asyncio
from datetime import datetime, timezone
from aegis.predict.backtest import run_backtest, BacktestSpec
from aegis.predict.registry import ModelStore, evaluate_promotion
from aegis.predict.schemas import ModelKind, ModelManifest
from aegis.predict.models.factory import load_model

async def promote(samples, weights_bytes, model_name, version):
    model = load_model(model_name)

    async def predict_fn(window):
        b = await model.predict(window)
        return b.by_horizon(24) or b.predictions[0]

    folds, agg = await run_backtest(
        samples, predict_fn,
        horizon=24, model_id=f"{model_name}@{version}",
    )
    print(f"backtest: {agg}")

    store = ModelStore(root="/var/lib/aegis/models")
    incumbent_manifest = store.get_production(model_name)
    if incumbent_manifest:
        # Look up incumbent's stored backtest from `model_manifest` table.
        incumbent_metrics = ...  # fetch via SQL
    else:
        incumbent_metrics = None

    decision = evaluate_promotion(agg, incumbent_metrics)
    print(f"gate: {decision}")
    if not decision.promote:
        return decision

    manifest = ModelManifest(
        model_id=f"{model_name}@{version}",
        name=model_name,
        kind=ModelKind.TEMPORAL,
        version=version,
        weights_uri=f"s3://aegis-models/{model_name}/{version}.bin",
        sha256="0" * 64,                       # placeholder; store fills it
        trained_at=datetime.now(timezone.utc),
        train_window_start=...,
        train_window_end=...,
        n_train_samples=len(samples),
    )
    store.register(manifest, weights_bytes)
    if decision.suggested_stage == "shadow":
        # Stay in 'staging'; the shadow timer (72h by default) is
        # tracked by the inference service, which switches stage
        # to 'production' after the shadow period.
        pass
    return decision
```

## Auto-rollback

The inference service computes a rolling 24-hour `breakout_precision`
per model and compares to the `backtest_summary.breakout_precision`
recorded at promotion time. If `live - promotion > 5%`, the service:

1. Calls `evaluate_rollback(live, promotion)`.
2. On `True`, calls `store.archive(name, version)`.
3. Looks up the previous production version via the audit log.
4. Promotes the previous version back to production.
5. Emits an `aegis_predict_auto_rollback_total` Prometheus counter.

To run rollback evaluation manually:

```python
from aegis.predict.registry import evaluate_rollback
decision = evaluate_rollback(live_metrics, promotion_metrics)
if decision.promote:  # True means "do roll back"
    store.archive(name, version)
```

## Restoring from corruption

If the on-disk registry is damaged:

```bash
# 1. Stop inference workers
sudo systemctl stop aegis-predict

# 2. Restore from MinIO snapshot (Phase 1 nightly backup)
rclone sync minio:aegis-backups/registry/$(date -d yesterday +%Y%m%d) \
            /var/lib/aegis/models

# 3. Verify integrity
python - <<'PY'
from aegis.predict.registry import ModelStore
store = ModelStore(root="/var/lib/aegis/models")
for name in store.list_models():
    for ver in store.list_versions(name):
        try:
            store.load_artifact(name, ver)
        except Exception as e:
            print(f"BAD {name}@{ver}: {e}")
PY

# 4. Restart
sudo systemctl start aegis-predict
```

## Latency-budget breach

If `latency_budget_exceeded` halt_reasons fire frequently:

1. Check `prediction_audit.halt_reasons` distribution — what fraction
   of requests are breaching?
2. Check inference duration percentiles per model. If one model's
   p99 has regressed:
   - Roll the model back via `evaluate_rollback`.
   - File an issue against the model with the slow prediction's
     `correlation_id`.
3. If all models are slow, suspect the host:
   - CPU governor in `powersave` mode? `sudo cpupower frequency-set -g performance`.
   - Container memory pressure? `docker stats`.
   - Phase 1 DB slowness in feature builder? `EXPLAIN ANALYSE` on
     `fetch_recent_signals`.

## Observability

Prometheus metrics exposed at `/metrics`:

| Metric | Labels | Purpose |
|---|---|---|
| `aegis_predict_requests_total` | endpoint, outcome | request rate |
| `aegis_predict_latency_ms` | endpoint | latency histogram |
| `aegis_predict_slo_breach_total` | endpoint | budget breaches |

Recommended Grafana panels:

* p50/p90/p99 latency per endpoint, last 1h.
* Halt-reason counts by reason, last 24h.
* `is_heuristic_only=true` rate (when this rises, neural predictors
  are degrading).
* Active production model per name.

## Disaster recovery RPO/RTO

| Asset | RPO | RTO | Mechanism |
|---|---|---|---|
| `predictions` table | 15 min | 60 min | TimescaleDB WAL + pgBackRest |
| `model_manifest` rows | 15 min | 60 min | TimescaleDB WAL + pgBackRest |
| ModelStore artifacts | 1 day | 30 min | restic to MinIO + Backblaze B2 |
| ModelStore manifests | 1 day | 30 min | same |
| Audit sidecars (MinIO) | 0 (object lock) | 0 (read-only) | WORM bucket |

## Index of error codes

| Code | Class | Meaning |
|---|---|---|
| AEGIS-PREDICT-1001 | feature | malformed signal row |
| AEGIS-PREDICT-2001 | model | predictor produced invalid output |
| AEGIS-PREDICT-2002 | model | predictor timeout |
| AEGIS-PREDICT-3001 | registry | model not found |
| AEGIS-PREDICT-3002 | registry | sha256 mismatch |
| AEGIS-PREDICT-3003 | registry | model load error |
| AEGIS-PREDICT-4001 | backtest | lookahead leakage detected |
| AEGIS-PREDICT-5001 | serving | invalid request |

Each code's dedicated `docs/errors/AEGIS-PREDICT-NNNN.md` file (TODO)
should describe diagnosis and remediation. For now, see
`src/aegis/predict/errors.py` for the source of truth.
