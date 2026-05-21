"""Log-only notifier.

Always enabled. Always succeeds. The system's baseline delivery channel
— ensures `enabled_notifiers()` is never empty even with zero secrets,
so the drainer always has at least one path that can mark an alert
delivered.

Structlog is configured by `aegis.config` at process startup; we just
get a logger and emit a structured event.
"""

from __future__ import annotations

import time
from typing import Final

import structlog

from aegis.execute.notifiers.base import Notifier
from aegis.execute.schemas.alert import AlertEnvelope, DeliveryStatus
from aegis.execute.schemas.notification import ChannelKind, NotificationResult

_log = structlog.get_logger("aegis.execute.alerts")


class LogNotifier(Notifier):
    """Emit a structured log line per alert.

    This is the canonical 'always works' channel. Operators can grep the
    logs even when no other channel is configured.
    """

    name = "log"
    enabled = True
    kind = ChannelKind.LOG

    def __init__(self, *, channel_name: str = "log") -> None:
        self.name = channel_name

    async def send(self, envelope: AlertEnvelope) -> NotificationResult:
        start = time.monotonic()
        alert = envelope.alert
        _log.info(
            "execute.alert.emitted",
            alert_id=alert.alert_id,
            tenant_id=str(alert.tenant_id),
            trend_id=alert.trend_id,
            verdict=alert.verdict,
            priority=alert.priority,
            score=round(alert.score, 4),
            confidence=round(alert.confidence, 4),
            source=str(alert.source),
            title=alert.title,
            halt_reason=alert.halt_reason,
            blocked_by=list(alert.blocked_by),
            correlation_id=alert.correlation_id,
        )
        elapsed_ms = (time.monotonic() - start) * 1000.0
        return NotificationResult(
            channel=self.name,
            status=DeliveryStatus.SUCCESS,
            http_status=None,
            latency_ms=elapsed_ms,
            error_code=None,
            error_message=None,
        )


__all__: Final = ["LogNotifier"]
