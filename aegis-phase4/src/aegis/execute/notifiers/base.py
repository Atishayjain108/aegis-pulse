"""Notifier ABC and channel registry.

`Notifier` is the contract every channel implements. Three properties
must be settled at construction time:

  * `name`     — stable identifier, persisted in `alert_deliveries.channel`.
  * `enabled`  — False channels are dropped at startup; their `send`
                  never runs. The drainer relies on this to skip cleanly.
  * `timeout_s` — per-call budget; the channel raises an internal
                  AegisExecuteError(EXEC_NOTIFIER_TIMEOUT) on overrun.

A `ChannelRegistry` is a tiny factory that builds a tuple of enabled
notifiers from an `ExecuteSettings`. All channels degrade quietly: a
missing token simply disables the channel and emits a structured log
line at INFO level.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable
from typing import Final

import structlog

from aegis.execute.config import ExecuteSettings
from aegis.execute.constants import NOTIFY_TIMEOUT_S
from aegis.execute.schemas.alert import AlertEnvelope
from aegis.execute.schemas.notification import ChannelKind, NotificationResult

_log = structlog.get_logger(__name__)


class Notifier(abc.ABC):
    """Abstract notification channel.

    Subclasses must implement `send`. They MUST NOT raise from `send`;
    the contract is to return a `NotificationResult` with a non-success
    status on failure. The drainer's `_call_one` adds defence in depth
    by catching, but disciplined subclasses never let exceptions escape.
    """

    #: Stable identifier, persisted in alert_deliveries.channel
    name: str = "base"
    #: True ⇒ included in the drainer's fan-out
    enabled: bool = False
    #: Per-call timeout budget in seconds
    timeout_s: float = NOTIFY_TIMEOUT_S
    #: Channel kind (enum)
    kind: ChannelKind = ChannelKind.LOG

    @abc.abstractmethod
    async def send(self, envelope: AlertEnvelope) -> NotificationResult:
        """Deliver the envelope. Always returns a result; never raises."""
        raise NotImplementedError

    # Optional lifecycle hook for HTTP-based notifiers
    async def aclose(self) -> None:  # pragma: no cover - default no-op
        """Release any held resources (HTTP clients, etc.)."""
        return

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"{type(self).__name__}(name={self.name!r}, enabled={self.enabled})"


class ChannelRegistry:
    """Factory + container for notifier instances.

    Use `ChannelRegistry.from_settings(settings)` to build the default
    set. The registry guarantees:
      * `LogNotifier` is always present and enabled.
      * Any channel whose config is missing is built but `enabled=False`.

    The drainer is given the `enabled_notifiers()` view.
    """

    __slots__ = ("_notifiers",)

    def __init__(self, notifiers: Iterable[Notifier]) -> None:
        self._notifiers: list[Notifier] = list(notifiers)

    @classmethod
    def from_settings(cls, settings: ExecuteSettings) -> ChannelRegistry:
        # Lazy imports avoid circular references at module-load time.
        from aegis.execute.notifiers.discord import DiscordNotifier
        from aegis.execute.notifiers.log import LogNotifier
        from aegis.execute.notifiers.ntfy import NtfyNotifier
        from aegis.execute.notifiers.telegram import TelegramNotifier
        from aegis.execute.notifiers.webhook import GenericWebhookNotifier

        notifiers: list[Notifier] = [LogNotifier()]

        if settings.ntfy_topic:
            notifiers.append(
                NtfyNotifier(
                    base_url=settings.ntfy_base_url,
                    topic=settings.ntfy_topic,
                    timeout_s=settings.notify_timeout_s,
                )
            )
        else:
            _log.info("notifier.disabled", channel="ntfy", reason="no topic")

        if settings.telegram_token and settings.telegram_chat_id:
            notifiers.append(
                TelegramNotifier(
                    bot_token=settings.telegram_token,
                    chat_id=settings.telegram_chat_id,
                    timeout_s=settings.notify_timeout_s,
                )
            )
        else:
            _log.info("notifier.disabled", channel="telegram", reason="no token/chat")

        if settings.discord_webhook_url:
            notifiers.append(
                DiscordNotifier(
                    webhook_url=settings.discord_webhook_url,
                    timeout_s=settings.notify_timeout_s,
                )
            )
        else:
            _log.info("notifier.disabled", channel="discord", reason="no webhook")

        if settings.generic_webhook_url:
            notifiers.append(
                GenericWebhookNotifier(
                    url=settings.generic_webhook_url,
                    hmac_key=settings.hmac_key,
                    timeout_s=settings.notify_timeout_s,
                )
            )
        else:
            _log.info("notifier.disabled", channel="webhook", reason="no url")

        return cls(notifiers)

    def all(self) -> tuple[Notifier, ...]:
        return tuple(self._notifiers)

    def enabled_notifiers(self) -> tuple[Notifier, ...]:
        return tuple(n for n in self._notifiers if n.enabled)

    async def aclose_all(self) -> None:
        for n in self._notifiers:
            try:
                await n.aclose()
            except Exception as exc:
                _log.warning("notifier.aclose_failed", name=n.name, error=str(exc))


__all__: Final = ["ChannelRegistry", "Notifier"]
