"""Alert data model — the canonical Phase 4 output.

The `Alert` is a frozen, content-addressable Pydantic v2 model. Two alerts
with the same `(tenant_id, trend_id, decision_window, verdict, priority)`
hash to the same `alert_id` — this is the foundation of idempotency.

`AlertEnvelope` is the over-the-wire wrapper sent to notifiers, carrying
versioning + signing fields.

`AlertOutboxRow` mirrors the Postgres row layout.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis.execute.constants import (
    ALERT_ENVELOPE_VERSION,
    ALLOWED_PRIORITIES,
    ALLOWED_VERDICTS,
)


class AlertSource(StrEnum):
    """Which upstream phase(s) produced this alert."""

    PHASE2_ONLY = "phase2_only"
    PHASE3_ONLY = "phase3_only"
    PHASE2_AND_PHASE3 = "phase2_and_phase3"


class AlertStatus(StrEnum):
    """Lifecycle state of an alert."""

    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    FAILED = "failed"
    ACKED = "acked"
    SUPPRESSED = "suppressed"


class DeliveryStatus(StrEnum):
    """Per-channel delivery outcome."""

    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


def _utc_now() -> datetime:
    """Return timezone-aware UTC now. Centralised for testability."""
    return datetime.now(UTC)


class Alert(BaseModel):
    """Canonical alert object.

    Frozen Pydantic v2 model. Immutable after construction. The `alert_id`
    is content-addressable (SHA-256) and computed by the caller (see
    `aegis.execute.utils.hashing.compute_alert_id`).

    Attributes are flat (no nested mutable types) to keep serialisation
    cheap and the model hashable.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=False,  # frozen already prevents assignment
    )

    # ---- Identity ----
    alert_id: str = Field(min_length=16, max_length=128)
    tenant_id: UUID
    trend_id: str = Field(min_length=1, max_length=256)
    decision_window: str = Field(
        default="default",
        max_length=64,
        description=(
            "Logical grouping key used to dedupe rapid re-emission "
            "(e.g. an ISO hour bucket or a Phase 2 cycle id)."
        ),
    )

    # ---- Verdict + classification ----
    verdict: str = Field(min_length=1, max_length=32)
    priority: int = Field(ge=0, le=3)
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)

    # ---- Source attribution ----
    source: AlertSource

    # ---- Optional Phase 3 numeric attachments ----
    p_breakout_24h: float | None = Field(default=None, ge=0.0, le=1.0)
    p_decline_6h: float | None = Field(default=None, ge=0.0, le=1.0)
    p_saturation: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_margin_usd: float | None = None
    loss_probability: float | None = Field(default=None, ge=0.0, le=1.0)

    # ---- Optional sizing (advisory) ----
    advised_units: int | None = Field(default=None, ge=0)
    advised_capital_usd: float | None = Field(default=None, ge=0.0)

    # ---- Halt / block info ----
    halt_reason: str | None = Field(default=None, max_length=128)
    blocked_by: tuple[str, ...] = Field(default_factory=tuple)

    # ---- Narrative (LLM-augmented; never affects verdict) ----
    title: str = Field(min_length=1, max_length=200)
    summary_text: str = Field(max_length=2000, default="")

    # ---- Bookkeeping ----
    created_at: datetime = Field(default_factory=_utc_now)
    correlation_id: str | None = Field(default=None, max_length=128)

    @field_validator("verdict")
    @classmethod
    def _verdict_must_be_allowed(cls, v: str) -> str:
        if v not in ALLOWED_VERDICTS:
            raise ValueError(
                f"verdict must be one of {sorted(ALLOWED_VERDICTS)}, got {v!r}"
            )
        return v

    @field_validator("priority")
    @classmethod
    def _priority_must_be_allowed(cls, v: int) -> int:
        if v not in ALLOWED_PRIORITIES:
            raise ValueError(
                f"priority must be one of {sorted(ALLOWED_PRIORITIES)}, got {v}"
            )
        return v

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _check_block_consistency(self) -> Self:
        if self.verdict == "BLOCK" and not self.halt_reason and not self.blocked_by:
            raise ValueError("BLOCK verdict requires halt_reason or blocked_by")
        return self


class AlertEnvelope(BaseModel):
    """Wire format for notifications.

    The envelope is what notifiers receive. It wraps the canonical `Alert`
    and adds versioning + an optional HMAC signature so downstream consumers
    can verify integrity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    envelope_version: str = Field(default=ALERT_ENVELOPE_VERSION)
    alert: Alert
    signature_hex: str | None = None  # HMAC-SHA256 hex; set by signer

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict for HTTP bodies."""
        return self.model_dump(mode="json")


class DeliveryAttempt(BaseModel):
    """One delivery attempt to one channel.

    Immutable row in `alert_deliveries`. Multiple attempts may exist per
    (alert, channel) — the latest is authoritative.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_id: str
    channel: str = Field(max_length=64)
    attempt_no: int = Field(ge=1)
    status: DeliveryStatus
    http_status: int | None = Field(default=None, ge=0, le=600)
    latency_ms: float = Field(ge=0.0)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime = Field(default_factory=_utc_now)


class AlertOutboxRow(BaseModel):
    """Mirror of a row in `alert_outbox`.

    Includes mutable state (`status`, `attempts`, `next_retry_at`). The
    `alert` payload itself is frozen.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    alert: Alert
    status: AlertStatus
    attempts: int = Field(ge=0)
    next_retry_at: datetime | None = None
    last_error_code: str | None = None
    enqueued_at: datetime


_ALL: Final = [
    "Alert",
    "AlertEnvelope",
    "AlertOutboxRow",
    "AlertSource",
    "AlertStatus",
    "DeliveryAttempt",
    "DeliveryStatus",
]
__all__ = list(_ALL)
