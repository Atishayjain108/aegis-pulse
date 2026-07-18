# AEGIS-EXEC-0003 — Composed verdict not in allowed set.

**Symbolic name:** `EXEC_COMPOSER_INVALID_VERDICT`

**Component:** Phase 4 / `aegis.execute`

**Retryable:** no — fail-fast

## Likely causes

- Phase 2 and Phase 3 inputs were both empty for this trend.
- An upstream worker emitted a malformed message that the bridge could not parse.
- The composer was called directly without a `ComposerInput`.

## Diagnosis

1. Search the structlog output for `error_code=AEGIS-EXEC-0003`.
2. Inspect the surrounding context fields — Phase 4 always emits
   `tenant_id`, `trend_id`, and `alert_id` where applicable.
3. Cross-reference the operator dashboard at `/dashboard/` for the
   affected tenant.
4. If the upstream is Phase 2 / Phase 3, check the relevant Redis stream
   (`aegis:phase2:graph_results` or `aegis:phase3:inference_results`)
   for the matching `correlation_id`.

## Remediation

- For BLOCK / gate-downgrade codes (0010–0019): the system is behaving
  correctly; review whether the threshold itself needs adjustment.
- For kill-switch codes (0020–0024): trip / arm via the CLI or API; see
  `docs/phase4/OPERATIONS.md`.
- For pool / outbox codes (0025–0029): ensure `set_shared_pool()` runs
  at startup; check Postgres availability.
- For notifier codes (0030–0033): inspect the failing channel's config;
  the registry will auto-skip it if the relevant env var is empty.

## Related

- `docs/phase4/ARCHITECTURE.md` — design rationale
- `docs/phase4/OPERATIONS.md` — incident response runbook
- `docs/phase4/INTEGRATION.md` — Phase 2 / Phase 3 contract
