# Phase 10 Schemas Reference

All schemas live in `aegis.datalake.schemas` as Pydantic v2 frozen models.

## Layering rules

| Layer  | Schema policy            | Pydantic `extra` | Why                                            |
|--------|--------------------------|------------------|------------------------------------------------|
| Bronze | schema-on-read tolerant  | `extra="allow"`  | Upstream may add fields; we store everything.  |
| Silver | strict, typed             | `extra="forbid"` | Conformance enforced; rejected rows quarantined. |
| Gold   | strict, narrow             | `extra="forbid"` | Business-ready; downstream consumers depend on stability. |

## Bronze schemas

### `BronzeSignal`
* `signal_id: str` *(required)*
* `tenant_id: str` *(required)*
* `platform: str` *(required)*
* `title: str | None`
* `url: str | None`
* `author_handle: str | None`
* `captured_at: datetime` *(required)*
* `views, likes, comments, shares, saves: int | None`
* `raw_json: dict | None` — stored as JSON string at rest

### `BronzePrediction`
Mirrors the Phase 3 `predictions` row.

* `prediction_id, tenant_id, trend_id: str` *(required)*
* `horizon_h: int` *(required)*
* `finished_at: datetime` *(required)*
* `p_breakout, p_decline, confidence: float | None`
* `model_version: str | None`
* `raw_json: dict | None`

### `BronzeAlert`
Mirrors the Phase 4 `alerts` row.

* `alert_id, tenant_id, trend_id, verdict: str` *(required)*
* `created_at: datetime` *(required)*
* `score, confidence: float | None`
* `dedup_key: str | None`
* `raw_json: dict | None`

### `BronzeAgentResult`
Mirrors a Phase 2 `aegis:phase2:graph_results` Redis stream entry. Stream
field is `"body"` (gotcha).

## Silver schemas

### `SilverSignal`
* `signal_id, tenant_id, platform: str` *(required)*
* `platform_tier: Literal["TIER_1_INTENT", "TIER_2_COMMERCE", "TIER_3_SEARCH", "TIER_4_OTHER"]` *(required)*
* `title, url_normalized, author_hash: str | None` *(required to be present, may be None)*
* `captured_at: datetime, captured_date: str` *(required)*
* `views, likes, comments, shares, saves: int` *(default 0)*
* `engagement_total: int` — computed sum
* `is_high_engagement: bool` — `engagement_total >= 1000`

### `SilverPrediction`
* `prediction_id, tenant_id, trend_id: str` *(required)*
* `horizon_h: int` *(default 24)*
* `p_breakout, p_decline, confidence: float` — clipped to `[0, 1]`
* `model_version: str` *(default `"unknown"`)*
* `finished_at: datetime, finished_date: str` *(required)*

## Gold schemas

### `GoldDailyPlatformStats`
One row per `(dt, tenant_id, platform)`.

* `dt: str, tenant_id: str, platform: str`
* `signal_count: int`
* `unique_authors: int`
* `total_engagement: int`
* `high_engagement_rate: float`

### `GoldTrendVerdictRollup`
One row per `(dt, tenant_id, trend_id)`. Reads from Phase 2 agent results in
Bronze (no Silver builder for agent results yet).

### `GoldPredictionAccuracy`
One row per `(dt, tenant_id, model_version)`. Used by HISTORIAN agent (Phase 2)
for analogue lookups.

## Partition layout

```
{layer}/{table}/dt=YYYY-MM-DD/tenant_id={uuid}/{batch_id}.parquet
```

Where `batch_id` = `sha256(canonical_json(rows))[:64]`.

## Manifest sidecar (`{batch_id}_manifest.json`)

```json
{
  "schema_version": "1.0",
  "table_name": "signals",
  "layer": "bronze",
  "partition_key": "dt=2026-05-20",
  "tenant_id": "...",
  "row_count": 1234,
  "byte_size": 56789,
  "sha256": "<64-hex>",
  "written_at": "2026-05-20T12:00:00+00:00",
  "file_paths": ["bronze/signals/dt=2026-05-20/tenant_id=.../<batch>.parquet"],
  "source": "postgres.signals",
  "batch_id": "<64-hex>"
}
```
