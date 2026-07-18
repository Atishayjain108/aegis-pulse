"""Tests for decorrelated jitter backoff."""

from __future__ import annotations

import random

import pytest

from aegis.execute.utils.backoff import next_backoff_seconds


def test_first_attempt_within_base_and_3x_base():
    rng = random.Random(42)
    v = next_backoff_seconds(previous=0.0, base=1.0, cap=60.0, rng=rng)
    # With previous=0, upper = max(base, 0) = base, so v == base.
    assert v == 1.0


def test_subsequent_attempts_grow_bounded_by_cap():
    rng = random.Random(42)
    samples = []
    prev = 1.0
    for _ in range(20):
        v = next_backoff_seconds(previous=prev, base=1.0, cap=10.0, rng=rng)
        samples.append(v)
        prev = v
    assert all(1.0 <= s <= 10.0 for s in samples)


def test_invalid_previous_raises():
    with pytest.raises(ValueError):
        next_backoff_seconds(previous=-1, base=1.0, cap=10.0)


def test_invalid_base_or_cap_raises():
    with pytest.raises(ValueError):
        next_backoff_seconds(previous=1.0, base=0.0, cap=10.0)
    with pytest.raises(ValueError):
        next_backoff_seconds(previous=1.0, base=1.0, cap=0.0)
