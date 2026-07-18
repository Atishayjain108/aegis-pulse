"""
Audit sidecar.

`PredictionRecord` is a minimal, immutable, schema-locked DB row. But
operators routinely need to answer questions like:

  * "Why was this trend marked DORMANT instead of EMERGING?"
  * "Did the latency budget fire on this prediction?"
  * "What were the top 3 causal contributors?"

Stuffing those answers into `PredictionRecord` would either widen its
schema (breaking the immutability contract) or stuff them into a
freeform JSON blob (which loses static type guarantees).

Instead we keep a parallel `AuditRecord` co-keyed on the bundle's
correlation_id. Audit records are append-only, written to MinIO as
Parquet under `audit/predict/<date>/<correlation_id>.json`, and never
read by the runtime — only by humans and offline analysers.

This file defines the schema. The persistence layer is in Phase 20's
WORM bucket integration, not here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..causal import CausalAttribution


class AuditRecord(BaseModel):
    """Sidecar audit document. Append-only, never mutated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: str
    tenant_id: str
    trend_id: str
    bundle_id: str  # PredictionBundle.model_id
    halt_reasons: list[str] = Field(default_factory=list)
    causal_top: list[dict[str, Any]] = Field(default_factory=list)
    graph_summary: dict[str, float] = Field(default_factory=dict)
    duration_ms: float = 0.0
    is_heuristic_only: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def make_audit(
    *,
    correlation_id: str,
    tenant_id: str,
    trend_id: str,
    bundle_id: str,
    halt_reasons: tuple[str, ...],
    causal: tuple[CausalAttribution, ...],
    graph_summary: dict[str, float],
    duration_ms: float,
    is_heuristic_only: bool,
) -> AuditRecord:
    """Build an audit record. Pure function — does not persist."""
    return AuditRecord(
        correlation_id=correlation_id,
        tenant_id=tenant_id,
        trend_id=trend_id,
        bundle_id=bundle_id,
        halt_reasons=list(halt_reasons),
        causal_top=[
            {
                "feature": a.feature,
                "contribution": a.contribution,
                "method": a.method,
                "ci": a.confidence_interval,
            }
            for a in causal[:5]
        ],
        graph_summary=dict(graph_summary),
        duration_ms=duration_ms,
        is_heuristic_only=is_heuristic_only,
    )
