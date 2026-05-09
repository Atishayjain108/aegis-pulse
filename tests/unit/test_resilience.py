"""Unit tests for aegis.core.resilience (circuit breaker + retry)."""

from __future__ import annotations

import asyncio
import time

import pytest

from aegis.core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    ErrorCategory,
    ExhaustedError,
    ResiliencePolicy,
    ResilientError,
    _decorrelated_jitter,
    get_breaker,
    reset_all_breakers,
    resilient_call,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_breakers():
    """Clear the global breaker registry before each test."""
    reset_all_breakers()
    yield
    reset_all_breakers()


# ---------------------------------------------------------------------------
# _decorrelated_jitter
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_jitter_returns_float_in_range():
    for _ in range(20):
        j = _decorrelated_jitter(0.5, 0.1, 2.0)
        assert 0.1 <= j <= 2.0


@pytest.mark.unit
def test_jitter_cap_respected():
    for _ in range(20):
        j = _decorrelated_jitter(10.0, 0.5, 1.0)
        assert j <= 1.0


# ---------------------------------------------------------------------------
# ResiliencePolicy validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_policy_valid():
    p = ResiliencePolicy(name="test.op", max_attempts=3, timeout=5.0)
    assert p.max_attempts == 3
    assert p.name == "test.op"


@pytest.mark.unit
def test_policy_rejects_zero_attempts():
    with pytest.raises(ValueError, match="max_attempts"):
        ResiliencePolicy(name="x", max_attempts=0, timeout=1.0)


@pytest.mark.unit
def test_policy_rejects_zero_timeout():
    with pytest.raises(ValueError, match="timeout"):
        ResiliencePolicy(name="x", max_attempts=1, timeout=0.0)


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_circuit_starts_closed():
    cb = CircuitBreaker(name="test.cb", fail_threshold=3, reset_timeout=60.0)
    cb.check()  # should not raise


@pytest.mark.unit
def test_circuit_opens_after_consecutive_failures():
    cb = CircuitBreaker(name="test.cb", fail_threshold=3, reset_timeout=60.0)
    cb.record_failure()
    cb.record_failure()
    cb.check()  # still closed after 2
    cb.record_failure()  # 3rd — trips
    with pytest.raises(CircuitOpenError):
        cb.check()


@pytest.mark.unit
def test_circuit_resets_on_success():
    cb = CircuitBreaker(name="test.cb2", fail_threshold=2, reset_timeout=60.0)
    cb.record_failure()
    cb.record_failure()  # trips
    with pytest.raises(CircuitOpenError):
        cb.check()

    # Force the circuit half-open by backdating opened_at
    cb._state.opened_at = time.monotonic() - 9999
    cb.check()  # half-open probe allowed
    cb.record_success()
    cb.check()  # should be closed again


@pytest.mark.unit
def test_circuit_half_open_rejects_concurrent_probes():
    cb = CircuitBreaker(name="test.cb3", fail_threshold=2, reset_timeout=1.0)
    cb.record_failure()
    cb.record_failure()  # trips

    cb._state.opened_at = time.monotonic() - 9999  # force cooldown elapsed
    cb.check()  # first caller gets the probe
    with pytest.raises(CircuitOpenError):
        cb.check()  # second caller blocked


@pytest.mark.unit
def test_get_breaker_returns_same_instance():
    b1 = get_breaker("shared.test")
    b2 = get_breaker("shared.test")
    assert b1 is b2


@pytest.mark.unit
def test_get_breaker_different_names():
    b1 = get_breaker("domain.op1")
    b2 = get_breaker("domain.op2")
    assert b1 is not b2


@pytest.mark.unit
def test_circuit_prunes_old_samples():
    cb = CircuitBreaker(
        name="test.prune",
        fail_threshold=5,
        reset_timeout=60.0,
        error_rate_window=0.01,  # 10ms window
    )
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.02)  # wait past window
    cb._prune(time.monotonic())
    assert len(cb._state.recent_failures) == 0


# ---------------------------------------------------------------------------
# resilient_call decorator
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resilient_call_success():
    policy = ResiliencePolicy(name="test.success", max_attempts=1, timeout=5.0)

    @resilient_call(policy)
    async def fn() -> int:
        return 42

    result = await fn()
    assert result == 42


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resilient_call_retries_on_failure():
    call_count = 0
    policy = ResiliencePolicy(
        name="test.retry",
        max_attempts=3,
        timeout=5.0,
        base_delay=0.001,
        max_delay=0.01,
    )

    @resilient_call(policy)
    async def fn() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("not ready yet")
        return "ok"

    result = await fn()
    assert result == "ok"
    assert call_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resilient_call_exhausted_raises():
    policy = ResiliencePolicy(
        name="test.exhausted",
        max_attempts=2,
        timeout=5.0,
        base_delay=0.001,
        max_delay=0.005,
    )

    @resilient_call(policy)
    async def fn() -> None:
        raise RuntimeError("always fails")

    with pytest.raises(ExhaustedError):
        await fn()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resilient_call_fallback_used_on_exhaustion():
    policy = ResiliencePolicy(
        name="test.fallback",
        max_attempts=2,
        timeout=5.0,
        base_delay=0.001,
        max_delay=0.005,
    )

    async def _fallback() -> str:
        return "fallback result"

    @resilient_call(policy, fallback=_fallback)
    async def fn() -> str:
        raise RuntimeError("always fails")

    result = await fn()
    assert result == "fallback result"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resilient_call_timeout_triggers():
    policy = ResiliencePolicy(
        name="test.timeout",
        max_attempts=1,
        timeout=0.01,
        base_delay=0.001,
        max_delay=0.01,
    )

    @resilient_call(policy)
    async def fn() -> None:
        await asyncio.sleep(10.0)  # way too long

    with pytest.raises(ExhaustedError):
        await fn()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resilient_call_do_not_retry_on_bubbles_immediately():
    call_count = 0
    policy = ResiliencePolicy(
        name="test.no_retry",
        max_attempts=5,
        timeout=5.0,
        base_delay=0.001,
        max_delay=0.01,
        do_not_retry_on=(ValueError,),
    )

    @resilient_call(policy)
    async def fn() -> None:
        nonlocal call_count
        call_count += 1
        raise ValueError("auth error")

    with pytest.raises(ValueError):
        await fn()
    assert call_count == 1  # no retries


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resilient_call_circuit_opens_after_failures():
    policy = ResiliencePolicy(
        name="test.circuit_open",
        max_attempts=1,
        timeout=5.0,
        base_delay=0.001,
        max_delay=0.005,
        circuit_open_after=3,
    )

    @resilient_call(policy)
    async def fn() -> None:
        raise RuntimeError("fail")

    # 3 failures trip the breaker
    for _ in range(3):
        with pytest.raises(ExhaustedError):
            await fn()

    # Next call should raise CircuitOpenError immediately
    with pytest.raises(CircuitOpenError):
        await fn()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resilient_call_circuit_fallback_on_open():
    policy = ResiliencePolicy(
        name="test.circuit_fallback",
        max_attempts=1,
        timeout=5.0,
        base_delay=0.001,
        max_delay=0.005,
        circuit_open_after=2,
    )

    async def _fallback() -> str:
        return "circuit fallback"

    @resilient_call(policy, fallback=_fallback)
    async def fn() -> str:
        raise RuntimeError("fail")

    for _ in range(2):
        result = await fn()
        assert result == "circuit fallback"  # fallback used even before open

    # After circuit opens, fallback still serves
    result = await fn()
    assert result == "circuit fallback"


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resilient_error_attributes():
    err = ResilientError(
        message="test error",
        category=ErrorCategory.TIMEOUT,
        policy_name="test.op",
    )
    assert err.category == ErrorCategory.TIMEOUT
    assert err.policy_name == "test.op"
    assert str(err) == "test error"


@pytest.mark.unit
def test_error_category_values():
    assert ErrorCategory.TIMEOUT == "timeout"
    assert ErrorCategory.CIRCUIT_OPEN == "circuit_open"
    assert ErrorCategory.RETRYABLE == "retryable"
