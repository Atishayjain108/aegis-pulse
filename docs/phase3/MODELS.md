# Model Catalog

The model registry tracks predictors by stable name. Each name has a
heuristic fallback that runs without ML deps. This document maps each
name to its purpose, dependencies, and behaviour under degradation.

## Resolution rule

```python
from aegis.predict.models.factory import load_model
m = load_model("patchtst")  # returns torch model, or heuristic_temporal if torch missing
```

The factory tries the requested name; if its dependencies are not
importable, it falls back to the corresponding heuristic. Every fall
back is logged at WARNING and tagged in the bundle with
`is_heuristic_only=True`.

## Catalog

### `heuristic_temporal`  ★ floor

| | |
|---|---|
| Purpose | Stage classification + velocity quantiles, single trend |
| Inputs | `FeatureWindow` (168 × 20) |
| Outputs | `PredictionBundle` over 4 horizons (1, 6, 24, 72) |
| Dependencies | None — stdlib + pydantic |
| Latency (CPU) | < 2 ms |
| Falls back to | (this is the floor) |

Algorithm: log-velocity differencing + classification thresholds
calibrated against historical breakout rates. See
`models/heuristic.py:HeuristicTemporalPredictor` for the rule table.

### `heuristic_relational`  ★ floor

| | |
|---|---|
| Purpose | Coordination-risk scoring from creator+platform graph |
| Inputs | `FeatureWindow` (graph derived inside) |
| Outputs | `PredictionBundle` over 4 horizons |
| Dependencies | None |
| Latency (CPU) | < 2 ms |
| Falls back to | (this is the floor) |

Algorithm: density × clustering coefficient × tier-mix entropy. High
density with low entropy flags coordinated/astroturf trends.

### `patchtst`  ⚙ optional

| | |
|---|---|
| Purpose | SOTA long-horizon time-series transformer |
| Paper | Nie et al, ICLR 2023 |
| Dependencies | `torch>=2.5` |
| Latency (CPU, INT8 ONNX) | ~50 ms |
| Falls back to | `heuristic_temporal` |

When loaded with weights, multiplies confidence by `[0.5, 1.0]`
based on the disagreement between its prediction and the heuristic
floor.

### `autoformer`  ⚙ optional

| | |
|---|---|
| Purpose | Decomposition-based time-series transformer |
| Paper | Wu et al, NeurIPS 2021 |
| Dependencies | `torch>=2.5` |
| Latency (CPU, INT8 ONNX) | ~80 ms |
| Falls back to | `heuristic_temporal` |

### `timesnet`  ⚙ optional

| | |
|---|---|
| Purpose | FFT-folded 2-D inception for periodic time-series |
| Paper | Wu et al, ICLR 2023 |
| Dependencies | `torch>=2.5` |
| Latency (CPU, INT8 ONNX) | ~120 ms |
| Falls back to | `heuristic_temporal` |

### `hgt`  ⚙ optional

| | |
|---|---|
| Purpose | Heterogeneous graph transformer over creator network |
| Paper | Hu et al, WWW 2020 |
| Dependencies | `torch>=2.5`, `torch_geometric>=2.6` |
| Latency (CPU) | ~200 ms (graph construction dominates) |
| Falls back to | `heuristic_relational` |

### `deep_ensemble`  ⚙ optional

Wrapper that runs ≥2 base predictors and averages their outputs with
NLL-weighted uncertainty. Used to seed a `ConformalCalibrator`.

| Method | Reports |
|---|---|
| `DEEP_ENSEMBLE` | mean ± std across members |
| `CONFORMAL` | conformalized mean ± calibrated quantile |

### `fusion`  ★ floor

The fused model produced by `inference.runner._fuse_bundles()`. Not
loaded from the registry — synthesised at inference time per request.

```
fusion::<temporal_id>::<relational_id>
```

Uses `FusionWeights(temporal=0.6, relational=0.4)` by default.

## Heuristic outputs format

All heuristic models emit:

```python
Prediction(
    horizon_hours=h,
    stage=TrendStage,           # one of {DORMANT, EMERGING, BREAKOUT, PEAK, DECLINING, SATURATED}
    velocity_log=...,            # log-domain velocity at horizon h
    velocity_mean=exp(velocity_log),
    velocity_p10=...,
    velocity_p50=...,
    velocity_p90=...,
    p_breakout=...,              # P(stage in {BREAKOUT, EMERGING})
    p_peak=...,
    p_decline=...,
    confidence=...,              # composite uncertainty score
    action=PredictionAction,     # one of {ENTER, HOLD, OBSERVE, EXIT, AVOID}
    reasoning="...",
    model_kind=ModelKind.HEURISTIC,
)
```

## Action mapping (canonical)

```
p_breakout >= 0.55 AND confidence >= 0.45  → ENTER
p_decline  >= 0.65                          → EXIT
stage == SATURATED OR coordination > 0.7    → AVOID
otherwise                                   → OBSERVE  (or HOLD if already in)
```

Tuned in `constants.py`:

```python
ACTION_ENTER_PROBABILITY_FLOOR = 0.55
ACTION_EXIT_PROBABILITY_CEILING = 0.65
ACTION_CONFIDENCE_FLOOR = 0.45
```

## Adding a new model

1. Implement `Predictor` protocol in `models/<name>.py`. Inherit
   `BasePredictor` for the lazy `predict()` wrapper.
2. Add to `models/factory.py` `_REGISTRY` with the import-time
   try/except fallback.
3. Add a heuristic fallback name (typically `heuristic_temporal` or
   `heuristic_relational`) so the factory degrades.
4. Add a row to this catalog.
5. Add at least one smoke test in `tests/unit/predict/test_models.py`.
6. Bake an ONNX export path into `training/onnx_export.py`.
