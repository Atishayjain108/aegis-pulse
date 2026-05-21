"""Telegram bot notifier.

Uses the Telegram Bot API `sendMessage` endpoint:
    POST https://api.telegram.org/bot{token}/sendMessage

Parses message body as MarkdownV2 — we escape Telegram's reserved chars
so verdict tags and trend IDs that contain `_`, `*`, `[`, `]`, etc. don't
break the parser.

Channel auto-disables if either bot token or chat ID is missing.
"""

from __future__ import annotations

from typing import Final

import structlog

from aegis.execute.constants import NOTIFY_TIMEOUT_S
from aegis.execute.errors import EXEC_NOTIFIER_DISABLED
from aegis.execute.notifiers._http import HttpNotifierMixin
from aegis.execute.notifiers.base import Notifier
from aegis.execute.schemas.alert import AlertEnvelope, DeliveryStatus
from aegis.execute.schemas.notification import ChannelKind, NotificationResult

_log = structlog.get_logger(__name__)

# Reserved chars for MarkdownV2 per Telegram docs.
_MDV2_RESERVED: Final = "_*[]()~`>#+-=|{}.!"


def _esc(s: str) -> str:
    """Escape MarkdownV2 reserved chars."""
    out_chars: list[str] = []
    for ch in s:
        if ch in _MDV2_RESERVED:
            out_chars.append("\\")
        out_chars.append(ch)
    return "".join(out_chars)


class TelegramNotifier(HttpNotifierMixin, Notifier):
    """Send alerts to a Telegram chat via a bot."""

    name = "telegram"
    kind = ChannelKind.TELEGRAM

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        timeout_s: float = NOTIFY_TIMEOUT_S,
    ) -> None:
        HttpNotifierMixin.__init__(self, timeout_s=timeout_s)
        if not bot_token or not chat_id:
            self.enabled = False
            self._url = ""
            self._chat_id = ""
            return
        # NB: token is in the URL by Telegram's design; we never log this URL.
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = str(chat_id)
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
        text = _format_message(alert)
        body = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }
        return await self._post_json(
            channel=self.name,
            url=self._url,
            json_body=body,
        )


_VERDICT_EMOJI: Final[dict[str, str]] = {
    "ENTER": "🚀",
    "EXIT": "🚨",
    "HOLD": "⏳",
    "BLOCK": "⛔",
    "DEGRADED": "⚠️",
}


def _format_message(alert) -> str:
    """Build a MarkdownV2 message body. Escapes inputs carefully."""
    emoji = _VERDICT_EMOJI.get(alert.verdict, "🔔")
    lines: list[str] = [
        f"{emoji} *{_esc(alert.verdict)}* \\(P{alert.priority}\\) — {_esc(alert.title)}",
        f"`trend_id={_esc(alert.trend_id)}`",
        (
            f"score=`{alert.score:.2f}`  "
            f"confidence=`{alert.confidence:.2f}`"
        ),
    ]
    quant_bits: list[str] = []
    if alert.p_breakout_24h is not None:
        quant_bits.append(f"p\\_breakout\\(24h\\)=`{alert.p_breakout_24h:.2f}`")
    if alert.p_decline_6h is not None:
        quant_bits.append(f"p\\_decline\\(6h\\)=`{alert.p_decline_6h:.2f}`")
    if alert.expected_margin_usd is not None:
        quant_bits.append(f"E\\[margin\\]=`${alert.expected_margin_usd:.2f}`")
    if alert.loss_probability is not None:
        quant_bits.append(f"P\\[loss\\]=`{alert.loss_probability:.2f}`")
    if quant_bits:
        lines.append(" \\| ".join(quant_bits))
    if alert.halt_reason:
        lines.append(f"halt: `{_esc(alert.halt_reason)}`")
    if alert.summary_text:
        body = alert.summary_text if len(alert.summary_text) <= 800 else alert.summary_text[:799] + "…"
        lines.append("")
        lines.append(_esc(body))
    return "\n".join(lines)


__all__: Final = ["TelegramNotifier"]
