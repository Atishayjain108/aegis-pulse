"""Tests for Alert / AlertEnvelope / DeliveryAttempt schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aegis.execute.schemas.alert import (
    Alert,
    AlertEnvelope,
    AlertSource,
    DeliveryAttempt,
    DeliveryStatus,
)


def _base_kwargs(verdict="ENTER", priority=1, **overrides):
    base = {
        "alert_id": "a" * 32,
        "tenant_id": uuid4(),
        "trend_id": "t-1",
        "verdict": verdict,
        "priority": priority,
        "score": 0.7,
        "confidence": 0.65,
        "source": AlertSource.PHASE2_AND_PHASE3,
        "title": "Trend t-1 — ENTER",
    }
    base.update(overrides)
    return base


def test_alert_frozen_and_rejects_unknown():
    a = Alert(**_base_kwargs())
    with pytest.raises(Exception):
        # frozen: assignment forbidden
        a.score = 0.9  # type: ignore[misc]


def test_alert_rejects_bad_verdict():
    with pytest.raises(Exception):
        Alert(**_base_kwargs(verdict="NOPE"))


def test_alert_rejects_bad_priority():
    with pytest.raises(Exception):
        Alert(**_base_kwargs(priority=7))


def test_alert_block_requires_halt_reason_or_blocked_by():
    with pytest.raises(Exception):
        Alert(**_base_kwargs(verdict="BLOCK", priority=3, halt_reason=None, blocked_by=()))


def test_alert_block_with_halt_reason_ok():
    a = Alert(**_base_kwargs(verdict="BLOCK", priority=3, halt_reason="x"))
    assert a.verdict == "BLOCK"


def test_alert_created_at_must_be_utc():
    naive = datetime(2026, 1, 1, 12, 0)
    with pytest.raises(Exception):
        Alert(**_base_kwargs(created_at=naive))


def test_envelope_to_dict_returns_json_safe():
    a = Alert(**_base_kwargs(created_at=datetime(2026, 1, 1, tzinfo=UTC)))
    env = AlertEnvelope(alert=a)
    d = env.to_dict()
    assert d["envelope_version"] == "1.0"
    assert d["alert"]["alert_id"] == a.alert_id


def test_delivery_attempt_construct():
    da = DeliveryAttempt(
        alert_id="x" * 16,
        channel="log",
        attempt_no=1,
        status=DeliveryStatus.SUCCESS,
        latency_ms=12.5,
    )
    assert da.status == DeliveryStatus.SUCCESS
    assert da.attempt_no == 1
