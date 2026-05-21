"""HTTP notifier tests (ntfy / telegram / discord / webhook).

Uses httpx.MockTransport to intercept requests without a real network.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx

from aegis.execute.notifiers.discord import DiscordNotifier
from aegis.execute.notifiers.ntfy import NtfyNotifier
from aegis.execute.notifiers.telegram import TelegramNotifier
from aegis.execute.notifiers.webhook import GenericWebhookNotifier
from aegis.execute.schemas.alert import (
    Alert,
    AlertEnvelope,
    AlertSource,
    DeliveryStatus,
)
from aegis.execute.utils.hmac_signer import verify_payload


def _make_alert(verdict: str = "ENTER", priority: int = 1, **extra) -> Alert:
    base = {
        "alert_id": "a" * 32,
        "tenant_id": uuid4(),
        "trend_id": "t-1",
        "verdict": verdict,
        "priority": priority,
        "score": 0.78,
        "confidence": 0.70,
        "source": AlertSource.PHASE2_AND_PHASE3,
        "title": "t-1 — " + verdict,
        "p_breakout_24h": 0.85,
        "p_decline_6h": 0.10,
        "expected_margin_usd": 4.20,
        "loss_probability": 0.15,
        "summary_text": "Phase 4 test message.",
    }
    base.update(extra)
    return Alert(**base)


def _swap_client(notifier, transport: httpx.MockTransport):
    """Replace the notifier's AsyncClient with one bound to a MockTransport."""
    notifier._client = httpx.AsyncClient(transport=transport, timeout=5)


# ---------------------------------------------------------------------------
# ntfy
# ---------------------------------------------------------------------------
async def test_ntfy_success():
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, text="ok")

    n = NtfyNotifier(base_url="https://ntfy.sh", topic="aegis-test")
    _swap_client(n, httpx.MockTransport(handler))

    res = await n.send(AlertEnvelope(alert=_make_alert()))
    assert res.status == DeliveryStatus.SUCCESS
    assert res.http_status == 200
    assert len(calls) == 1
    assert calls[0].url.path == "/aegis-test"
    assert "Trend: t-1" in calls[0].content.decode()
    headers = dict(calls[0].headers)
    assert headers.get("priority") == "4"  # P1 → 4
    assert "rocket" in headers.get("tags", "")


async def test_ntfy_failure_propagates():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream busy")

    n = NtfyNotifier(base_url="https://ntfy.sh", topic="t")
    _swap_client(n, httpx.MockTransport(handler))
    res = await n.send(AlertEnvelope(alert=_make_alert()))
    assert res.status == DeliveryStatus.FAILURE
    assert res.http_status == 503
    assert res.error_code == "AEGIS-EXEC-0031"


async def test_ntfy_disabled_without_topic():
    n = NtfyNotifier(base_url="https://ntfy.sh", topic="")
    assert n.enabled is False
    res = await n.send(AlertEnvelope(alert=_make_alert()))
    assert res.status == DeliveryStatus.SKIPPED


# ---------------------------------------------------------------------------
# telegram
# ---------------------------------------------------------------------------
async def test_telegram_success_uses_markdownv2():
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={"ok": True})

    n = TelegramNotifier(bot_token="botX:abc", chat_id="123")
    _swap_client(n, httpx.MockTransport(handler))

    res = await n.send(AlertEnvelope(alert=_make_alert()))
    assert res.status == DeliveryStatus.SUCCESS
    body = json.loads(captured[0].content.decode())
    assert body["chat_id"] == "123"
    assert body["parse_mode"] == "MarkdownV2"
    # MarkdownV2 reserved chars in `t-1` (the `-`) are escaped
    assert "\\-" in body["text"] or "trend\\_id" in body["text"]


async def test_telegram_disabled_without_token():
    n = TelegramNotifier(bot_token="", chat_id="123")
    assert n.enabled is False


# ---------------------------------------------------------------------------
# discord
# ---------------------------------------------------------------------------
async def test_discord_204_is_success():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    n = DiscordNotifier(webhook_url="https://discord.example/webhook/X")
    _swap_client(n, httpx.MockTransport(handler))
    res = await n.send(AlertEnvelope(alert=_make_alert()))
    assert res.status == DeliveryStatus.SUCCESS
    assert res.http_status == 204


async def test_discord_embed_includes_fields():
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(204)

    n = DiscordNotifier(webhook_url="https://discord.example/x")
    _swap_client(n, httpx.MockTransport(handler))
    await n.send(AlertEnvelope(alert=_make_alert()))
    payload = json.loads(captured[0].content.decode())
    embed = payload["embeds"][0]
    field_names = {f["name"] for f in embed["fields"]}
    assert {"Verdict", "Priority", "Score", "Confidence", "Trend"}.issubset(field_names)


# ---------------------------------------------------------------------------
# webhook (HMAC-signed)
# ---------------------------------------------------------------------------
async def test_generic_webhook_signs_body():
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200)

    key = "shared-secret-123"
    n = GenericWebhookNotifier(url="https://hooks.example/x", hmac_key=key)
    _swap_client(n, httpx.MockTransport(handler))
    res = await n.send(AlertEnvelope(alert=_make_alert()))
    assert res.status == DeliveryStatus.SUCCESS
    req = captured[0]
    body = req.content
    sig = req.headers.get("x-aegis-signature")
    assert sig is not None
    assert verify_payload(key=key, payload=body, signature_hex=sig) is True


async def test_generic_webhook_disabled_without_hmac():
    n = GenericWebhookNotifier(url="https://x.example", hmac_key="")
    assert n.enabled is False
    res = await n.send(AlertEnvelope(alert=_make_alert()))
    assert res.status == DeliveryStatus.SKIPPED


async def test_generic_webhook_handles_timeout():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=req)

    n = GenericWebhookNotifier(url="https://x.example", hmac_key="k")
    _swap_client(n, httpx.MockTransport(handler))
    res = await n.send(AlertEnvelope(alert=_make_alert()))
    assert res.status == DeliveryStatus.TIMEOUT
    assert res.error_code == "AEGIS-EXEC-0030"
