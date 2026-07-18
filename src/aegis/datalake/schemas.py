"""Canonical Phase 10 schemas — Pydantic v2, frozen, immutable after construction.

These models are the contract between the data lake and the rest of AEGIS.
They MUST stay backwards compatible — additive changes only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Manifest / lineage records
# ---------------------------------------------------------------------------
class WriteManifest(BaseModel):
    """Persisted alongside every Parquet write.

    The manifest is the source of truth for ``what was written, when, by whom,
    and with what hash`` — it powers reproducibility audits and idempotency.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    table_name: str
    layer: Literal["bronze", "silver", "gold"]
    partition_key: str  # e.g. "dt=2026-05-20"
    tenant_id: str
    row_count: int = Field(ge=0)
    byte_size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    written_at: datetime
    file_paths: tuple[str, ...]
    source: str = Field(description="upstream system (e.g. 'postgres.signals')")
    batch_id: str = Field(description="content-addressable id of the batch")

    @field_validator("written_at")
    @classmethod
    def _must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("written_at must be timezone-aware")
        return v.astimezone(UTC)

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, v: str) -> str:
        if not all(c in "0123456789abcdef" for c in v.lower()):
            raise ValueError("sha256 must be hex")
        return v.lower()

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, text: str) -> WriteManifest:
        return cls.model_validate_json(text)


# ---------------------------------------------------------------------------
# Bronze records — one model per upstream
# ---------------------------------------------------------------------------
class BronzeSignal(BaseModel):
    """Raw signal as ingested from Phase 1 ``signals`` table.

    Schema-on-read tolerant: extra fields land in ``raw_json``.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    signal_id: str
    tenant_id: str
    platform: str
    title: str | None = None
    url: str | None = None
    author_handle: str | None = None
    captured_at: datetime
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    raw_json: dict[str, Any] | None = None


class BronzePrediction(BaseModel):
    """Raw prediction record from Phase 3 ``predictions`` table."""

    model_config = ConfigDict(frozen=True, extra="allow")

    prediction_id: str
    tenant_id: str
    trend_id: str
    horizon_h: int
    p_breakout: float | None = None
    p_decline: float | None = None
    confidence: float | None = None
    model_version: str | None = None
    finished_at: datetime
    raw_json: dict[str, Any] | None = None


class BronzeAgentResult(BaseModel):
    """Raw Phase 2 graph result captured from the Redis stream."""

    model_config = ConfigDict(frozen=True, extra="allow")

    correlation_id: str
    tenant_id: str
    trend_id: str
    final_verdict: str  # ENTER / HOLD / BLOCK (phase4 vocab)
    raw_verdict: str | None = None  # original phase2 vocab
    final_score: float | None = None
    final_confidence: float | None = None
    final_priority: int | None = None
    halt_reason: str | None = None
    data_confidence: float | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int | None = None
    decisions: list[dict[str, Any]] = Field(default_factory=list)


class BronzeAlert(BaseModel):
    """Raw alert record from Phase 4 ``alerts`` table."""

    model_config = ConfigDict(frozen=True, extra="allow")

    alert_id: str
    tenant_id: str
    trend_id: str
    verdict: str
    score: float | None = None
    confidence: float | None = None
    created_at: datetime
    dedup_key: str | None = None
    raw_json: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Silver records — cleaned, conformed, with surrogate ids
# ---------------------------------------------------------------------------
class SilverSignal(BaseModel):
    """Cleaned + conformed signal for analytics.

    Differences from bronze:
      - Engagement nulls replaced by 0.
      - URL normalised (lower scheme, trailing slash removed).
      - ``engagement_total`` computed.
      - ``platform_tier`` mapped from a registry.
      - PII fields (``author_handle``) hashed unless explicitly retained.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: str
    tenant_id: str
    platform: str
    platform_tier: Literal["TIER_1_INTENT", "TIER_2_COMMERCE", "TIER_3_SEARCH", "TIER_4_OTHER"]
    title: str | None
    url_normalized: str | None
    author_hash: str | None
    captured_at: datetime
    captured_date: str  # YYYY-MM-DD partition key
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    engagement_total: int = 0
    is_high_engagement: bool = False


class SilverPrediction(BaseModel):
    """Conformed prediction record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prediction_id: str
    tenant_id: str
    trend_id: str
    horizon_h: int
    p_breakout: float = Field(ge=0.0, le=1.0)
    p_decline: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str
    finished_at: datetime
    finished_date: str


# ---------------------------------------------------------------------------
# Gold records — business aggregates
# ---------------------------------------------------------------------------
class GoldDailyPlatformStats(BaseModel):
    """One row per (tenant, platform, dt). Powers the dashboard headline cards."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    platform: str
    dt: str
    signal_count: int = Field(ge=0)
    unique_authors: int = Field(ge=0)
    total_engagement: int = Field(ge=0)
    avg_engagement: float = Field(ge=0.0)
    high_engagement_rate: float = Field(ge=0.0, le=1.0)


class GoldTrendVerdictRollup(BaseModel):
    """One row per (tenant, dt, verdict). Tracks ENTER/HOLD/BLOCK volume."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    dt: str
    verdict: Literal["ENTER", "HOLD", "BLOCK"]
    count: int = Field(ge=0)
    avg_score: float
    avg_confidence: float
    avg_duration_ms: float = Field(ge=0.0)


class GoldPredictionAccuracy(BaseModel):
    """One row per (tenant, dt, model_version, horizon)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    dt: str
    model_version: str
    horizon_h: int
    prediction_count: int = Field(ge=0)
    avg_p_breakout: float = Field(ge=0.0, le=1.0)
    avg_confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Batch / lineage helpers
# ---------------------------------------------------------------------------
class IngestBatch(BaseModel):
    """A logical group of rows headed for a single Parquet file.

    The ``batch_id`` is content-addressable — re-running the same batch
    produces the same id, which is what gives the lake its idempotency.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    table_name: str
    layer: Literal["bronze", "silver", "gold"]
    tenant_id: str
    partition_key: str
    source: str
    rows: tuple[dict[str, Any], ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def batch_id(self) -> str:
        """Stable sha256 over the canonical-JSON encoding of the batch payload."""
        payload = {
            "table_name": self.table_name,
            "layer": self.layer,
            "tenant_id": self.tenant_id,
            "partition_key": self.partition_key,
            "rows": list(self.rows),
        }
        canonical = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


# Tagged type aliases for self-documenting signatures
TableName = Annotated[str, "Catalog table name (e.g. 'signals')"]
PartitionKey = Annotated[str, "Hive-style key, e.g. 'dt=2026-05-20'"]


__all__ = [
    "BronzeAgentResult",
    "BronzeAlert",
    "BronzePrediction",
    "BronzeSignal",
    "GoldDailyPlatformStats",
    "GoldPredictionAccuracy",
    "GoldTrendVerdictRollup",
    "IngestBatch",
    "PartitionKey",
    "SilverPrediction",
    "SilverSignal",
    "TableName",
    "WriteManifest",
]
