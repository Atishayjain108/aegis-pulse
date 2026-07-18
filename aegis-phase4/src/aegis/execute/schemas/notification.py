"""Notification + channel configuration schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from aegis.execute.schemas.alert import DeliveryStatus


class ChannelKind(StrEnum):
    """Supported notification channel kinds."""

    LOG = "log"
    NTFY = "ntfy"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WEBHOOK = "webhook"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ChannelConfig(BaseModel):
    """Per-channel runtime configuration.

    Built once from `ExecuteSettings` at startup. If `enabled=False`, the
    drainer skips this channel without error.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ChannelKind
    enabled: bool
    name: str = Field(min_length=1, max_length=64)
    timeout_s: float = Field(gt=0.0, default=5.0)
    extras: dict[str, str] = Field(default_factory=dict)


class NotificationResult(BaseModel):
    """Result of a single send attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: str
    status: DeliveryStatus
    http_status: int | None = None
    latency_ms: float = Field(ge=0.0)
    error_code: str | None = None
    error_message: str | None = None
    occurred_at: datetime = Field(default_factory=_utc_now)


__all__: Final = ["ChannelConfig", "ChannelKind", "NotificationResult"]
