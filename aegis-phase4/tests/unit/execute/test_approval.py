"""Unit tests for Phase 6 approval workflow."""

from __future__ import annotations

import asyncio

import pytest

from aegis.execute.approval import ApprovalBroker, ApprovalRequest, ApprovalResult


def _req(**overrides) -> ApprovalRequest:
    defaults = {
        "plan_id": "plan-abc",
        "trend_id": "trend-xyz",
        "quantity": 5,
        "capital_usd": 100.0,
        "estimated_profit_usd": 40.0,
        "kelly_fraction": 0.25,
        "risk_score": 0.3,
    }
    defaults.update(overrides)
    return ApprovalRequest(**defaults)


# ---------------------------------------------------------------------------
# Disabled broker (no credentials)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_broker_returns_timeout():
    broker = ApprovalBroker(bot_token="", chat_id="", timeout_s=1)
    result = await broker.request_approval(_req())
    assert result.decision == "timeout"
    assert isinstance(result, ApprovalResult)


def test_enabled_property_false_without_credentials():
    broker = ApprovalBroker(bot_token="", chat_id="chat-123")
    assert broker.enabled is False


def test_enabled_property_true_with_credentials():
    broker = ApprovalBroker(bot_token="tok", chat_id="chat-123")
    assert broker.enabled is True


# ---------------------------------------------------------------------------
# Callback resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_callback_unknown_request_no_error():
    broker = ApprovalBroker(bot_token="tok", chat_id="cid", timeout_s=1)
    # Should not raise even for an unknown request_id.
    await broker.handle_callback("no-such-id", "approved")


@pytest.mark.asyncio
async def test_approval_resolved_via_callback(monkeypatch):
    """Simulate Telegram send success, then fire a callback from another task."""
    broker = ApprovalBroker(bot_token="tok", chat_id="cid", timeout_s=5)

    # Patch _send_telegram to avoid real HTTP call.
    async def _fake_send(_req: ApprovalRequest) -> bool:
        return True

    monkeypatch.setattr(broker, "_send_telegram", _fake_send)

    req = _req()

    async def _resolve_later() -> None:
        await asyncio.sleep(0.05)
        await broker.handle_callback(req.request_id, "approved", decided_by="test_user")

    task = asyncio.create_task(_resolve_later())
    result = await broker.request_approval(req)
    await task

    assert result.decision == "approved"
    assert result.plan_id == req.plan_id


@pytest.mark.asyncio
async def test_rejection_resolved_via_callback(monkeypatch):
    broker = ApprovalBroker(bot_token="tok", chat_id="cid", timeout_s=5)

    async def _fake_send(_req: ApprovalRequest) -> bool:
        return True

    monkeypatch.setattr(broker, "_send_telegram", _fake_send)
    req = _req()

    async def _resolve_later() -> None:
        await asyncio.sleep(0.05)
        await broker.handle_callback(req.request_id, "rejected")

    task = asyncio.create_task(_resolve_later())
    result = await broker.request_approval(req)
    await task

    assert result.decision == "rejected"


@pytest.mark.asyncio
async def test_request_approval_timeout_when_send_fails(monkeypatch):
    broker = ApprovalBroker(bot_token="tok", chat_id="cid", timeout_s=1)

    async def _fake_send(_req: ApprovalRequest) -> bool:
        return False  # simulate send failure

    monkeypatch.setattr(broker, "_send_telegram", _fake_send)
    result = await broker.request_approval(_req())
    assert result.decision == "timeout"
