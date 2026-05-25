"""Gold layer — business-ready aggregates.

Reads Silver, produces denormalized rollups suitable for direct dashboard
consumption. Every aggregation is deterministic; same Silver in =
same Gold out, bit-for-bit.

Pure Python aggregation — no pandas dependency. Keeps the build path
viable in minimal environments and avoids surprises from pandas defaults.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from aegis.datalake import constants as C
from aegis.datalake._logging import get_logger
from aegis.datalake.catalog import LakeCatalog, LineageEdge, PartitionInfo
from aegis.datalake.schemas import (
    GoldDailyPlatformStats,
    GoldPredictionAccuracy,
    GoldTrendVerdictRollup,
    IngestBatch,
    WriteManifest,
)
from aegis.datalake.storage import StorageBackend
from aegis.datalake.storage.parquet import (
    parquet_bytes_to_rows,
    plan_file_path,
    plan_manifest_path,
    write_batch,
)

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Stats dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GoldBuildStats:
    silver_rows_read: int
    gold_rows_written: int
    aggregations_built: int


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
class GoldAggregator:
    """Build Gold aggregations from Silver."""

    def __init__(
        self,
        *,
        backend: StorageBackend,
        catalog: LakeCatalog,
        tenant_id: str = C.DEFAULT_TENANT_ID,
    ) -> None:
        self._backend = backend
        self._catalog = catalog
        self._tenant_id = tenant_id

    # ---- Public API -----------------------------------------------------
    def build_daily_platform_stats(
        self,
        *,
        date_iso: str,
        tenant_id: str | None = None,
    ) -> GoldBuildStats:
        """Build the headline ``daily_platform_stats`` Gold table for one day."""
        tenant = tenant_id or self._tenant_id
        partition_key = f"{C.TIME_PARTITION_KEY}={date_iso}"

        silver_rows = self._load_silver(
            table="signals", partition_key=partition_key, tenant=tenant
        )
        if not silver_rows:
            return GoldBuildStats(0, 0, 0)

        gold_rows = _aggregate_daily_platform_stats(
            silver_rows, tenant_id=tenant, dt=date_iso
        )

        manifest = self._write_gold(
            table="daily_platform_stats",
            partition_key=partition_key,
            tenant=tenant,
            rows=gold_rows,
            source=f"silver.signals/{date_iso}",
        )
        self._catalog.record_lineage(
            LineageEdge(
                upstream_table="signals",
                upstream_layer=C.SILVER,
                downstream_table="daily_platform_stats",
                downstream_layer=C.GOLD,
                transform="aggregate_daily_platform_stats_v1",
            )
        )
        return GoldBuildStats(
            silver_rows_read=len(silver_rows),
            gold_rows_written=manifest.row_count,
            aggregations_built=1,
        )

    def build_trend_verdict_rollup(
        self,
        *,
        date_iso: str,
        tenant_id: str | None = None,
    ) -> GoldBuildStats:
        """Aggregate Phase 2 agent results into a verdict rollup."""
        tenant = tenant_id or self._tenant_id
        partition_key = f"{C.TIME_PARTITION_KEY}={date_iso}"

        silver_rows = self._load_silver(
            table="agent_results", partition_key=partition_key, tenant=tenant
        )
        # agent_results live in Bronze (we don't have a Silver builder
        # for them yet — they're already clean from the stream). Fall back.
        if not silver_rows:
            silver_rows = self._load_bronze(
                table="agent_results", partition_key=partition_key, tenant=tenant
            )
        if not silver_rows:
            return GoldBuildStats(0, 0, 0)

        gold_rows = _aggregate_trend_verdicts(silver_rows, tenant_id=tenant, dt=date_iso)
        manifest = self._write_gold(
            table="trend_verdict_rollup",
            partition_key=partition_key,
            tenant=tenant,
            rows=gold_rows,
            source=f"silver_or_bronze.agent_results/{date_iso}",
        )
        return GoldBuildStats(
            silver_rows_read=len(silver_rows),
            gold_rows_written=manifest.row_count,
            aggregations_built=1,
        )

    def build_prediction_accuracy(
        self,
        *,
        date_iso: str,
        tenant_id: str | None = None,
    ) -> GoldBuildStats:
        """Aggregate Phase 3 predictions into accuracy summaries (rollup only)."""
        tenant = tenant_id or self._tenant_id
        partition_key = f"{C.TIME_PARTITION_KEY}={date_iso}"

        silver_rows = self._load_silver(
            table="predictions", partition_key=partition_key, tenant=tenant
        )
        if not silver_rows:
            return GoldBuildStats(0, 0, 0)

        gold_rows = _aggregate_prediction_accuracy(
            silver_rows, tenant_id=tenant, dt=date_iso
        )
        manifest = self._write_gold(
            table="prediction_accuracy",
            partition_key=partition_key,
            tenant=tenant,
            rows=gold_rows,
            source=f"silver.predictions/{date_iso}",
        )
        return GoldBuildStats(
            silver_rows_read=len(silver_rows),
            gold_rows_written=manifest.row_count,
            aggregations_built=1,
        )

    # ---- Internals ------------------------------------------------------
    def _load_silver(
        self, *, table: str, partition_key: str, tenant: str
    ) -> list[dict[str, Any]]:
        return self._load_layer(
            layer=C.SILVER, table=table, partition_key=partition_key, tenant=tenant
        )

    def _load_bronze(
        self, *, table: str, partition_key: str, tenant: str
    ) -> list[dict[str, Any]]:
        return self._load_layer(
            layer=C.BRONZE, table=table, partition_key=partition_key, tenant=tenant
        )

    def _load_layer(
        self, *, layer: str, table: str, partition_key: str, tenant: str
    ) -> list[dict[str, Any]]:
        partitions = self._catalog.list_partitions(
            table_name=table,
            layer=layer,
            tenant_id=tenant,
            partition_key=partition_key,
        )
        rows: list[dict[str, Any]] = []
        for p in partitions:
            data = self._backend.get_bytes(p.file_path)
            rows.extend(parquet_bytes_to_rows(data))
        return rows

    def _write_gold(
        self,
        *,
        table: str,
        partition_key: str,
        tenant: str,
        rows: list[dict[str, Any]],
        source: str,
    ) -> WriteManifest:
        batch = IngestBatch(
            table_name=table,
            layer=C.GOLD,  # type: ignore[arg-type]
            tenant_id=tenant,
            partition_key=partition_key,
            source=source,
            rows=tuple(rows),
        )
        # Auto-register on first write — idempotent thanks to if_exists=skip.
        self._catalog.register_table(
            name=table,
            layer=C.GOLD,
            schema_json="{}",
            description=f"Gold table {table} (auto-registered by GoldAggregator)",
            if_exists="skip",
        )
        manifest = write_batch(backend=self._backend, batch=batch, source=source)

        file_path = plan_file_path(
            layer=C.GOLD,
            table=table,
            partition_key=partition_key,
            tenant_id=tenant,
            batch_id=manifest.batch_id,
        )
        manifest_path = plan_manifest_path(
            layer=C.GOLD,
            table=table,
            partition_key=partition_key,
            tenant_id=tenant,
            batch_id=manifest.batch_id,
        )
        self._catalog.record_partition(
            PartitionInfo(
                table_name=table,
                layer=C.GOLD,
                partition_key=partition_key,
                tenant_id=tenant,
                batch_id=manifest.batch_id,
                file_path=file_path,
                manifest_path=manifest_path,
                row_count=manifest.row_count,
                byte_size=manifest.byte_size,
                sha256=manifest.sha256,
                written_at=manifest.written_at,
            )
        )
        return manifest


# ---------------------------------------------------------------------------
# Pure aggregations (no IO)
# ---------------------------------------------------------------------------
def _aggregate_daily_platform_stats(
    rows: Iterable[Mapping[str, Any]], *, tenant_id: str, dt: str
) -> list[dict[str, Any]]:
    """Group by platform, compute counts + engagement aggregates."""
    by_platform: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "signal_count": 0,
            "authors": set(),
            "total_engagement": 0,
            "high_engagement_count": 0,
        }
    )
    for r in rows:
        platform = str(r.get("platform") or "unknown")
        bucket = by_platform[platform]
        bucket["signal_count"] += 1
        author = r.get("author_hash")
        if author:
            bucket["authors"].add(author)
        engagement = int(r.get("engagement_total") or 0)
        bucket["total_engagement"] += engagement
        if r.get("is_high_engagement"):
            bucket["high_engagement_count"] += 1

    out: list[dict[str, Any]] = []
    for platform, b in sorted(by_platform.items()):
        signal_count = b["signal_count"]
        # Validate via schema for guaranteed shape, then unwrap
        record = GoldDailyPlatformStats(
            tenant_id=tenant_id,
            platform=platform,
            dt=dt,
            signal_count=signal_count,
            unique_authors=len(b["authors"]),
            total_engagement=b["total_engagement"],
            avg_engagement=(b["total_engagement"] / signal_count) if signal_count else 0.0,
            high_engagement_rate=(b["high_engagement_count"] / signal_count) if signal_count else 0.0,
        )
        out.append(record.model_dump(mode="json"))
    return out


def _aggregate_trend_verdicts(
    rows: Iterable[Mapping[str, Any]], *, tenant_id: str, dt: str
) -> list[dict[str, Any]]:
    """Group Phase 2 results by verdict and compute summary metrics."""
    by_verdict: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "score_sum": 0.0,
            "confidence_sum": 0.0,
            "duration_sum": 0.0,
            "duration_count": 0,
        }
    )
    valid = {"ENTER", "HOLD", "BLOCK"}
    for r in rows:
        verdict = str(r.get("final_verdict") or "HOLD").upper()
        if verdict not in valid:
            continue
        bucket = by_verdict[verdict]
        bucket["count"] += 1
        bucket["score_sum"] += float(r.get("final_score") or 0.0)
        bucket["confidence_sum"] += float(r.get("final_confidence") or 0.0)
        if r.get("duration_ms") is not None:
            bucket["duration_sum"] += float(r["duration_ms"])
            bucket["duration_count"] += 1

    out: list[dict[str, Any]] = []
    for verdict, b in sorted(by_verdict.items()):
        count = b["count"]
        avg_duration = (
            b["duration_sum"] / b["duration_count"] if b["duration_count"] else 0.0
        )
        record = GoldTrendVerdictRollup(
            tenant_id=tenant_id,
            dt=dt,
            verdict=verdict,  # type: ignore[arg-type]
            count=count,
            avg_score=(b["score_sum"] / count) if count else 0.0,
            avg_confidence=(b["confidence_sum"] / count) if count else 0.0,
            avg_duration_ms=avg_duration,
        )
        out.append(record.model_dump(mode="json"))
    return out


def _aggregate_prediction_accuracy(
    rows: Iterable[Mapping[str, Any]], *, tenant_id: str, dt: str
) -> list[dict[str, Any]]:
    """Group predictions by (model_version, horizon)."""
    by_key: dict[tuple[str, int], dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "p_breakout_sum": 0.0, "confidence_sum": 0.0}
    )
    for r in rows:
        model = str(r.get("model_version") or "unknown")
        horizon = int(r.get("horizon_h") or 0)
        bucket = by_key[(model, horizon)]
        bucket["count"] += 1
        bucket["p_breakout_sum"] += float(r.get("p_breakout") or 0.0)
        bucket["confidence_sum"] += float(r.get("confidence") or 0.0)

    out: list[dict[str, Any]] = []
    for (model, horizon), b in sorted(by_key.items()):
        count = b["count"]
        if count == 0:
            continue
        record = GoldPredictionAccuracy(
            tenant_id=tenant_id,
            dt=dt,
            model_version=model,
            horizon_h=horizon,
            prediction_count=count,
            avg_p_breakout=max(0.0, min(1.0, b["p_breakout_sum"] / count)),
            avg_confidence=max(0.0, min(1.0, b["confidence_sum"] / count)),
        )
        out.append(record.model_dump(mode="json"))
    return out


__all__ = [
    "GoldAggregator",
    "GoldBuildStats",
]
