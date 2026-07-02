# AEGIS-EVOLVE-0011 — Shadow model registration failed

## What it means

The weekly retraining pipeline produced a candidate model that beat the
champion's AUC, but writing the candidate to `model_candidates` as a shadow
(`is_shadow = TRUE`) failed. The candidate will **not** enter the 72-hour
parallel evaluation window and therefore cannot be promoted.

## Why it usually happens

* The Postgres pool is unavailable or the connection dropped mid-write.
* Migration `0012_shadow_models.sql` has not been applied — the
  `is_shadow` / `shadow_registered_at` columns do not exist yet.
* RLS: `app.current_tenant` was not set on the connection.

## Remediation

1. Verify migration 0012 is applied:
   `psql -h localhost -p 5433 -U aegis_app -d aegis -c "\d model_candidates"`
   and confirm the `is_shadow` column exists.
2. Check Postgres health: `uv run aegis doctor`.
3. Re-run the retrain manually: `uv run aegis evolve retrain` — shadow
   registration is retried on every run that finds an improved candidate.

## If remediation fails

Collect the `evolve.shadow_register_failed` structlog event (it includes the
underlying DB error string) and the output of `uv run aegis evolve status`.
