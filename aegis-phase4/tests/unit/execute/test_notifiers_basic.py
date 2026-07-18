"""Notifier-base + LogNotifier tests."""

from __future__ import annotations

from uuid import uuid4

from aegis.execute.config import ExecuteSettings
from aegis.execute.notifiers.base import ChannelRegistry
from aegis.execute.notifiers.log import LogNotifier
from aegis.execute.schemas.alert import (
    Alert,
    AlertEnvelope,
    AlertSource,
    DeliveryStatus,
)


def _make_alert() -> Alert:
    return Alert(
        alert_id="a" * 32,
        tenant_id=uuid4(),
        trend_id="t-1",
        verdict="ENTER",
        priority=1,
        score=0.7,
        confidence=0.65,
        source=AlertSource.PHASE2_AND_PHASE3,
        title="t-1 — ENTER",
    )


async def test_log_notifier_always_succeeds():
    n = LogNotifier()
    assert n.enabled is True
    res = await n.send(AlertEnvelope(alert=_make_alert()))
    assert res.status == DeliveryStatus.SUCCESS
    assert res.channel == "log"
    assert res.latency_ms >= 0


async def test_registry_includes_log_by_default():
    reg = ChannelRegistry.from_settings(ExecuteSettings())
    names = [n.name for n in reg.enabled_notifiers()]
    assert "log" in names


async def test_registry_disables_external_channels_without_config():
    s = ExecuteSettings(
        ntfy_topic="",
        telegram_token="",
        telegram_chat_id="",
        discord_webhook_url="",
        generic_webhook_url="",
    )
    reg = ChannelRegistry.from_settings(s)
    enabled = [n.name for n in reg.enabled_notifiers()]
    # Only LogNotifier should be enabled.
    assert enabled == ["log"]


async def test_registry_enables_ntfy_with_topic():
    s = ExecuteSettings(ntfy_topic="my-topic", ntfy_base_url="https://ntfy.example")
    reg = ChannelRegistry.from_settings(s)
    names = [n.name for n in reg.enabled_notifiers()]
    assert "ntfy" in names


async def test_registry_aclose_all_does_not_raise():
    reg = ChannelRegistry.from_settings(ExecuteSettings())
    await reg.aclose_all()  # idempotent, no-op safe
