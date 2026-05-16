"""Reliability primitives — ``resilient_call`` decorator + supporting pieces.

Per the project charter, **every external call** must have:

1. Timeout
2. Retry with decorrelated jitter
3. Circuit breaker
4. Structured error log
5. Graceful degradation (fallback)
6. Metric emission

Rather than scatter that ceremony across every call site, this module provides
one decorator, ``@resilient_call``, that composes all six concerns. The
behaviour is tunable per-call-site via a ``ResiliencePolicy`` dataclass.

Design rationale:

- **Decorator, not context manager**: call sites are already `await`-ed
  coroutines; wrapping them in a decorator keeps the read path clean.
- **Per-key circuit breakers**: breaker state is keyed by a tag string
  (e.g. ``"reddit.api"``), so hosting problems with one source do not trip
  breakers for unrelated ones.
- **Typed errors**: the decorator converts arbitrary exceptions into a small
  enum of ``ResilientError`` subclasses so callers can branch on category.
- **Pluggable fallback**: the optional ``fallback`` callable is awaited on
  final failure; its return value is surfaced as the call's result.
- **Metric hooks**: delegated to ``core.metrics`` (added in the next file).
  If metrics aren't wired up yet, emissions are no-ops — no hard dependency
  on a global registry at import time.

Usage::

    from aegis.core.resilience import resilient_call, ResiliencePolicy


    @resilient_call(
        ResiliencePolicy(
            name="reddit.api",
            timeout=10.0,
            max_attempts=3,
            circuit_open_after=5,
        )
    )
    async def fetch_subreddit(name: str) -> list[dict]:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"https://reddit.com/r/{name}.json")
            r.raise_for_status()
            return r.json()["data"]["children"]

Author: AEGIS Pulse Team
Relationship: depended on by every scraper adapter + every external DB/cache op.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import StrEnum, unique
from functools import wraps
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast

import structlog

from aegis.constants import (
    CIRCUIT_BREAKER_ERROR_RATE_THRESHOLD,
    CIRCUIT_BREAKER_ERROR_RATE_WINDOW_SECONDS,
    CIRCUIT_BREAKER_FAIL_THRESHOLD,
    CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS,
    HTTP_TOTAL_TIMEOUT_SECONDS,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_ATTEMPTS_DEFAULT,
    RETRY_MAX_DELAY_SECONDS,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# =============================================================================
# Type vars (PEP 695 generic syntax, Python 3.12)
# =============================================================================

P = ParamSpec("P")
R = TypeVar("R")


# =============================================================================
# Error taxonomy
# =============================================================================


@unique
class ErrorCategory(StrEnum):
    """Categorisation of a failed call — used for metric labels + log fields."""

    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    FALLBACK_SUCCEEDED = "fallback_succeeded"
    UNKNOWN = "unknown"


class ResilientError(Exception):
    """Base class for errors raised by ``resilient_call``.

    Carries the original exception (``__cause__``), the category, and the
    policy name so downstream structured logs always have full context.
    """

    def __init__(
        self,
        *,
        message: str,
        category: ErrorCategory,
        policy_name: str,
    ) -> None:
        super().__init__(message)
        self.category: ErrorCategory = category
        self.policy_name: str = policy_name


class TimeoutError_(ResilientError):
    """Raised when a single attempt exceeds the policy's timeout."""


class CircuitOpenError(ResilientError):
    """Raised immediately when the circuit breaker for this policy is open."""


class ExhaustedError(ResilientError):
    """Raised when all retries are exhausted and no fallback was provided."""


# =============================================================================
# Circuit breaker
# =============================================================================


@dataclass
class _BreakerState:
    """Mutable state for one logical circuit breaker.

    Not thread-safe by design — we run under a single asyncio event loop per
    process. For multi-process coordination, use Redis-backed counters
    (out of scope for Phase 1).
    """

    # Consecutive failures since the last success.
    consecutive_failures: int = 0
    # Timestamps of failures within the sliding window (monotonic seconds).
    recent_failures: list[float] = field(default_factory=list)
    recent_attempts: list[float] = field(default_factory=list)
    # Time at which the breaker entered OPEN state; 0 means closed.
    opened_at: float = 0.0
    # Half-open probe in flight?
    half_open_probe: bool = False


class CircuitBreaker:
    """Trips on either consecutive failures OR sliding-window error rate.

    Three states:

    - **CLOSED**: calls pass through normally.
    - **OPEN**: calls raise ``CircuitOpenError`` immediately.
    - **HALF_OPEN**: a single probe call is allowed; success closes the
      breaker, failure re-opens it.
    """

    def __init__(
        self,
        *,
        name: str,
        fail_threshold: int = CIRCUIT_BREAKER_FAIL_THRESHOLD,
        reset_timeout: float = CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS,
        error_rate_window: float = CIRCUIT_BREAKER_ERROR_RATE_WINDOW_SECONDS,
        error_rate_threshold: float = CIRCUIT_BREAKER_ERROR_RATE_THRESHOLD,
    ) -> None:
        self.name: str = name
        self._fail_threshold = fail_threshold
        self._reset_timeout = reset_timeout
        self._window = error_rate_window
        self._rate_threshold = error_rate_threshold
        self._state = _BreakerState()
        self._log = structlog.get_logger(__name__).bind(breaker=name)

    def _prune(self, now: float) -> None:
        """Drop samples older than the sliding window."""
        cutoff = now - self._window
        s = self._state
        s.recent_failures[:] = [t for t in s.recent_failures if t >= cutoff]
        s.recent_attempts[:] = [t for t in s.recent_attempts if t >= cutoff]

    def check(self) -> None:
        """Call before attempting a protected operation.

        Raises:
            CircuitOpenError: if the breaker is OPEN and cooldown has not
                elapsed. During HALF_OPEN, only the first caller proceeds;
                subsequent callers are rejected with the same error until
                the probe completes.
        """
        now = time.monotonic()
        s = self._state

        if s.opened_at == 0.0:
            # CLOSED — always allow
            return

        elapsed = now - s.opened_at
        if elapsed < self._reset_timeout:
            # OPEN
            raise CircuitOpenError(
                message=(
                    f"circuit '{self.name}' open "
                    f"(opens for {self._reset_timeout - elapsed:.0f}s more)"
                ),
                category=ErrorCategory.CIRCUIT_OPEN,
                policy_name=self.name,
            )

        # Cooldown elapsed — HALF_OPEN. Only one probe allowed.
        if s.half_open_probe:
            raise CircuitOpenError(
                message=f"circuit '{self.name}' half-open (probe in flight)",
                category=ErrorCategory.CIRCUIT_OPEN,
                policy_name=self.name,
            )
        s.half_open_probe = True
        self._log.info("circuit.half_open")

    def record_success(self) -> None:
        """Call after a successful attempt."""
        now = time.monotonic()
        s = self._state
        s.recent_attempts.append(now)
        s.consecutive_failures = 0
        if s.opened_at != 0.0:
            # Recovering from OPEN via half-open probe.
            self._log.info("circuit.closed", was_open_for_seconds=now - s.opened_at)
            s.opened_at = 0.0
            s.half_open_probe = False

    def record_failure(self) -> None:
        """Call after a failed attempt. May trip the breaker."""
        now = time.monotonic()
        s = self._state
        s.recent_attempts.append(now)
        s.recent_failures.append(now)
        s.consecutive_failures += 1
        s.half_open_probe = False
        self._prune(now)

        trip_consecutive = s.consecutive_failures >= self._fail_threshold
        attempts = len(s.recent_attempts)
        rate = (len(s.recent_failures) / attempts) if attempts > 0 else 0.0
        trip_rate = attempts >= 4 and rate >= self._rate_threshold

        if (trip_consecutive or trip_rate) and s.opened_at == 0.0:
            s.opened_at = now
            self._log.warning(
                "circuit.opened",
                consecutive=s.consecutive_failures,
                rate=round(rate, 3),
                window_attempts=attempts,
                reason="consecutive" if trip_consecutive else "error_rate",
            )


# Process-global breaker registry, keyed by policy name. Lazy-initialised.
_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    *,
    fail_threshold: int = CIRCUIT_BREAKER_FAIL_THRESHOLD,
    reset_timeout: float = CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS,
    error_rate_window: float = CIRCUIT_BREAKER_ERROR_RATE_WINDOW_SECONDS,
    error_rate_threshold: float = CIRCUIT_BREAKER_ERROR_RATE_THRESHOLD,
) -> CircuitBreaker:
    """Return the (possibly new) breaker for ``name``.

    All params other than ``name`` are applied only when the breaker is first
    created; subsequent calls with the same name return the existing instance
    unchanged. This keeps breaker config stable across call sites even if the
    defaults drift.
    """
    b = _BREAKERS.get(name)
    if b is None:
        b = CircuitBreaker(
            name=name,
            fail_threshold=fail_threshold,
            reset_timeout=reset_timeout,
            error_rate_window=error_rate_window,
            error_rate_threshold=error_rate_threshold,
        )
        _BREAKERS[name] = b
    return b


def reset_all_breakers() -> None:
    """Reset every breaker in the registry. Intended for tests."""
    _BREAKERS.clear()


# =============================================================================
# Policy & decorator
# =============================================================================


@dataclass(frozen=True, slots=True)
class ResiliencePolicy:
    """Declarative configuration for ``resilient_call``.

    Frozen + slotted because policies are expected to be declared as module-
    level constants and reused; no reason to pay for dict + mutability.
    """

    name: str
    """Identity for metrics + breaker registry. Convention: ``"<domain>.<op>"``."""

    timeout: float = HTTP_TOTAL_TIMEOUT_SECONDS
    """Per-attempt timeout in seconds. Not the total budget across retries."""

    max_attempts: int = RETRY_MAX_ATTEMPTS_DEFAULT
    """Total attempts including the first one. 1 = no retry."""

    base_delay: float = RETRY_BASE_DELAY_SECONDS
    max_delay: float = RETRY_MAX_DELAY_SECONDS

    retry_on: tuple[type[BaseException], ...] = (Exception,)
    """Exception types that should trigger a retry. Everything else bubbles."""

    do_not_retry_on: tuple[type[BaseException], ...] = ()
    """Types that MUST NOT be retried (takes precedence over ``retry_on``).
    Useful for auth errors (401/403), validation errors, etc."""

    circuit_open_after: int = CIRCUIT_BREAKER_FAIL_THRESHOLD
    circuit_reset_timeout: float = CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.timeout <= 0:
            raise ValueError("timeout must be > 0")
        if self.base_delay <= 0 or self.max_delay < self.base_delay:
            raise ValueError("invalid delay bounds")


def _decorrelated_jitter(prev_delay: float, base: float, cap: float) -> float:
    """AWS 'decorrelated jitter' backoff.

    https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
    Each sleep is uniform random over ``[base, min(cap, prev*3)]``. Tends to
    spread load better than pure exponential under correlated failures.
    """
    upper = min(cap, max(base, prev_delay * 3.0))
    return random.uniform(base, upper)  # not cryptographic


def resilient_call(
    policy: ResiliencePolicy,
    *,
    fallback: Callable[P, Awaitable[R]] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Wrap an async callable with timeout + retry + breaker + fallback + metrics.

    Args:
        policy: configuration; usually a module-level constant.
        fallback: optional async callable invoked **once** after all retries
            are exhausted. Receives the same ``*args, **kwargs`` as the wrapped
            callable. Its return value is returned from the decorated function.
            If ``None``, exhaustion raises ``ExhaustedError``.

    Returns:
        A decorator.

    The decorator preserves the wrapped function's signature via
    ``functools.wraps`` and is fully type-preserving on Python 3.12.
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        breaker = get_breaker(
            policy.name,
            fail_threshold=policy.circuit_open_after,
            reset_timeout=policy.circuit_reset_timeout,
        )
        log = structlog.get_logger(__name__).bind(resilient=policy.name)

        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Import lazily to avoid a hard dependency on the metrics module
            # at import time (metrics registry lives in core/metrics.py).
            # Typed as Optional so mypy accepts the None fallback.
            _observe: Callable[..., None] | None
            try:
                from aegis.core.metrics import resilient_call_observe as _observe_imported

                _observe = _observe_imported
            except ImportError:
                _observe = None

            last_exc: BaseException | None = None
            delay = policy.base_delay

            for attempt in range(1, policy.max_attempts + 1):
                # Pre-flight breaker check.
                try:
                    breaker.check()
                except CircuitOpenError:
                    if _observe is not None:
                        _observe(
                            policy=policy.name,
                            outcome=ErrorCategory.CIRCUIT_OPEN.value,
                            attempt=attempt,
                        )
                    # Breaker-open: skip straight to fallback if any.
                    if fallback is not None:
                        log.info("fallback.after_circuit_open")
                        return await fallback(*args, **kwargs)
                    raise

                start = time.monotonic()
                try:
                    result = await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=policy.timeout,
                    )
                except TimeoutError as exc:
                    breaker.record_failure()
                    last_exc = exc
                    if _observe is not None:
                        _observe(
                            policy=policy.name,
                            outcome=ErrorCategory.TIMEOUT.value,
                            attempt=attempt,
                            duration=time.monotonic() - start,
                        )
                    log.warning(
                        "attempt.timeout",
                        attempt=attempt,
                        timeout_seconds=policy.timeout,
                    )
                    # Fall through to retry logic.
                except policy.do_not_retry_on as exc:
                    breaker.record_failure()
                    if _observe is not None:
                        _observe(
                            policy=policy.name,
                            outcome=ErrorCategory.NON_RETRYABLE.value,
                            attempt=attempt,
                            duration=time.monotonic() - start,
                        )
                    log.error(
                        "attempt.non_retryable",
                        attempt=attempt,
                        exc_type=type(exc).__name__,
                        exc_message=str(exc)[:200],
                    )
                    raise  # never retry on do_not_retry_on
                except policy.retry_on as exc:
                    breaker.record_failure()
                    last_exc = exc
                    if _observe is not None:
                        _observe(
                            policy=policy.name,
                            outcome=ErrorCategory.RETRYABLE.value,
                            attempt=attempt,
                            duration=time.monotonic() - start,
                        )
                    log.warning(
                        "attempt.retryable",
                        attempt=attempt,
                        exc_type=type(exc).__name__,
                        exc_message=str(exc)[:200],
                    )
                else:
                    breaker.record_success()
                    if _observe is not None:
                        _observe(
                            policy=policy.name,
                            outcome="success",
                            attempt=attempt,
                            duration=time.monotonic() - start,
                        )
                    return result

                # Exhausted?
                if attempt >= policy.max_attempts:
                    break

                delay = _decorrelated_jitter(delay, policy.base_delay, policy.max_delay)
                log.info(
                    "attempt.retry_sleep", next_attempt=attempt + 1, sleep_seconds=round(delay, 2)
                )
                await asyncio.sleep(delay)

            # All attempts exhausted.
            if fallback is not None:
                if _observe is not None:
                    _observe(
                        policy=policy.name,
                        outcome=ErrorCategory.FALLBACK_SUCCEEDED.value,
                        attempt=policy.max_attempts,
                    )
                log.warning(
                    "resilient.fallback_invoked",
                    after_attempts=policy.max_attempts,
                    last_exc=type(last_exc).__name__ if last_exc else None,
                )
                return await fallback(*args, **kwargs)

            raise ExhaustedError(
                message=(
                    f"{policy.name!r} exhausted after {policy.max_attempts} attempts "
                    f"(last error: {type(last_exc).__name__ if last_exc else 'unknown'})"
                ),
                category=ErrorCategory.RETRYABLE
                if isinstance(last_exc, policy.retry_on)
                else ErrorCategory.TIMEOUT
                if isinstance(last_exc, asyncio.TimeoutError)
                else ErrorCategory.UNKNOWN,
                policy_name=policy.name,
            ) from last_exc

        # Mypy can't automatically see that wraps() preserves the signature
        # across our ParamSpec; explicit cast keeps callers type-safe.
        return cast("Callable[P, Awaitable[R]]", wrapper)

    return decorator


__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "ErrorCategory",
    "ExhaustedError",
    "ResilienceError",  # alias below
    "ResiliencePolicy",
    "ResilientError",
    "TimeoutError_",
    "get_breaker",
    "reset_all_breakers",
    "resilient_call",
]

# Alias because "ResilientError" reads better in exception chains but
# "ResilienceError" is what people type by instinct.
ResilienceError = ResilientError
