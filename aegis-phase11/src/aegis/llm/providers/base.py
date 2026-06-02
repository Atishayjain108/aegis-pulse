"""
aegis.llm.providers.base — abstract provider interface
=======================================================

Every provider adapter inherits from ``BaseProvider`` and implements
the ``complete()`` coroutine.  The gateway never touches provider-specific
classes directly — it works through this interface.

Author: AEGIS Engineering
"""

from __future__ import annotations

import abc
import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from aegis.llm.constants import (
    CIRCUIT_BREAKER_RESET_S,
    CIRCUIT_BREAKER_THRESHOLD,
    CIRCUIT_BREAKER_WINDOW_S,
    LLM_HARD_TIMEOUT_S,
    MAX_RETRY_ATTEMPTS,
    RETRY_BASE_S,
    RETRY_MAX_S,
)
from aegis.llm.errors import CircuitOpen, ProviderTimeout
from aegis.llm.gateway.response import LLMResponse, TokenUsage

_log = structlog.get_logger("aegis.llm.providers")


# ---------------------------------------------------------------------------
# Circuit breaker state
# ---------------------------------------------------------------------------


@dataclass
class _CircuitState:
    """Rolling-window circuit breaker state (not thread-safe — single event loop)."""

    window_s: float = CIRCUIT_BREAKER_WINDOW_S
    threshold: float = CIRCUIT_BREAKER_THRESHOLD
    reset_s: float = CIRCUIT_BREAKER_RESET_S

    _timestamps: list[float] = field(default_factory=list)
    _errors: list[float] = field(default_factory=list)
    _tripped_at: float | None = None

    def record_success(self) -> None:
        now = time.monotonic()
        self._timestamps.append(now)
        self._prune(now)

    def record_failure(self) -> None:
        now = time.monotonic()
        self._timestamps.append(now)
        self._errors.append(now)
        self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        self._errors = [t for t in self._errors if t > cutoff]

    @property
    def is_open(self) -> bool:
        """``True`` when the breaker is tripped and reset time has not elapsed."""
        if self._tripped_at is None:
            return False
        if time.monotonic() - self._tripped_at > self.reset_s:
            # Half-open: allow a single probe
            self._tripped_at = None
            return False
        return True

    def evaluate(self, provider: str) -> None:
        """Trip the breaker if error rate exceeds threshold."""
        if not self._timestamps:
            return
        rate = len(self._errors) / len(self._timestamps)
        if rate > self.threshold:
            self._tripped_at = time.monotonic()
            _log.warning(
                "circuit_breaker.tripped",
                provider=provider,
                error_rate=round(rate, 3),
                window_s=self.window_s,
            )


# ---------------------------------------------------------------------------
# Decorrelated jitter retry helper
# ---------------------------------------------------------------------------


async def _jitter_sleep(attempt: int) -> None:
    """Decorrelated jitter sleep (AWS recipe)."""
    cap = RETRY_MAX_S
    base = RETRY_BASE_S
    sleep = min(cap, random.uniform(base, base * (2**attempt)))  # noqa: S311
    await asyncio.sleep(sleep)


# ---------------------------------------------------------------------------
# Abstract base provider
# ---------------------------------------------------------------------------


class BaseProvider(abc.ABC):
    """
    Abstract base for every LLM provider adapter.

    Subclasses must implement:
    - ``name`` property
    - ``_raw_complete()`` coroutine
    - ``health_check()`` coroutine
    - ``context_limit`` property

    The base class provides: circuit breaker, retry with jitter,
    hard timeout enforcement, structured logging, and metric hooks.
    """

    def __init__(self) -> None:
        self._circuit = _CircuitState()

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short provider identifier (e.g. ``"ollama"``)."""

    @property
    @abc.abstractmethod
    def context_limit(self) -> int:
        """Maximum context window in tokens for the currently configured model."""

    @abc.abstractmethod
    async def _raw_complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Send ``messages`` to the provider and return a raw ``LLMResponse``.

        This method is called by ``complete()`` inside the retry/timeout harness.
        Raise any exception on failure; the harness will handle retry/circuit-break.
        """

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Return ``True`` if the provider is reachable and healthy."""

    # ------------------------------------------------------------------
    # Public entrypoint (with retry + circuit breaker + timeout)
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout_s: float = LLM_HARD_TIMEOUT_S,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Call the provider with full resilience harness.

        Raises
        ------
        CircuitOpen
            When the circuit breaker is tripped.
        ProviderTimeout
            When the call exceeds ``timeout_s``.
        Exception
            Any other provider error after exhausting retries.
        """
        if self._circuit.is_open:
            raise CircuitOpen(
                f"Circuit breaker open for {self.name}",
                provider=self.name,
            )

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                response = await asyncio.wait_for(
                    self._raw_complete(
                        messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    ),
                    timeout=timeout_s,
                )
                self._circuit.record_success()
                _log.debug(
                    "llm.complete.success",
                    provider=self.name,
                    attempt=attempt,
                    latency_ms=round(response.latency_ms, 1),
                )
                return response

            except TimeoutError:
                self._circuit.record_failure()
                self._circuit.evaluate(self.name)
                _log.warning(
                    "llm.complete.timeout",
                    provider=self.name,
                    attempt=attempt,
                    timeout_s=timeout_s,
                )
                last_exc = ProviderTimeout(
                    f"Timed out after {timeout_s}s",
                    provider=self.name,
                )

            except Exception as exc:  # noqa: BLE001
                self._circuit.record_failure()
                self._circuit.evaluate(self.name)
                _log.warning(
                    "llm.complete.error",
                    provider=self.name,
                    attempt=attempt,
                    error=str(exc),
                    exc_info=True,
                )
                last_exc = exc

            if attempt < MAX_RETRY_ATTEMPTS - 1:
                await _jitter_sleep(attempt)

        raise last_exc or RuntimeError(f"Provider {self.name} failed silently")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_usage(
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> TokenUsage:
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
