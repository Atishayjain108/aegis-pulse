"""Notifier package — outbound channels for Phase 4 alerts.

A notifier is anything that satisfies the `Notifier` ABC: an async `send`
that turns an `AlertEnvelope` into a `NotificationResult`. The drainer
fans out to every registered, enabled notifier per alert.

The `LogNotifier` is the only always-on notifier. External channels
(ntfy, Telegram, Discord, webhook) auto-disable when their config env
vars are absent — so the system runs out of the box with zero secrets.
"""

from __future__ import annotations

from aegis.execute.notifiers.base import ChannelRegistry, Notifier
from aegis.execute.notifiers.discord import DiscordNotifier
from aegis.execute.notifiers.log import LogNotifier
from aegis.execute.notifiers.ntfy import NtfyNotifier
from aegis.execute.notifiers.telegram import TelegramNotifier
from aegis.execute.notifiers.webhook import GenericWebhookNotifier

__all__ = [
    "ChannelRegistry",
    "DiscordNotifier",
    "GenericWebhookNotifier",
    "LogNotifier",
    "Notifier",
    "NtfyNotifier",
    "TelegramNotifier",
]
