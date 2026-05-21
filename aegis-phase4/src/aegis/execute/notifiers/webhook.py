"""Generic webhook notifier with HMAC-SHA256 body signing.

Sends the full `AlertEnvelope` JSON to a user-configured URL with these
headers set:

    X-Aegis-Signature : hex-encoded HMAC-SHA256(body, hmac_key)
    X-Aegis-Envelope-Version : "1.0"
    X-Aegis-Alert-Id : <alert_id>
    Content-Type : application/json

The HMAC key is required. If empty, the channel disables itself rather
than send unsigned payloads — defence against accidental exposure.
"""

from __future__ import annotations

import json
from typing import Any, Final

import structlog

from aegis.execute.constants import (
    ALERT_ENVELOPE_VERSION,
    NOTIFY_TIMEOUT_S,
)
from aegis.execute.errors import (
    EXEC_NOTIFIER_DISABLED,
    EXEC_NOTIFIER_HMAC_REQUIRED,
)
from aegis.execute.notifiers._http import HttpNotifierMixin
from aegis.execute.notifiers.base import Notifier
from aegis.execute.schemas.alert import AlertEnvelope, DeliveryStatus
from aegis.execute.schemas.notification import ChannelKind, NotificationResult
from aegis.execute.utils.hmac_signer import sign_payload

_log = structlog.get_logger(__name__)


class GenericWebhookNotifier(HttpNotifierMixin, Notifier):
    """HMAC-signed JSON POST to an arbitrary URL."""

    name = "webhook"
    kind = ChannelKind.WEBHOOK

    def __init__(
        self,
        *,
        url: str,
        hmac_key: str,
        timeout_s: float = NOTIFY_TIMEOUT_S,
    ) -> None:
        HttpNotifierMixin.__init__(self, timeout_s=timeout_s)
        if not url:
            self.enabled = False
            self._url = ""
            self._hmac_key = ""
            return
        if not hmac_key:
            self.enabled = False
            self._url = url
            self._hmac_key = ""
            _log.warning(
                "notifier.webhook_disabled_no_hmac",
                error_code=EXEC_NOTIFIER_HMAC_REQUIRED.code,
            )
            return
        self._url = url
        self._hmac_key = hmac_key
        self.enabled = self.http_available

    async def send(self, envelope: AlertEnvelope) -> NotificationResult:
        if not self.enabled:
            return NotificationResult(
                channel=self.name,
                status=DeliveryStatus.SKIPPED,
                http_status=None,
                latency_ms=0.0,
                error_code=EXEC_NOTIFIER_DISABLED.code,
                error_message=EXEC_NOTIFIER_DISABLED.message,
            )

        # Serialise deterministically: sort keys for HMAC stability.
        payload_dict: dict[str, Any] = envelope.to_dict()
        body = json.dumps(payload_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = sign_payload(key=self._hmac_key, payload=body)
        headers = {
            "Content-Type": "application/json",
            "X-Aegis-Envelope-Version": ALERT_ENVELOPE_VERSION,
            "X-Aegis-Alert-Id": envelope.alert.alert_id,
        }
        if sig is not None:
            headers["X-Aegis-Signature"] = sig

        return await self._post_json(
            channel=self.name,
            url=self._url,
            data_body=body,
            headers=headers,
        )


__all__: Final = ["GenericWebhookNotifier"]
