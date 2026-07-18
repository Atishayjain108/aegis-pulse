"""Tests for compute_alert_id / compute_intent_id / compute_dedup_hash."""

from __future__ import annotations

from uuid import UUID

from aegis.execute.utils.hashing import (
    ALERT_ID_HEX_LEN,
    INTENT_ID_HEX_LEN,
    compute_alert_id,
    compute_dedup_hash,
    compute_intent_id,
)


def test_alert_id_is_deterministic():
    tid = UUID(int=1)
    a = compute_alert_id(
        tenant_id=tid, trend_id="t-1", decision_window="w1", verdict="ENTER", priority=1
    )
    b = compute_alert_id(
        tenant_id=tid, trend_id="t-1", decision_window="w1", verdict="ENTER", priority=1
    )
    assert a == b
    assert len(a) == ALERT_ID_HEX_LEN
    assert all(c in "0123456789abcdef" for c in a)


def test_alert_id_differs_on_any_field_change():
    tid = UUID(int=1)
    base = compute_alert_id(
        tenant_id=tid, trend_id="t", decision_window="w", verdict="ENTER", priority=1
    )
    diff_trend = compute_alert_id(
        tenant_id=tid, trend_id="other", decision_window="w", verdict="ENTER", priority=1
    )
    diff_verdict = compute_alert_id(
        tenant_id=tid, trend_id="t", decision_window="w", verdict="HOLD", priority=1
    )
    diff_priority = compute_alert_id(
        tenant_id=tid, trend_id="t", decision_window="w", verdict="ENTER", priority=0
    )
    diff_window = compute_alert_id(
        tenant_id=tid, trend_id="t", decision_window="other", verdict="ENTER", priority=1
    )
    diff_tenant = compute_alert_id(
        tenant_id=UUID(int=2),
        trend_id="t",
        decision_window="w",
        verdict="ENTER",
        priority=1,
    )
    assert len({base, diff_trend, diff_verdict, diff_priority, diff_window, diff_tenant}) == 6


def test_alert_id_is_case_insensitive_for_normalised_fields():
    tid = UUID(int=1)
    a = compute_alert_id(
        tenant_id=tid, trend_id="Trend-1", decision_window="W", verdict="enter", priority=1
    )
    b = compute_alert_id(
        tenant_id=tid, trend_id="trend-1", decision_window="w", verdict="ENTER", priority=1
    )
    assert a == b


def test_intent_id_is_deterministic_and_distinct():
    aid = "abc123"
    i1 = compute_intent_id(alert_id=aid, kind="enter_position")
    i2 = compute_intent_id(alert_id=aid, kind="enter_position")
    i3 = compute_intent_id(alert_id=aid, kind="exit_position")
    assert i1 == i2
    assert i1 != i3
    assert len(i1) == INTENT_ID_HEX_LEN


def test_dedup_hash_per_channel_differs():
    h1 = compute_dedup_hash(alert_id="x", channel="ntfy")
    h2 = compute_dedup_hash(alert_id="x", channel="telegram")
    assert h1 != h2
