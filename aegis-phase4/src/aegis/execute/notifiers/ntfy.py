"""ntfy.sh notifier.

ntfy is a zero-config, free pub-sub push service. Each tenant subscribes
to a unique topic in their phone app. POST plain text to
`{base_url}/{topic}` and the message lands as a push notification.

This is the recommended channel for solo operators who don't want to
plumb webhooks. Priority is mapped to ntfy's 1..5 levels.
"""

from __future__ import annotations

from typing import Final

import structlog

from aegis.execute.constants import NOTIFY_TIMEOUT_S
from aegis.execute.notifiers._http import HttpNotifierMixin
from aegis.execute.notifiers.base import Notifier
from aegis.execute.schemas.alert import AlertEnvelope, DeliveryStatus
from aegis.execute.schemas.notification import ChannelKind, NotificationResult

_log = structlog.get_logger(__name__)

# Phase 4 priority (0..3, 0=critical) → ntfy priority (1..5, 5=max).
# Lower P number = higher urgency, so we invert.
_NTFY_PRIORITY_MAP: Final[dict[int, str]] = {
    0: "5",  # max / urgent
    1: "4",  # high
    2: "3",  # default
    3: "2",  # low
}


class NtfyNotifier(HttpNotifierMixin, Notifier):
    """Push-to-phone notifier via ntfy.sh."""

    name = "ntfy"
    kind = ChannelKind.NTFY

    def __init__(
        self,
        *,
        base_url: str,
        topic: str,
        timeout_s: float = NOTIFY_TIMEOUT_S,
    ) -> None:
        HttpNotifierMixin.__init__(self, timeout_s=timeout_s)
        if not topic:
            self.enabled = False
            self._url = ""
            return
        # ntfy URL is `{base_url}/{topic}` — strip trailing slashes
        self._url = f"{base_url.rstrip('/')}/{topic.lstrip('/')}"
        self.enabled = self.http_available

    async def send(self, envelope: AlertEnvelope) -> NotificationResult:
        if not self.enabled:
            from aegis.execute.errors import EXEC_NOTIFIER_DISABLED

            return NotificationResult(
                channel=self.name,
                status=DeliveryStatus.SKIPPED,
                http_status=None,
                latency_ms=0.0,
                error_code=EXEC_NOTIFIER_DISABLED.code,
                error_message=EXEC_NOTIFIER_DISABLED.message,
            )

        alert = envelope.alert
        ntfy_priority = _NTFY_PRIORITY_MAP.get(alert.priority, "3")
        body = _format_body(alert)
        headers: dict[str, str] = {
            "Content-Type": "text/plain; charset=utf-8",
            "Title": _ascii_safe_header(_truncate(alert.title, 200)),
            "Priority": ntfy_priority,
            "Tags": ",".join(
                tag
                for tag in (
                    _verdict_tag(alert.verdict),
                    f"P{alert.priority}",
                    str(alert.source),
                )
                if tag
            ),
        }
        return await self._post_json(
            channel=self.name,
            url=self._url,
            data_body=body.encode("utf-8"),
            headers=headers,
        )


def _ascii_safe_header(s: str) -> str:
    """HTTP header values are latin-1 by spec.

    Replace common Unicode punctuation with ASCII equivalents, then
    encode-ignore the rest. Keeps titles readable in clients while
    satisfying the protocol.
    """
    repl = {
        "\u2014": "-",  # em dash
        "\u2013": "-",  # en dash
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
    }
    out = "".join(repl.get(ch, ch) for ch in s)
    return out.encode("ascii", errors="ignore").decode("ascii")


def _verdict_tag(verdict: str) -> str:
    """Map verdict to an emoji shortcode ntfy supports."""
    return {
        "ENTER": "rocket",
        "EXIT": "rotating_light",
        "HOLD": "hourglass_flowing_sand",
        "BLOCK": "no_entry",
        "DEGRADED": "warning",
    }.get(verdict, "bell")


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _format_body(alert) -> str:
    bits: list[str] = []
    bits.append(f"Trend: {alert.trend_id}")
    bits.append(
        f"Verdict={alert.verdict} P{alert.priority} "
        f"score={alert.score:.2f} conf={alert.confidence:.2f}"
    )
    if alert.p_breakout_24h is not None:
        bits.append(f"p_breakout(24h)={alert.p_breakout_24h:.2f}")
    if alert.p_decline_6h is not None:
        bits.append(f"p_decline(6h)={alert.p_decline_6h:.2f}")
    if alert.expected_margin_usd is not None:
        bits.append(f"E[margin]=${alert.expected_margin_usd:.2f}")
    if alert.halt_reason:
        bits.append(f"halt={alert.halt_reason}")
    if alert.summary_text:
        bits.append("")
        bits.append(_truncate(alert.summary_text, 1500))
    return "\n".join(bits)


__all__: Final = ["NtfyNotifier"]
