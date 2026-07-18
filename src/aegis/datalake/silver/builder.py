"""Silver layer — cleaning, conformance, PII handling, deterministic typing.

Reads partitions from Bronze, applies pure-Python transforms, and writes
the cleaned shape to Silver. Every transform is deterministic and
reproducible: given the same Bronze input, Silver is bit-for-bit identical.

Following Phase 3 doctrine: **no LLM in the data layer**. All decisions are
rule-based.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aegis.datalake import constants as C
from aegis.datalake._logging import get_logger
from aegis.datalake.catalog import LakeCatalog, LineageEdge, PartitionInfo
from aegis.datalake.quality import QualityGate
from aegis.datalake.schemas import (
    IngestBatch,
    SilverPrediction,
    SilverSignal,
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
# Platform-tier registry (matches CLAUDE.md tier scheme)
# ---------------------------------------------------------------------------
_PLATFORM_TIER: dict[str, str] = {
    # Tier 1 — Intent
    "tiktok": "TIER_1_INTENT",
    "youtube": "TIER_1_INTENT",
    "instagram": "TIER_1_INTENT",
    "pinterest": "TIER_1_INTENT",
    "reddit": "TIER_1_INTENT",
    "reddit_finance": "TIER_1_INTENT",
    "reddit_ecommerce": "TIER_1_INTENT",
    # Tier 2 — Commerce
    "amazon": "TIER_2_COMMERCE",
    "amazon_in": "TIER_2_COMMERCE",
    "flipkart": "TIER_2_COMMERCE",
    "meesho": "TIER_2_COMMERCE",
    "myntra": "TIER_2_COMMERCE",
    "ajio": "TIER_2_COMMERCE",
    "nykaa": "TIER_2_COMMERCE",
    "snapdeal": "TIER_2_COMMERCE",
    "indiamart": "TIER_2_COMMERCE",
    "nse_bse": "TIER_2_COMMERCE",
    # Tier 3 — Search / News
    "hacker_news": "TIER_3_SEARCH",
    "github_trending": "TIER_3_SEARCH",
    "github_public": "TIER_3_SEARCH",
    "google_news": "TIER_3_SEARCH",
    "bing_news": "TIER_3_SEARCH",
    "google_trends": "TIER_3_SEARCH",
    "google_trends_india": "TIER_3_SEARCH",
    "techcrunch": "TIER_3_SEARCH",
    "wired": "TIER_3_SEARCH",
    "bbc_news": "TIER_3_SEARCH",
    "reuters": "TIER_3_SEARCH",
    "ndtv_profit": "TIER_3_SEARCH",
    "mint": "TIER_3_SEARCH",
    "business_standard": "TIER_3_SEARCH",
    "yahoo_finance": "TIER_3_SEARCH",
    "investing_com": "TIER_3_SEARCH",
    "moneycontrol": "TIER_3_SEARCH",
    "economic_times": "TIER_3_SEARCH",
    "screener_in": "TIER_3_SEARCH",
    "medium": "TIER_3_SEARCH",
    "devto": "TIER_3_SEARCH",
    "npm_trends": "TIER_3_SEARCH",
    "producthunt": "TIER_3_SEARCH",
}

# Threshold above which a signal is marked "high engagement"
_HIGH_ENGAGEMENT_THRESHOLD = 1_000


@dataclass(frozen=True, slots=True)
class SilverBuildStats:
    """Result of a Silver build."""

    bronze_rows_read: int
    silver_rows_written: int
    rows_rejected: int
    partitions_written: int


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
class SilverBuilder:
    """Promote Bronze partitions to Silver with cleaning + conformance."""

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
    def build_signals_for_date(
        self,
        *,
        date_iso: str,
        tenant_id: str | None = None,
    ) -> SilverBuildStats:
        """Read a Bronze signals partition for ``date_iso`` and write Silver."""
        tenant = tenant_id or self._tenant_id
        partition_key = f"{C.TIME_PARTITION_KEY}={date_iso}"

        bronze_partitions = self._catalog.list_partitions(
            table_name="signals",
            layer=C.BRONZE,
            tenant_id=tenant,
            partition_key=partition_key,
        )
        if not bronze_partitions:
            _log.info("silver.no_bronze", date=date_iso, table="signals")
            return SilverBuildStats(0, 0, 0, 0)

        raw_rows: list[dict[str, Any]] = []
        for p in bronze_partitions:
            data = self._backend.get_bytes(p.file_path)
            raw_rows.extend(parquet_bytes_to_rows(data))

        cleaned = [_clean_signal_row(r) for r in raw_rows]

        gate = QualityGate(schema=SilverSignal)
        report = gate.validate(cleaned)

        if not report.passed:
            # Quarantine instead of crash — write to _rejected for later analysis
            self._write_rejects(
                table="signals",
                partition_key=partition_key,
                tenant=tenant,
                rejects=[r.row for r in report.rejected],
            )

        manifest = self._write_silver(
            table="signals",
            partition_key=partition_key,
            tenant=tenant,
            rows=list(report.accepted_rows),
            source=f"bronze.signals/{date_iso}",
        )
        self._record_lineage(
            upstream_table="signals",
            upstream_layer=C.BRONZE,
            downstream_table="signals",
            downstream_layer=C.SILVER,
            transform="clean_signal_row_v1",
        )
        return SilverBuildStats(
            bronze_rows_read=len(raw_rows),
            silver_rows_written=manifest.row_count,
            rows_rejected=report.reject_count,
            partitions_written=1,
        )

    def build_predictions_for_date(
        self,
        *,
        date_iso: str,
        tenant_id: str | None = None,
    ) -> SilverBuildStats:
        """Read a Bronze predictions partition and write Silver."""
        tenant = tenant_id or self._tenant_id
        partition_key = f"{C.TIME_PARTITION_KEY}={date_iso}"

        bronze_partitions = self._catalog.list_partitions(
            table_name="predictions",
            layer=C.BRONZE,
            tenant_id=tenant,
            partition_key=partition_key,
        )
        if not bronze_partitions:
            return SilverBuildStats(0, 0, 0, 0)

        raw_rows: list[dict[str, Any]] = []
        for p in bronze_partitions:
            raw_rows.extend(parquet_bytes_to_rows(self._backend.get_bytes(p.file_path)))

        cleaned = [_clean_prediction_row(r) for r in raw_rows]
        gate = QualityGate(schema=SilverPrediction)
        report = gate.validate(cleaned)
        if not report.passed:
            self._write_rejects(
                table="predictions",
                partition_key=partition_key,
                tenant=tenant,
                rejects=[r.row for r in report.rejected],
            )
        manifest = self._write_silver(
            table="predictions",
            partition_key=partition_key,
            tenant=tenant,
            rows=list(report.accepted_rows),
            source=f"bronze.predictions/{date_iso}",
        )
        self._record_lineage(
            upstream_table="predictions",
            upstream_layer=C.BRONZE,
            downstream_table="predictions",
            downstream_layer=C.SILVER,
            transform="clean_prediction_row_v1",
        )
        return SilverBuildStats(
            bronze_rows_read=len(raw_rows),
            silver_rows_written=manifest.row_count,
            rows_rejected=report.reject_count,
            partitions_written=1,
        )

    # ---- Internals ------------------------------------------------------
    def _write_silver(
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
            layer=C.SILVER,  # type: ignore[arg-type]
            tenant_id=tenant,
            partition_key=partition_key,
            source=source,
            rows=tuple(rows),
        )
        # Auto-register on first write — idempotent thanks to if_exists=skip.
        self._catalog.register_table(
            name=table,
            layer=C.SILVER,
            schema_json="{}",
            description=f"Silver table {table} (auto-registered by SilverBuilder)",
            if_exists="skip",
        )
        manifest = write_batch(backend=self._backend, batch=batch, source=source)

        file_path = plan_file_path(
            layer=C.SILVER,
            table=table,
            partition_key=partition_key,
            tenant_id=tenant,
            batch_id=manifest.batch_id,
        )
        manifest_path = plan_manifest_path(
            layer=C.SILVER,
            table=table,
            partition_key=partition_key,
            tenant_id=tenant,
            batch_id=manifest.batch_id,
        )
        self._catalog.record_partition(
            PartitionInfo(
                table_name=table,
                layer=C.SILVER,
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

    def _write_rejects(
        self,
        *,
        table: str,
        partition_key: str,
        tenant: str,
        rejects: list[Mapping[str, Any]],
    ) -> None:
        if not rejects:
            return
        # Reject partitions go to a `_rejected` table — easily inspectable
        batch = IngestBatch(
            table_name=f"{table}__rejected",
            layer=C.BRONZE,  # type: ignore[arg-type]  -- keep raw look
            tenant_id=tenant,
            partition_key=partition_key,
            source=f"silver.reject.{table}",
            rows=tuple(dict(r) for r in rejects),
        )
        write_batch(backend=self._backend, batch=batch, source=batch.source)
        _log.warning(
            "silver.rejects.persisted",
            table=table,
            count=len(rejects),
            partition=partition_key,
        )

    def _record_lineage(
        self,
        *,
        upstream_table: str,
        upstream_layer: str,
        downstream_table: str,
        downstream_layer: str,
        transform: str,
    ) -> None:
        self._catalog.record_lineage(
            LineageEdge(
                upstream_table=upstream_table,
                upstream_layer=upstream_layer,
                downstream_table=downstream_table,
                downstream_layer=downstream_layer,
                transform=transform,
            )
        )


# ---------------------------------------------------------------------------
# Pure transformer functions (no IO, deterministic)
# ---------------------------------------------------------------------------
def _clean_signal_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Bronze signal → Silver signal (no IO, deterministic)."""
    platform = str(row.get("platform") or "unknown").lower()
    tier = _PLATFORM_TIER.get(platform, "TIER_4_OTHER")
    captured_at = _coerce_dt_utc(row.get("captured_at"))
    captured_date = captured_at.date().isoformat() if captured_at else ""

    views = _safe_int(row.get("views"))
    likes = _safe_int(row.get("likes"))
    comments = _safe_int(row.get("comments"))
    shares = _safe_int(row.get("shares"))
    saves = _safe_int(row.get("saves"))
    engagement_total = views + likes + comments + shares + saves

    author_handle = row.get("author_handle")
    author_hash = _stable_hash(author_handle) if author_handle else None

    return {
        "signal_id": str(row.get("signal_id") or ""),
        "tenant_id": str(row.get("tenant_id") or C.DEFAULT_TENANT_ID),
        "platform": platform,
        "platform_tier": tier,
        "title": _norm_str(row.get("title")),
        "url_normalized": _normalize_url(row.get("url")),
        "author_hash": author_hash,
        "captured_at": captured_at,
        "captured_date": captured_date,
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "saves": saves,
        "engagement_total": engagement_total,
        "is_high_engagement": engagement_total >= _HIGH_ENGAGEMENT_THRESHOLD,
    }


def _clean_prediction_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Bronze prediction → Silver prediction (no IO, deterministic)."""
    finished_at = _coerce_dt_utc(row.get("finished_at"))
    finished_date = finished_at.date().isoformat() if finished_at else ""
    return {
        "prediction_id": str(row.get("prediction_id") or ""),
        "tenant_id": str(row.get("tenant_id") or C.DEFAULT_TENANT_ID),
        "trend_id": str(row.get("trend_id") or ""),
        "horizon_h": _safe_int(row.get("horizon_h"), default=24),
        "p_breakout": _clip01(row.get("p_breakout")),
        "p_decline": _clip01(row.get("p_decline")),
        "confidence": _clip01(row.get("confidence")),
        "model_version": str(row.get("model_version") or "unknown"),
        "finished_at": finished_at,
        "finished_date": finished_date,
    }


# ---------------------------------------------------------------------------
# Tiny pure helpers
# ---------------------------------------------------------------------------
def _safe_int(v: Any, *, default: int = 0) -> int:
    if v is None or (isinstance(v, float) and (v != v)):  # NaN check  # noqa: PLR0124
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _clip01(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (ValueError, TypeError):
        return 0.0
    if f != f:  # NaN  # noqa: PLR0124
        return 0.0
    return max(0.0, min(1.0, f))


def _norm_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _normalize_url(v: Any) -> str | None:
    if not v:
        return None
    try:
        parts = urlsplit(str(v))
    except ValueError:
        return None
    if not parts.scheme:
        return None
    # lowercase scheme + host, drop trailing slash on empty path
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or "/",
            parts.query,
            "",  # drop fragment
        )
    )


def _stable_hash(v: Any) -> str:
    return hashlib.sha256(str(v).encode("utf-8")).hexdigest()[:16]


def _coerce_dt_utc(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(UTC) if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


__all__ = ["SilverBuildStats", "SilverBuilder"]
