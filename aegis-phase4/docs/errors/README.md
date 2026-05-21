# AEGIS-EXEC error codes


| Code | Message | Retryable |
|---|---|---|
| [`AEGIS-EXEC-0001`](AEGIS-EXEC-0001.md) | Composer received neither GraphResult nor InferenceResult. | — |
| [`AEGIS-EXEC-0002`](AEGIS-EXEC-0002.md) | GraphResult.trend_id != InferenceResult.trend_id. | — |
| [`AEGIS-EXEC-0003`](AEGIS-EXEC-0003.md) | Composed verdict not in allowed set. | — |
| [`AEGIS-EXEC-0010`](AEGIS-EXEC-0010.md) | Expected margin below floor; alert blocked. | — |
| [`AEGIS-EXEC-0011`](AEGIS-EXEC-0011.md) | Loss probability above ceiling; alert blocked. | — |
| [`AEGIS-EXEC-0012`](AEGIS-EXEC-0012.md) | Overall confidence below floor; alert blocked. | — |
| [`AEGIS-EXEC-0013`](AEGIS-EXEC-0013.md) | Tenant is on the operator blocklist. | — |
| [`AEGIS-EXEC-0020`](AEGIS-EXEC-0020.md) | Kill-switch is TRIPPED; dispatch halted. | ✅ |
| [`AEGIS-EXEC-0021`](AEGIS-EXEC-0021.md) | Redis unreachable; kill-switch defaults to TRIPPED (fail-closed). | ✅ |
| [`AEGIS-EXEC-0025`](AEGIS-EXEC-0025.md) | Failed to insert alert into outbox; durability not guaranteed. | ✅ |
| [`AEGIS-EXEC-0026`](AEGIS-EXEC-0026.md) | Shared Postgres pool not initialized; call set_shared_pool() first. | — |
| [`AEGIS-EXEC-0030`](AEGIS-EXEC-0030.md) | Notifier channel exceeded per-call timeout budget. | ✅ |
| [`AEGIS-EXEC-0031`](AEGIS-EXEC-0031.md) | Notifier received non-2xx HTTP response. | ✅ |
| [`AEGIS-EXEC-0032`](AEGIS-EXEC-0032.md) | Notifier disabled due to missing configuration (non-fatal). | — |
| [`AEGIS-EXEC-0033`](AEGIS-EXEC-0033.md) | Webhook notifier requires HMAC key; channel disabled. | — |
