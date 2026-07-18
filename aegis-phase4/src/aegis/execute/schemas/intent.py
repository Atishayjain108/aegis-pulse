"""Execution intent — the read-only handoff to a future Phase 6 executor.

Phase 4 NEVER calls a storefront, payment, or supplier API. When the policy
chain emits an ENTER or EXIT verdict that has cleared all risk gates, an
`ExecutionIntent` is persisted to the `execution_intents` table. A future
phase (or a human) consumes that table.

This separation is intentional: Phase 4 is the *advisor*, Phase 6 is the
*actor*. The contract between them is this immutable record.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IntentKind(StrEnum):
    """Kind of intent."""

    ENTER_POSITION = "enter_position"
    EXIT_POSITION = "exit_position"
    HOLD_POSITION = "hold_position"


class IntentStatus(StrEnum):
    """Lifecycle of an intent."""

    PROPOSED = "proposed"  # Phase 4 wrote it
    APPROVED = "approved"  # operator approved (manual)
    REJECTED = "rejected"  # operator rejected
    EXECUTED = "executed"  # Phase 6 acted (future)
    EXPIRED = "expired"  # TTL elapsed


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ExecutionIntent(BaseModel):
    """Advisory intent record.

    Always persisted alongside an Alert; never used as a side-channel.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_id: str = Field(min_length=16, max_length=128)
    alert_id: str = Field(min_length=16, max_length=128)
    tenant_id: UUID
    trend_id: str = Field(min_length=1, max_length=256)
    kind: IntentKind
    status: IntentStatus = IntentStatus.PROPOSED
    advised_units: int = Field(ge=0)
    advised_capital_usd: float = Field(ge=0.0)
    expected_margin_usd: float | None = None
    loss_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    horizon_hours: int = Field(default=24, gt=0)
    rationale: str = Field(max_length=2000, default="")
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime | None = None

    @field_validator("created_at", "expires_at")
    @classmethod
    def _must_be_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("datetime fields must be timezone-aware")
        return v.astimezone(UTC)


__all__: Final = ["ExecutionIntent", "IntentKind", "IntentStatus"]
