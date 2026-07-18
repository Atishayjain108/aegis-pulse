"""Discord webhook notifier.

Uses Discord's incoming webhook URL (no bot OAuth needed). Sends a rich
embed with colour-coded priority and a compact field grid.

Reference: https://discord.com/developers/docs/resources/webhook
"""

from __future__ import annotations

from typing import Final

from aegis.execute.constants import NOTIFY_TIMEOUT_S
from aegis.execute.errors import EXEC_NOTIFIER_DISABLED
from aegis.execute.notifiers._http import HttpNotifierMixin
from aegis.execute.notifiers.base import Notifier
from aegis.execute.schemas.alert import AlertEnvelope, DeliveryStatus
from aegis.execute.schemas.notification import ChannelKind, NotificationResult

# Discord embed colours per verdict (integer RGB).
_COLOUR: Final[dict[str, int]] = {
    "ENTER": 0x2ECC71,   # green
    "EXIT": 0xE74C3C,    # red
    "HOLD": 0xF1C40F,    # yellow
    "BLOCK": 0x95A5A6,   # grey
    "DEGRADED": 0xE67E22,  # orange
}


class DiscordNotifier(HttpNotifierMixin, Notifier):
    """Send a Discord-style embed via webhook URL."""

    name = "discord"
    kind = ChannelKind.DISCORD

    def __init__(
        self,
        *,
        webhook_url: str,
        timeout_s: float = NOTIFY_TIMEOUT_S,
    ) -> None:
        HttpNotifierMixin.__init__(self, timeout_s=timeout_s)
        if not webhook_url:
            self.enabled = False
            self._url = ""
            return
        self._url = webhook_url
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

        alert = envelope.alert
        embed = _build_embed(alert)
        body = {
            "username": "AEGIS Pulse",
            "embeds": [embed],
        }
        # Discord returns 204 No Content on success.
        return await self._post_json(
            channel=self.name,
            url=self._url,
            json_body=body,
            accept_204=True,
        )


def _build_embed(alert) -> dict:
    fields: list[dict[str, object]] = [
        {"name": "Verdict", "value": f"`{alert.verdict}`", "inline": True},
        {"name": "Priority", "value": f"P{alert.priority}", "inline": True},
        {"name": "Source", "value": str(alert.source), "inline": True},
        {"name": "Score", "value": f"{alert.score:.2f}", "inline": True},
        {"name": "Confidence", "value": f"{alert.confidence:.2f}", "inline": True},
        {"name": "Trend", "value": f"`{alert.trend_id}`", "inline": True},
    ]
    if alert.p_breakout_24h is not None:
        fields.append({"name": "p_breakout(24h)", "value": f"{alert.p_breakout_24h:.2f}", "inline": True})
    if alert.p_decline_6h is not None:
        fields.append({"name": "p_decline(6h)", "value": f"{alert.p_decline_6h:.2f}", "inline": True})
    if alert.expected_margin_usd is not None:
        fields.append({"name": "E[margin]", "value": f"${alert.expected_margin_usd:.2f}", "inline": True})
    if alert.loss_probability is not None:
        fields.append({"name": "P[loss]", "value": f"{alert.loss_probability:.2f}", "inline": True})
    if alert.advised_units:
        fields.append(
            {
                "name": "Advised",
                "value": f"{alert.advised_units} units / ${alert.advised_capital_usd:.0f}",
                "inline": False,
            }
        )
    if alert.halt_reason:
        fields.append({"name": "Halt reason", "value": f"`{alert.halt_reason}`", "inline": False})

    description = alert.summary_text if len(alert.summary_text) <= 1900 else alert.summary_text[:1899] + "…"

    embed: dict[str, object] = {
        "title": alert.title[:256],
        "description": description,
        "color": _COLOUR.get(alert.verdict, 0x3498DB),
        "fields": fields,
        "timestamp": alert.created_at.isoformat(),
        "footer": {"text": f"alert_id={alert.alert_id[:16]}…"},
    }
    return embed


__all__: Final = ["DiscordNotifier"]
