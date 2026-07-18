"""Tests for `aegis.predict.resilience`."""

from __future__ import annotations

import asyncio

import pytest

from aegis.predict.resilience import (
    CircuitBreaker,
    decorrelated_jitter,
    get_breaker,
    reset_all_breakers,
    resilient_call,
)


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Process-global breaker state must not leak between tests."""
    reset_all_breakers()
    yield
    reset_all_breakers()


class TestResilientCall:
    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        async def f():
            return 42

        v = await resilient_call(f, name="t-success", timeout_s=1.0)
        assert v == 42

    @pytest.mark.asyncio
    async def test_returns_fallback_on_timeout(self):
        async def f():
            await asyncio.sleep(5)

        v = await resilient_call(
            f,
            name="t-timeout",
            timeout_s=0.05,
            retries=0,
            fallback=lambda: "FALLBACK",
        )
        assert v == "FALLBACK"

    @pytest.mark.asyncio
    async def test_async_fallback_supported(self):
        async def f():
            raise RuntimeError("boom")

        async def fb():
            return "ASYNC_FALLBACK"

        v = await resilient_call(
            f,
            name="t-async-fb",
            timeout_s=1.0,
            retries=0,
            fallback=fb,
        )
        assert v == "ASYNC_FALLBACK"

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self):
        attempts = {"n": 0}

        async def f():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("transient")
            return "ok"

        v = await resilient_call(
            f,
            name="t-retries",
            timeout_s=1.0,
            retries=3,
        )
        assert v == "ok"
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_no_fallback_then_raises(self):
        async def f():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await resilient_call(
                f,
                name="t-noFallback",
                timeout_s=1.0,
                retries=1,
            )

    @pytest.mark.asyncio
    async def test_open_breaker_short_circuits_to_fallback(self):
        cb = CircuitBreaker(name="t-cb", min_samples=1, error_rate_threshold=0.1)
        # Force open by recording one failure (min_samples=1, threshold=0.1).
        cb.record(success=False)
        assert cb.is_open()

        async def f():
            return "should_not_run"

        v = await resilient_call(
            f,
            name="t-cb",
            timeout_s=1.0,
            breaker=cb,
            fallback=lambda: "FB",
        )
        assert v == "FB"


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker(name="cb-init")
        assert cb.is_open() is False

    def test_opens_after_error_rate(self):
        cb = CircuitBreaker(
            name="cb-trip",
            min_samples=4,
            error_rate_threshold=0.5,
        )
        for _ in range(4):
            cb.record(success=False)
        assert cb.is_open() is True

    def test_does_not_open_below_threshold(self):
        cb = CircuitBreaker(
            name="cb-noTrip",
            min_samples=4,
            error_rate_threshold=0.9,
        )
        for _ in range(3):
            cb.record(success=False)
        cb.record(success=True)
        # 75% error rate < 90% threshold → still closed.
        assert cb.is_open() is False

    def test_reset_clears_state(self):
        cb = CircuitBreaker(name="cb-reset", min_samples=1, error_rate_threshold=0.1)
        cb.record(success=False)
        assert cb.is_open()
        cb.reset()
        assert cb.is_open() is False


class TestDecorrelatedJitter:
    def test_first_call_returns_in_expected_range(self):
        v = decorrelated_jitter(attempt=1, last_sleep=0.0, base=1.0, cap=10.0)
        assert 0 < v <= 10

    def test_jitter_capped(self):
        # very large attempt + last_sleep should still cap.
        v = decorrelated_jitter(attempt=99, last_sleep=999, base=1.0, cap=5.0)
        assert v <= 5.0


class TestGetBreaker:
    def test_returns_same_breaker_for_same_name(self):
        a = get_breaker("shared-name-1")
        b = get_breaker("shared-name-1")
        assert a is b

    def test_returns_distinct_breakers_for_distinct_names(self):
        a = get_breaker("name-1")
        b = get_breaker("name-2")
        assert a is not b
