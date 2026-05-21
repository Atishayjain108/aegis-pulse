"""Pydantic v2 frozen models for Phase 4."""

from __future__ import annotations

from aegis.execute.schemas.alert import (
    Alert,
    AlertEnvelope,
    AlertOutboxRow,
    AlertSource,
    AlertStatus,
    DeliveryAttempt,
    DeliveryStatus,
)
from aegis.execute.schemas.dashboard import (
    DashboardSnapshot,
    MetricCard,
    SSEEvent,
)
from aegis.execute.schemas.intent import ExecutionIntent
from aegis.execute.schemas.notification import (
    ChannelConfig,
    ChannelKind,
    NotificationResult,
)

__all__ = [
    "Alert",
    "AlertEnvelope",
    "AlertOutboxRow",
    "AlertSource",
    "AlertStatus",
    "ChannelConfig",
    "ChannelKind",
    "DashboardSnapshot",
    "DeliveryAttempt",
    "DeliveryStatus",
    "ExecutionIntent",
    "MetricCard",
    "NotificationResult",
    "SSEEvent",
]
