# Phase 3 Quickstart

Get from a fresh checkout to a running prediction in under five minutes.

## Prerequisites

* Python **3.12.x** (Phase 0 bootstrap installs 3.12.7 via pyenv).
* No GPU required for the heuristic floor.
* No API keys required.

## 1. Install (heuristic-only path)

```bash
cd ~/code/aegis-pulse
pip install -e .
```

This pulls only `pydantic`, `structlog`, and `anyio` (~3 MB).

## 2. Run the smoke test

```bash
python -m pytest tests/ -q
# 159 passed in ~3s
```

Every test runs with no Docker, no DB, no network. If this passes,
your install is good.

## 3. Run a synthetic inference

```bash
python - <<'PY'
import asyncio
from datetime import datetime, timedelta, timezone
from aegis.predict.inference import InferenceRunner

async def main():
    runner = InferenceRunner()
    base = datetime.now(timezone.utc) - timedelta(hours=72)
    signals = [
        {
            "id": f"sig-{i}", "platform": "twitter",
            "captured_at": base + timedelta(hours=i),
            "title": None, "body": f"msg {i}", "url": None,
            "content_hash": f"h{i}", "author_id": f"a{i % 5}",
            "views": (i+1)*25, "likes": (i+1)*3, "comments": (i+1)//2,
            "shares": 0, "saves": 0,
            "sentiment": 0.0, "commercial_intent": 0.01*i,
            "novelty": 1.0 if i < 5 else 0.2,
        }
        for i in range(72)
    ]
    result = await runner.run(
        tenant_id="default", trend_id="trend-quickstart",
        signals=signals,
    )
    primary = result.bundle.by_horizon(24)
    print(f"horizon={primary.horizon_hours}h")
    print(f"stage={primary.stage.value}")
    print(f"action={primary.action.value}")
    print(f"p_breakout={primary.p_breakout:.3f}")
    print(f"confidence={primary.confidence:.3f}")
    print(f"duration_ms={result.duration_ms:.2f}")

asyncio.run(main())
PY
```

Expected: a prediction in ~10–15 ms.

## 4. Run the HTTP service

```bash
pip install -e ".[serving]"
uvicorn aegis.predict.serving.app:create_app --factory --port 8000
# Visit http://localhost:8000/docs for the OpenAPI UI.
```

Then in another terminal:

```bash
curl -s http://localhost:8000/healthz
# {"status":"ok","ts":"2026-..."}
```

## 5. Bench latency

```bash
python - <<'PY'
import asyncio
from aegis.predict.cli.commands import _bench
asyncio.run(_bench(50, 72))
PY
# iters=50  p50=~9ms  p90=~10ms  p99=~12ms
```

## 6. Plug into Phase 2

```python
from aegis.agents_phase3_glue.bridge import enrich_scout_decision

state = await enrich_scout_decision(state)
# state["phase3_decision"]["verdict"] in {"advance","hold","exit","block"}
# state["phase3_result"] carries the full InferenceResult
```

## 7. Optional: enable neural augmentation

```bash
pip install -e ".[ml,training,graph,causal,rl]"
```

PatchTST / TimesNet / HGT / DoWhy / RLlib engage automatically when
their dependencies are present. The factory falls back to the
heuristic if any of them fail to load.

## 8. Apply DB migration

```bash
psql "$AEGIS_PG_DSN" -f db/migrations/0002_predictions.sql
```

This adds:

* `predictions` (TimescaleDB hypertable, RLS-enabled)
* `prediction_audit`
* `model_manifest`
* `backtest_results`
* `predictions_daily_summary` continuous aggregate

## 9. Run the Docker image

```bash
docker build -f docker/Dockerfile.predict -t aegis-predict:3.0.0 .
docker run --rm -p 8000:8000 aegis-predict:3.0.0
```

For ML-augmented image:

```bash
docker build -f docker/Dockerfile.predict \
    --build-arg INSTALL_OPTIONAL_ML=1 \
    -t aegis-predict:3.0.0-ml .
```

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `AEGIS-PREDICT-3002 sha256 mismatch` | artifact bytes don't match manifest | re-register with correct sha or use `_PLACEHOLDER_SHA` |
| `AEGIS-PREDICT-3001 ModelNotFoundError` | manifest missing for name@version | check `ModelStore.list_versions(name)` |
| `latency_budget_exceeded` halt_reason | inference > `INFERENCE_LATENCY_BUDGET_MS` | check the audit; raise budget if intentional |
| `predictor_timeout` halt_reason | individual predictor > `INFERENCE_HARD_TIMEOUT_S` | usually a torch model on CPU; tune timeout |
| `signature String should have at least 1 character` | tried to persist with empty signature | use `"UNSIGNED"` placeholder; Phase 20 fills real Ed25519 |
