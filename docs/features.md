# Phase 3 FeatureWindow — Feature Reference (schema 3.1.0)

Every `FeatureWindow` is a row-major flat vector of `window_size × FEATURE_DIM`
floats (default window: 168 hourly buckets = 7 days). `FEATURE_DIM = 24` as of
schema **3.1.0** (PASS2-2E); schema 3.0.0 windows (20 features) are still
accepted on inbound paths and padded with zeros via
`aegis.predict.features.builder.pad_legacy_window()`.

The order below **is the schema** — never reorder without bumping
`FEATURE_SCHEMA_VERSION` in `src/aegis/predict/__init__.py`.

## Features 0–19 (schema 3.0.0, unchanged)

| # | Name | Range | Description |
|---|------|-------|-------------|
| 0 | `signal_count` | ≥ 0 | Signals captured in the hourly bucket |
| 1 | `unique_authors` | ≥ 0 | Distinct `author_id`s in the bucket |
| 2 | `platform_diversity` | 0–1 | Normalized Shannon entropy across platforms |
| 3 | `velocity_1h` | ℝ | log1p arrival-rate delta, 1-hour basis |
| 4 | `velocity_6h` | ℝ | log1p arrival-rate delta, 6-hour basis |
| 5 | `velocity_24h` | ℝ | log1p arrival-rate delta, 24-hour basis |
| 6 | `sentiment_mean` | −1–1 | Mean signal sentiment in the bucket |
| 7 | `sentiment_std` | ≥ 0 | Sample std-dev of sentiment |
| 8 | `commercial_intent` | 0–1 | Mean commercial-intent score |
| 9 | `novelty` | 0–1 | Mean novelty score |
| 10 | `coordination_risk` | 0–1 | Author-clustering proxy (signals/author ratio) |
| 11 | `engagement_total` | ≥ 0 | log1p(views+likes+comments+shares+saves) |
| 12 | `engagement_per_author` | ≥ 0 | Engagement / distinct authors |
| 13 | `comment_density` | ≥ 0 | Comments / signal count |
| 14 | `share_density` | ≥ 0 | Shares / signal count |
| 15 | `save_density` | ≥ 0 | Saves / signal count |
| 16 | `hour_of_day_sin` | −1–1 | Cyclic UTC hour encoding |
| 17 | `hour_of_day_cos` | −1–1 | Cyclic UTC hour encoding |
| 18 | `day_of_week_sin` | −1–1 | Cyclic UTC weekday encoding |
| 19 | `day_of_week_cos` | −1–1 | Cyclic UTC weekday encoding |

## Features 20–23 (new in schema 3.1.0 — PASS2-2E)

All four fall back to **0.0** on insufficient data; they never produce NaN and
never raise.

| # | Name | Range | Description |
|---|------|-------|-------------|
| 20 | `cross_platform_coherence` | 0–1 | Mean token-Jaccard between titles posted on *different* platforms within the bucket (capped at 64 pairs). Near 1.0 when the same story echoes verbatim across 2+ platforms — a strong organic-breakout signal. 0.0 with < 2 platforms. |
| 21 | `temporal_autocorr_lag1` | −1–1 | Lag-1 Pearson autocorrelation of hourly arrival counts over the trailing ≤ 24 buckets. Positive = momentum (bursts cluster); negative = oscillation. 0.0 with < 3 points or zero variance. |
| 22 | `author_diversity_ratio` | 0–1 | `(authors − 1) / (signals − 1)` within the bucket. 0.0 = a single voice (astroturf-shaped); 1.0 = every signal from a different author. 0.0 with ≤ 1 signal. |
| 23 | `geo_spread_entropy` | 0–1 | Normalized Shannon entropy of the bucket's region distribution. Regions resolve from an explicit `region`/`geo_region`/`country` row key, falling back to a coarse platform→region map (`_PLATFORM_REGION` in `features/builder.py`). 1.0 = perfectly even spread across 2+ regions; 0.0 = single region or unresolvable. |

## Migration notes

- `LEGACY_FEATURE_NAMES_V3` (the 20-name tuple) is exported from
  `aegis.predict`; the `FeatureWindow.feature_names` validator accepts either
  tuple, so persisted 3.0.0 windows still deserialize.
- `InferenceRunner.run()` pads any 20-dim window before models consume it —
  the first 20 indices are byte-identical, new features read 0.0 (their
  insufficient-data value), so legacy predictions are unchanged.
- `EVOLVE_FEATURE_DIM` (Phase 9) tracks `FEATURE_DIM` and was bumped to 24 in
  the same change.
- ONNX/neural artifacts compiled against 3.0.0 must be retrained before
  promotion; the heuristic floor reads features by name and is unaffected.
