"""Tests for `aegis.harden.utils`."""

from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest

from aegis.harden.utils.clock import FixedClock, SystemClock
from aegis.harden.utils.rng import SeededRng, default_rng


class TestSeededRng:
    def test_same_seed_same_sequence(self) -> None:
        a = SeededRng(seed=42)
        b = SeededRng(seed=42)
        assert [a.py.random() for _ in range(10)] == [b.py.random() for _ in range(10)]

    def test_different_seed_different_sequence(self) -> None:
        a = [SeededRng(seed=1).py.random() for _ in range(10)]
        b = [SeededRng(seed=2).py.random() for _ in range(10)]
        assert a != b

    def test_numpy_and_python_independent(self) -> None:
        rng = SeededRng(seed=10)
        py_first = rng.py.random()
        np_first = float(rng.np.random())
        rng2 = SeededRng(seed=10)
        np_first2 = float(rng2.np.random())  # call numpy first this time
        py_first2 = rng2.py.random()
        # Numpy stream is identical regardless of stdlib calls
        assert np_first == np_first2
        assert py_first == py_first2

    def test_choice_empty_raises(self) -> None:
        rng = SeededRng()
        with pytest.raises(IndexError):
            rng.choice([])

    def test_choice_one_element(self) -> None:
        rng = SeededRng()
        assert rng.choice(("only",)) == "only"

    def test_jitter_clamped_non_negative(self) -> None:
        rng = SeededRng()
        for _ in range(50):
            v = rng.jitter_ms(base_ms=10, spread_ms=1000)
            assert v >= 0

    def test_jitter_zero_spread(self) -> None:
        rng = SeededRng()
        assert rng.jitter_ms(base_ms=42, spread_ms=0) == 42

    def test_default_rng_consistent(self) -> None:
        assert default_rng().seed == default_rng().seed


class TestClock:
    def test_system_clock_is_utc(self) -> None:
        c = SystemClock()
        assert c.now().tzinfo is not None

    def test_fixed_clock_stable(self) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        c = FixedClock(t)
        assert c.now() == t
        assert c.now() == t  # stable across calls

    def test_fixed_clock_requires_tz(self) -> None:
        with pytest.raises(ValueError):
            FixedClock(datetime(2026, 1, 1))

    def test_fixed_clock_set(self) -> None:
        c = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
        c.set(datetime(2026, 6, 1, tzinfo=UTC))
        assert c.now() == datetime(2026, 6, 1, tzinfo=UTC)

    def test_fixed_clock_set_requires_tz(self) -> None:
        c = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
        with pytest.raises(ValueError):
            c.set(datetime(2026, 6, 1))
