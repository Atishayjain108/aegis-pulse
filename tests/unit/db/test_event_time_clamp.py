"""Regression for audit P4-1: the `ts` partition/recency column must not be
anchored to absurd article publish dates (which fragmented the hypertable into
ancient chunks back to 2011 and made recency windows miss old-published content)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aegis.db.signals import _clamp_event_time

_NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def test_none_posted_at_uses_scraped_at():
    assert _clamp_event_time(None, _NOW) == _NOW


def test_recent_posted_at_is_kept():
    recent = _NOW - timedelta(days=2)
    assert _clamp_event_time(recent, _NOW) == recent  # idempotency preserved


def test_ancient_posted_at_anchors_to_ingestion():
    ancient = datetime(2011, 3, 9, tzinfo=UTC)
    assert _clamp_event_time(ancient, _NOW) == _NOW  # no 2011 chunk


def test_future_posted_at_anchors_to_ingestion():
    future = _NOW + timedelta(days=5)
    assert _clamp_event_time(future, _NOW) == _NOW


def test_boundary_just_inside_window_kept():
    edge = _NOW - timedelta(days=44, hours=23)
    assert _clamp_event_time(edge, _NOW) == edge
