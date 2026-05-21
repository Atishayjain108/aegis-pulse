"""Tests for time utilities (clock injection)."""

from __future__ import annotations

from datetime import UTC, datetime

from aegis.execute.utils.time import reset_clock, set_clock, utc_now


def test_utc_now_is_utc_aware():
    t = utc_now()
    assert t.tzinfo is not None
    assert t.utcoffset() == UTC.utcoffset(t)


def test_injected_clock_overrides():
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    set_clock(lambda: fixed)
    try:
        assert utc_now() == fixed
    finally:
        reset_clock()
    # After reset, utc_now returns real time.
    real = utc_now()
    assert real != fixed
