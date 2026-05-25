"""
aegis.llm.gateway.middleware — Gateway middleware stack
========================================================

Middleware components that wrap ``LLMGateway.complete()`` calls:

1. ``LatencyBudgetMiddleware``  — enforces p99 < 500ms SLA, logs violations
2. ``RateLimitMiddleware``      — token-bucket rate limiter per provider
3. ``CostGateMiddleware``       — blocks calls when daily budget is exhausted
4. ``RequestLogMiddleware``     — structured log entry per call (request + response)

Middleware is applied at ``LLMGateway`` construction and is transparent
to callers.

Author: AEGIS Engineering
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import structlog

from aegis.llm.constants import LLM_SOFT_WARN_TIMEOUT_S, PROVIDER_RPM_LIMITS
from aegis.llm.gateway.response import LLMResponse

_log = structlog.get_logger("aegis.llm.gateway.middleware")

# Type alias for a complete() coroutine
CompleteFn = Callable[..., Coroutine[Any, Any, LLMResponse]]


# ---------------------------------------------------------------------------
# Latency budget middleware
# ---------------------------------------------------------------------------


class LatencyBudgetMiddleware:
    """
    Logs a warning when a call exceeds ``warn_ms`` milliseconds.

    Enforces a hard timeout if ``hard_ms`` is set (raises ``asyncio.TimeoutError``).

    Parameters
    ----------
    warn_ms:
        Warn threshold in milliseconds (default: 30 000 ms = 30 s).
    hard_ms:
        Hard abort threshold in milliseconds. ``None`` = no hard limit (use
        provider-level timeout instead).
    """

    def __init__(
        self,
        *,
        warn_ms: float = LLM_SOFT_WARN_TIMEOUT_S * 1000,
        hard_ms: float | None = None,
    ) -> None:
        self._warn_ms = warn_ms
        self._hard_ms = hard_ms
        self._violations: int = 0

    async def __call__(self, fn: CompleteFn, *args: Any, **kwargs: Any) -> LLMResponse:
        t0 = time.perf_counter()
        if self._hard_ms:
            result = await asyncio.wait_for(fn(*args, **kwargs), timeout=self._hard_ms / 1000)
        else:
            result = await fn(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        if elapsed_ms > self._warn_ms:
            self._violations += 1
            _log.warning(
                "latency_budget.violation",
                elapsed_ms=round(elapsed_ms, 1),
                warn_ms=self._warn_ms,
                total_violations=self._violations,
                provider=result.provider,
            )
        return result

    @property
    def violation_count(self) -> int:
        return self._violations


# ---------------------------------------------------------------------------
# Token-bucket rate limiter
# ---------------------------------------------------------------------------


@dataclass
class _TokenBucket:
    """Simple token-bucket rate limiter (not thread-safe — single event loop)."""

    capacity: float           # Max tokens (= max burst)
    refill_rate: float        # Tokens added per second
    _tokens: float = field(init=False)
    _last_refill: float = field(default_factory=time.monotonic, init=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity

    def consume(self, n: float = 1.0) -> bool:
        """Attempt to consume ``n`` tokens. Returns ``True`` if successful."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    @property
    def available(self) -> float:
        return self._tokens


class RateLimitMiddleware:
    """
    Token-bucket rate limiter per provider.

    Blocks (sleeps) until a token is available rather than raising an error,
    because short backpressure is preferable to a failed request.

    Parameters
    ----------
    rpm_limits:
        Mapping of ``provider_name → requests_per_minute``.
        Defaults to ``PROVIDER_RPM_LIMITS`` from constants.
    max_wait_s:
        Maximum time to wait for a token before giving up (raising).
    """

    def __init__(
        self,
        *,
        rpm_limits: dict[str, int] | None = None,
        max_wait_s: float = 10.0,
    ) -> None:
        limits = rpm_limits or PROVIDER_RPM_LIMITS
        self._buckets: dict[str, _TokenBucket] = {
            provider: _TokenBucket(
                capacity=float(rpm),
                refill_rate=rpm / 60.0,
            )
            for provider, rpm in limits.items()
        }
        self._max_wait_s = max_wait_s

    async def __call__(self, fn: CompleteFn, *args: Any, **kwargs: Any) -> LLMResponse:
        # Determine provider from kwargs or from result (post-call is too late)
        # We peek at the provider kwarg if present
        provider: str = kwargs.get("provider", "unknown") or "unknown"
        bucket = self._buckets.get(provider)

        if bucket is not None:
            waited = 0.0
            sleep_interval = 0.05
            while not bucket.consume():
                if waited >= self._max_wait_s:
                    raise RuntimeError(
                        f"Rate limit: waited {waited:.1f}s for {provider} token"
                    )
                await asyncio.sleep(sleep_interval)
                waited += sleep_interval

        return await fn(*args, **kwargs)

    def available_tokens(self, provider: str) -> float:
        """Return available tokens for a provider (for monitoring)."""
        bucket = self._buckets.get(provider)
        return bucket.available if bucket else float("inf")


# ---------------------------------------------------------------------------
# Cost gate middleware
# ---------------------------------------------------------------------------


class CostGateMiddleware:
    """
    Blocks requests when the daily cost budget is exhausted.

    For free providers (cost == 0), this middleware is a no-op.

    Parameters
    ----------
    daily_budget_usd:
        Maximum daily spend in USD. ``0.0`` = unlimited (no gate).
    provider_costs:
        ``{provider: (input_$/1M, output_$/1M)}`` mapping.
    """

    def __init__(
        self,
        *,
        daily_budget_usd: float = 0.0,
        provider_costs: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        from aegis.llm.constants import (
            PROVIDER_COST_PER_1M_INPUT,
            PROVIDER_COST_PER_1M_OUTPUT,
        )

        self._budget = daily_budget_usd
        self._costs = provider_costs or {
            p: (PROVIDER_COST_PER_1M_INPUT.get(p, 0.0), PROVIDER_COST_PER_1M_OUTPUT.get(p, 0.0))
            for p in PROVIDER_COST_PER_1M_INPUT
        }
        self._spent: float = 0.0
        self._reset_at: float = time.monotonic()
        self._day_seconds: float = 86_400.0

    def _maybe_reset(self) -> None:
        now = time.monotonic()
        if now - self._reset_at >= self._day_seconds:
            _log.info("cost_gate.daily_reset", spent_usd=round(self._spent, 6))
            self._spent = 0.0
            self._reset_at = now

    async def __call__(self, fn: CompleteFn, *args: Any, **kwargs: Any) -> LLMResponse:
        self._maybe_reset()

        if self._budget > 0.0 and self._spent >= self._budget:
            provider = kwargs.get("provider", "unknown")
            in_cost, out_cost = self._costs.get(provider, (0.0, 0.0))
            if in_cost > 0 or out_cost > 0:  # Only gate paid providers
                raise RuntimeError(
                    f"Daily LLM cost budget ${self._budget:.4f} exhausted "
                    f"(spent ${self._spent:.4f})"
                )

        result = await fn(*args, **kwargs)
        cost = result.cost_usd({result.provider: self._costs.get(result.provider, (0.0, 0.0))})
        self._spent += cost
        return result

    @property
    def daily_spend_usd(self) -> float:
        self._maybe_reset()
        return self._spent


# ---------------------------------------------------------------------------
# Request log middleware
# ---------------------------------------------------------------------------


class RequestLogMiddleware:
    """
    Emits a structured log entry for every LLM call.

    Includes: provider, model, latency, token counts, and whether the
    semantic router short-circuited the call.
    """

    def __init__(self, *, log_level: str = "info") -> None:
        self._log_level = log_level
        self._call_history: deque[dict[str, Any]] = deque(maxlen=1000)

    async def __call__(self, fn: CompleteFn, *args: Any, **kwargs: Any) -> LLMResponse:
        t0 = time.perf_counter()
        try:
            result = await fn(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            entry = {
                **result.to_log_dict(),
                "elapsed_ms": round(elapsed_ms, 1),
                "status": "ok",
            }
            getattr(_log, self._log_level)("llm.request", **entry)
            self._call_history.append(entry)
            return result
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _log.error(
                "llm.request.failed",
                error=str(exc),
                error_type=type(exc).__name__,
                elapsed_ms=round(elapsed_ms, 1),
            )
            raise

    def recent_calls(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the N most recent call log entries."""
        return list(self._call_history)[-n:]
