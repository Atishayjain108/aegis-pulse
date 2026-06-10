# AEGIS-PREDICT-0001: Inference Timeout

**Code**: `AEGIS-PREDICT-0001`  
**Phase**: Phase 3 — Predictive Apex  
**Severity**: WARNING (heuristic fallback activates automatically)

## What happened

The `InferenceRunner` did not receive a result from the ML model within the hard
timeout (`INFERENCE_HARD_TIMEOUT_S`, default 30s). The heuristic floor verdict was
used instead to maintain the < 500ms SLA.

## When it appears

- Neural model (PatchTST / HGT) loading took longer than expected (cold start).
- The system is under memory pressure and model inference stalled.
- A GPU process hung and didn't release.

## Remediation

1. **If it happens once**: ignore — the heuristic fallback is intentional.
2. **If it happens repeatedly**:
   ```bash
   # Check model loading time
   uv run aegis predict bench --model neural
   
   # Check system resources
   aegis doctor
   
   # Restart predict service
   docker compose restart aegis-predict
   ```
3. **If GPU is stalled**: `nvidia-smi` → look for hung processes → `kill -9 <pid>`.
4. **If model artifact is corrupted**: `uv run aegis predict eval --re-validate`

## Related constants

- `aegis.predict.constants.INFERENCE_HARD_TIMEOUT_S` (default: 30)
- `aegis.predict.constants.PREDICT_BATCH_MAX` (default: 100)

## Monitoring

Prometheus metric: `aegis_predict_timeout_total{model="neural"}`
