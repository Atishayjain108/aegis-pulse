"""Unit tests for aegis.llm.gateway.middleware."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from unittest.mock import AsyncMock

import pytest

from aegis.llm.gateway.middleware import (
    CostGateMiddleware,
    LatencyBudgetMiddleware,
    RateLimitMiddleware,
    RequestLogMiddleware,
    _TokenBucket,
)
from aegis.llm.gateway.response import LLMResponse, TokenUsage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(provider: str = "ollama", input_tok: int = 10, output_tok: int = 20) -> LLMResponse:
    return LLMResponse(
        content="hello",
        provider=provider,
        model="test-model",
        usage=TokenUsage(input_tokens=input_tok, output_tokens=output_tok, total_tokens=input_tok + output_tok),
        latency_ms=50.0,
    )


def _fast_fn(provider: str = "ollama") -> AsyncMock:
    return AsyncMock(return_value=_make_response(provider=provider))


# ---------------------------------------------------------------------------
# LatencyBudgetMiddleware
# ---------------------------------------------------------------------------

class TestLatencyBudgetMiddleware:
    async def test_fast_call_no_violation(self) -> None:
        mw = LatencyBudgetMiddleware(warn_ms=5000.0)
        fn = _fast_fn()
        result = await mw(fn)
        assert result.provider == "ollama"
        assert mw.violation_count == 0

    async def test_slow_call_increments_violation_count(self) -> None:
        mw = LatencyBudgetMiddleware(warn_ms=1.0)  # 1ms threshold — any call will trigger

        async def slow_fn() -> LLMResponse:
            await asyncio.sleep(0.005)  # 5ms
            return _make_response()

        await mw(slow_fn)
        assert mw.violation_count == 1

    async def test_multiple_violations_accumulate(self) -> None:
        mw = LatencyBudgetMiddleware(warn_ms=1.0)

        async def slow_fn() -> LLMResponse:
            await asyncio.sleep(0.005)
            return _make_response()

        await mw(slow_fn)
        await mw(slow_fn)
        assert mw.violation_count == 2

    async def test_hard_timeout_raises(self) -> None:
        mw = LatencyBudgetMiddleware(warn_ms=5000.0, hard_ms=10.0)  # 10ms hard limit

        async def hanging_fn() -> LLMResponse:
            await asyncio.sleep(1.0)
            return _make_response()  # pragma: no cover

        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await mw(hanging_fn)

    async def test_no_hard_timeout_completes(self) -> None:
        mw = LatencyBudgetMiddleware(warn_ms=5000.0, hard_ms=None)
        fn = _fast_fn()
        result = await mw(fn)
        assert result is not None


# ---------------------------------------------------------------------------
# _TokenBucket
# ---------------------------------------------------------------------------

class TestTokenBucket:
    def test_full_bucket_can_consume(self) -> None:
        bucket = _TokenBucket(capacity=10.0, refill_rate=1.0)
        assert bucket.consume() is True

    def test_empty_bucket_cannot_consume(self) -> None:
        bucket = _TokenBucket(capacity=1.0, refill_rate=0.001)
        bucket.consume()  # drain the one token
        assert bucket.consume() is False

    def test_available_reflects_balance(self) -> None:
        bucket = _TokenBucket(capacity=5.0, refill_rate=1.0)
        bucket.consume()
        assert bucket.available < 5.0

    def test_refill_over_capacity(self) -> None:
        bucket = _TokenBucket(capacity=5.0, refill_rate=100.0)
        bucket.consume(5.0)  # drain all
        # Simulate time passing by manipulating _last_refill
        bucket._last_refill = time.monotonic() - 1.0  # pretend 1s passed
        bucket.consume(0)  # trigger refill without consuming
        assert bucket.available >= 5.0  # capacity capped

    def test_partial_consume(self) -> None:
        bucket = _TokenBucket(capacity=10.0, refill_rate=1.0)
        assert bucket.consume(3.0) is True
        assert bucket.available < 10.0


# ---------------------------------------------------------------------------
# RateLimitMiddleware
# ---------------------------------------------------------------------------

class TestRateLimitMiddleware:
    async def test_unknown_provider_passes_through(self) -> None:
        mw = RateLimitMiddleware(rpm_limits={"groq": 60})
        fn = _fast_fn("unknown_provider")
        result = await mw(fn, provider="unknown_provider")
        assert result.provider == "unknown_provider"

    async def test_available_tokens_unknown_provider_returns_inf(self) -> None:
        mw = RateLimitMiddleware(rpm_limits={})
        assert mw.available_tokens("nonexistent") == float("inf")

    async def test_available_tokens_known_provider(self) -> None:
        mw = RateLimitMiddleware(rpm_limits={"groq": 60})
        assert mw.available_tokens("groq") > 0

    async def test_max_wait_exceeded_raises(self) -> None:
        # Create a rate limiter with a near-empty bucket and tiny max_wait
        mw = RateLimitMiddleware(rpm_limits={"test": 1}, max_wait_s=0.05)
        # Drain the bucket fully
        bucket = mw._buckets["test"]
        bucket.consume(bucket.capacity)

        fn = _fast_fn("test")
        with pytest.raises(RuntimeError, match="Rate limit"):
            await mw(fn, provider="test")

    async def test_known_provider_with_available_tokens_passes(self) -> None:
        mw = RateLimitMiddleware(rpm_limits={"groq": 60})
        fn = AsyncMock(return_value=_make_response("groq"))
        result = await mw(fn, provider="groq")
        assert result is not None


# ---------------------------------------------------------------------------
# CostGateMiddleware
# ---------------------------------------------------------------------------

class TestCostGateMiddleware:
    async def test_no_budget_always_passes(self) -> None:
        mw = CostGateMiddleware(daily_budget_usd=0.0)
        fn = AsyncMock(return_value=_make_response("groq", 1000, 500))
        # Call many times — should never gate
        for _ in range(5):
            await mw(fn, provider="groq")
        assert mw.daily_spend_usd >= 0.0

    async def test_free_provider_bypasses_gate_even_when_budget_exceeded(self) -> None:
        mw = CostGateMiddleware(
            daily_budget_usd=0.001,
            provider_costs={"ollama": (0.0, 0.0), "groq": (3.0, 6.0)},
        )
        # Exhaust budget
        mw._spent = 1.0

        # Ollama (free) should pass even with exhausted budget
        fn = AsyncMock(return_value=_make_response("ollama"))
        await mw(fn, provider="ollama")  # should not raise

    async def test_paid_provider_blocked_when_budget_exhausted(self) -> None:
        mw = CostGateMiddleware(
            daily_budget_usd=0.001,
            provider_costs={"groq": (3.0, 6.0)},
        )
        mw._spent = 1.0  # already exhausted

        fn = AsyncMock(return_value=_make_response("groq"))
        with pytest.raises(RuntimeError, match="budget.*exhausted"):
            await mw(fn, provider="groq")

    async def test_daily_spend_increases_after_call(self) -> None:
        mw = CostGateMiddleware(
            daily_budget_usd=100.0,
            provider_costs={"groq": (3.0, 6.0)},
        )
        before = mw.daily_spend_usd
        fn = AsyncMock(return_value=_make_response("groq", 1_000_000, 1_000_000))
        await mw(fn, provider="groq")
        after = mw.daily_spend_usd
        assert after > before

    def test_daily_reset_clears_spend(self) -> None:
        mw = CostGateMiddleware(daily_budget_usd=1.0)
        mw._spent = 0.5
        # Force reset by making reset_at appear to be >24h ago
        mw._reset_at = time.monotonic() - 90_000.0
        mw._maybe_reset()
        assert mw._spent == 0.0


# ---------------------------------------------------------------------------
# RequestLogMiddleware
# ---------------------------------------------------------------------------

class TestRequestLogMiddleware:
    async def test_successful_call_logged(self) -> None:
        mw = RequestLogMiddleware()
        fn = _fast_fn()
        result = await mw(fn)
        assert result.provider == "ollama"
        calls = mw.recent_calls(10)
        assert len(calls) == 1
        assert calls[0]["status"] == "ok"

    async def test_failed_call_reraises(self) -> None:
        mw = RequestLogMiddleware()

        async def boom() -> LLMResponse:
            raise ValueError("network error")

        with pytest.raises(ValueError, match="network error"):
            await mw(boom)

    async def test_recent_calls_limits_output(self) -> None:
        mw = RequestLogMiddleware()
        fn = _fast_fn()
        for _ in range(5):
            await mw(fn)
        assert len(mw.recent_calls(3)) == 3
        assert len(mw.recent_calls(10)) == 5

    async def test_call_history_bounded(self) -> None:
        mw = RequestLogMiddleware()
        # maxlen=1000 on the deque
        assert isinstance(mw._call_history, deque)
        assert mw._call_history.maxlen == 1000

    async def test_log_level_debug(self) -> None:
        mw = RequestLogMiddleware(log_level="debug")
        fn = _fast_fn()
        result = await mw(fn)
        assert result is not None
        calls = mw.recent_calls()
        assert calls[0]["provider"] == "ollama"
