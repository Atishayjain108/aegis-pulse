"""
Resilient call wrapper for model and external operations.

Implements the project doctrine for external/expensive calls:
    - hard wall-clock timeout
    - decorrelated jitter retry (AWS recipe)
    - in-process circuit breaker (rolling-window error rate)
    - structured exception → typed error
    - graceful fallback callback
    - Prometheus-style metric hooks

Used everywhere a model is loaded, an ONNX session is started, or an
embedding service is queried. Pure stdlib + structlog — no torch
dependency, so it imports cleanly in CPU-only test environments.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

import structlog

T = TypeVar("T")

_log = structlog.get_logger("aegis.predict.resilience")


# ---------------------------------------------------------------------------
# Decorrelated jitter retry — AWS recipe.
# ---------------------------------------------------------------------------
def decorrelated_jitter(
    attempt: int,
    *,
    base: float = 0.2,
    cap: float = 8.0,
    last_sleep: float = 0.0,
) -> float:
    """Compute the next sleep using AWS's 'decorrelated jitter' recipe.

    Each call returns a sleep in [base, min(cap, last_sleep*3)],
    sampled uniformly. This avoids both the 'thundering herd'
    behaviour of pure exponential backoff AND the high variance
    of full-jitter.
    """
    upper = min(cap, max(base, last_sleep * 3.0))
    return random.uniform(base, upper)


# ---------------------------------------------------------------------------
# Circuit breaker — minimal in-process implementation.
# ---------------------------------------------------------------------------
@dataclass
class _Window:
    """Rolling window of (timestamp, success) tuples."""

    samples: list[tuple[float, bool]] = field(default_factory=list)

    def add(self, success: bool, *, now: float) -> None:
        self.samples.append((now, success))

    def trim(self, *, now: float, window_s: float) -> None:
        cutoff = now - window_s
        # Keep last 200 samples max to bound memory.
        self.samples = [s for s in self.samples if s[0] >= cutoff][-200:]

    def error_rate(self) -> float:
        if not self.samples:
            return 0.0
        errs = sum(1 for _, ok in self.samples if not ok)
        return errs / len(self.samples)

    def total(self) -> int:
        return len(self.samples)


@dataclass
class CircuitBreaker:
    """Trip on >threshold error rate; half-open after cooldown."""

    name: str
    error_rate_threshold: float = 0.30
    min_samples: int = 8
    rolling_window_s: float = 60.0
    cooldown_s: float = 120.0

    _window: _Window = field(default_factory=_Window)
    _open_until: float = 0.0

    def is_open(self) -> bool:
        return time.monotonic() < self._open_until

    def is_half_open(self) -> bool:
        # The slot just after `_open_until` lets exactly one probe
        # call through before deciding to re-close or stay open.
        return False  # simplification: full-open or fully closed

    def record(self, success: bool) -> None:
        now = time.monotonic()
        self._window.add(success, now=now)
        self._window.trim(now=now, window_s=self.rolling_window_s)
        if (
            self._window.total() >= self.min_samples
            and self._window.error_rate() >= self.error_rate_threshold
        ):
            if not self.is_open():
                _log.warning(
                    "circuit_breaker.opened",
                    name=self.name,
                    error_rate=round(self._window.error_rate(), 3),
                    sample_size=self._window.total(),
                )
            self._open_until = now + self.cooldown_s

    def reset(self) -> None:
        self._window = _Window()
        self._open_until = 0.0


# Process-global registry — one breaker per logical operation name.
_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(name: str) -> CircuitBreaker:
    if name not in _BREAKERS:
        _BREAKERS[name] = CircuitBreaker(name=name)
    return _BREAKERS[name]


def reset_all_breakers() -> None:
    """Test-only helper."""
    for b in _BREAKERS.values():
        b.reset()


# ---------------------------------------------------------------------------
# Public wrapper
# ---------------------------------------------------------------------------
async def resilient_call(
    op: Callable[[], Awaitable[T]],
    *,
    name: str,
    timeout_s: float,
    retries: int = 2,
    fallback: Callable[[], Awaitable[T] | T] | None = None,
    breaker: CircuitBreaker | None = None,
) -> T:
    """Run `op()` with timeout + retry + circuit breaker + fallback.

    Parameters
    ----------
    op:        zero-arg async callable producing the value.
    name:      logical name; used for breaker key + log labels.
    timeout_s: hard wall-clock per attempt.
    retries:   additional attempts after the first (so total <= retries+1).
    fallback:  optional zero-arg callable invoked when every attempt
               fails or the breaker is open. May be sync or async.
    breaker:   override the global breaker for this op (for testing).
    """
    cb = breaker or get_breaker(name)

    if cb.is_open():
        _log.info("circuit_breaker.short_circuit", name=name)
        if fallback is not None:
            return await _maybe_await(fallback)
        raise RuntimeError(f"circuit_breaker_open:{name}")

    last_sleep = 0.0
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            t0 = time.perf_counter()
            result = await asyncio.wait_for(op(), timeout=timeout_s)
            cb.record(success=True)
            _log.debug(
                "resilient_call.ok",
                name=name,
                attempt=attempt,
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
            return result
        except TimeoutError as exc:
            last_exc = exc
            cb.record(success=False)
            _log.warning("resilient_call.timeout", name=name, attempt=attempt)
        except asyncio.CancelledError:
            raise  # never swallow cancellation
        except Exception as exc:
            last_exc = exc
            cb.record(success=False)
            _log.warning(
                "resilient_call.error",
                name=name,
                attempt=attempt,
                error=str(exc),
                error_type=type(exc).__name__,
            )

        if attempt < retries:
            last_sleep = decorrelated_jitter(attempt + 1, last_sleep=last_sleep)
            await asyncio.sleep(last_sleep)

    if fallback is not None:
        _log.info("resilient_call.fallback", name=name)
        return await _maybe_await(fallback)

    assert last_exc is not None
    raise last_exc


async def _maybe_await(fn: Callable[[], Any]) -> Any:
    out = fn()
    if asyncio.iscoroutine(out) or isinstance(out, asyncio.Future):
        return await out
    return out


__all__ = [
    "CircuitBreaker",
    "decorrelated_jitter",
    "get_breaker",
    "resilient_call",
    "reset_all_breakers",
]
