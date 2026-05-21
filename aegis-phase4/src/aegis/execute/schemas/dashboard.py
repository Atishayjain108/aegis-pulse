"""Dashboard + SSE event schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from aegis.execute.schemas.alert import Alert


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MetricCard(BaseModel):
    """A single metric tile for the dashboard."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(max_length=64)
    value: str = Field(max_length=128)
    hint: str = Field(default="", max_length=200)


class DashboardSnapshot(BaseModel):
    """Read-only point-in-time snapshot served at `/snapshot`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    killswitch_state: str
    pending_outbox: int = Field(ge=0)
    recent_alerts: list[Alert]
    cards: list[MetricCard]
    generated_at: datetime = Field(default_factory=_utc_now)


class SSEEvent(BaseModel):
    """Server-sent event payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: str = Field(max_length=64)
    data: dict[str, Any]
    occurred_at: datetime = Field(default_factory=_utc_now)


__all__: Final = ["DashboardSnapshot", "MetricCard", "SSEEvent"]
