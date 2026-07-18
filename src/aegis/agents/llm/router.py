"""
Multi-provider LLM router.

Tries providers in declared order; trips a per-provider circuit
breaker after `failure_threshold` consecutive recoverable failures;
returns `None` if every provider is exhausted (so callers can fall
back to heuristics).

Design notes
------------
* `LLMConfigError` permanently removes a provider from rotation.
* `LLMProviderError` increments the breaker; once tripped, that
  provider is skipped until `cooldown_s` has elapsed.
* Per-call timeout is *separate* from breaker logic — we don't
  want a slow Ollama to delay the fall-through to Groq forever.
* The router is constructed lazily so missing-key cases don't
  blow up at import time. Callers do `await get_default_router()`.
* Token + latency are recorded in Prometheus via the `core.metrics`
  module. Costs are recorded as $0 for free providers but the
  metric is still incremented so any future paid migration is
  immediately visible.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

import structlog

from .providers.base import (
    LLMConfigError,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
)

_log = structlog.get_logger("aegis.agents.llm.router")


@dataclass
class _BreakerState:
    """Per-provider circuit breaker state."""

    consecutive_failures: int = 0
    opened_at: float | None = None  # monotonic seconds
    dead: bool = False  # config-error → permanent
    last_error: str = ""


@dataclass
class RouterConfig:
    """Tunable thresholds. Defaults are conservative for laptop dev."""

    failure_threshold: int = 3
    cooldown_s: float = 60.0
    per_call_timeout_s: float = 30.0


@dataclass
class _CallStats:
    total_calls: int = 0
    total_failures: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)


class LLMRouter:
    """Try providers in order; circuit-break failures; return `None`
    when everything is exhausted so callers can heuristic-fall-back.
    """

    def __init__(
        self,
        providers: list[LLMProvider],
        *,
        config: RouterConfig | None = None,
    ) -> None:
        self._providers = list(providers)
        self._config = config or RouterConfig()
        self._breakers: dict[str, _BreakerState] = {
            p.name: _BreakerState() for p in self._providers
        }
        self._stats = _CallStats()
        # Async-safe lock for breaker state mutations under parallel calls.
        self._lock = asyncio.Lock()

    @property
    def providers(self) -> list[str]:
        return [p.name for p in self._providers]

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
        stop: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> LLMResponse | None:
        """Best-effort completion. Returns None if no provider succeeds."""
        if not self._providers:
            return None
        timeout = timeout_s if timeout_s is not None else self._config.per_call_timeout_s
        self._stats.total_calls += 1

        for provider in self._providers:
            if not await self._is_available(provider.name):
                continue
            try:
                resp = await asyncio.wait_for(
                    provider.complete(
                        system=system,
                        user=user,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stop=stop,
                        timeout_s=timeout,
                    ),
                    timeout=timeout + 5.0,  # outer guard ≥ inner timeout
                )
            except TimeoutError:
                await self._record_failure(provider.name, "asyncio timeout")
                _log.warning("llm.timeout", provider=provider.name, timeout_s=timeout)
                continue
            except LLMConfigError as exc:
                await self._mark_dead(provider.name, str(exc))
                _log.error("llm.config_error", provider=provider.name, error=str(exc))
                continue
            except LLMProviderError as exc:
                await self._record_failure(provider.name, str(exc))
                _log.warning("llm.provider_error", provider=provider.name, error=str(exc))
                continue
            except (
                Exception
            ) as exc:  # pragma: no cover — safety net for unknown provider exceptions
                await self._record_failure(provider.name, f"unexpected: {exc!r}")
                _log.exception("llm.unexpected", provider=provider.name)
                continue

            # Success — reset breaker.
            await self._record_success(provider.name)
            self._stats.by_provider[provider.name] = (
                self._stats.by_provider.get(provider.name, 0) + 1
            )
            return resp

        self._stats.total_failures += 1
        _log.warning(
            "llm.all_providers_exhausted",
            providers=self.providers,
            breakers={k: (v.dead, v.consecutive_failures) for k, v in self._breakers.items()},
        )
        return None

    async def health(self) -> dict[str, bool]:
        """Probe each provider's cheap health endpoint in parallel."""
        out: dict[str, bool] = {}
        # Skip dead providers entirely.
        live = [p for p in self._providers if not self._breakers[p.name].dead]
        results = await asyncio.gather(*(p.health() for p in live), return_exceptions=True)
        for p, r in zip(live, results, strict=True):
            out[p.name] = bool(r) if not isinstance(r, BaseException) else False
        for p in self._providers:
            if p.name not in out:
                out[p.name] = False
        return out

    async def close(self) -> None:
        for p in self._providers:
            try:
                await p.close()
            except Exception:  # pragma: no cover — provider.close() is third-party
                _log.exception("llm.close_failed", provider=p.name)

    def stats(self) -> dict[str, int | dict[str, int]]:
        return {
            "total_calls": self._stats.total_calls,
            "total_failures": self._stats.total_failures,
            "by_provider": dict(self._stats.by_provider),
        }

    # ------------------------------------------------------------------
    # Internals — breaker bookkeeping
    # ------------------------------------------------------------------

    async def _is_available(self, name: str) -> bool:
        async with self._lock:
            state = self._breakers[name]
            if state.dead:
                return False
            if state.opened_at is None:
                return True
            elapsed = time.monotonic() - state.opened_at
            if elapsed >= self._config.cooldown_s:
                # half-open: allow one trial call
                state.opened_at = None
                state.consecutive_failures = 0
                _log.info("llm.breaker_half_open", provider=name)
                return True
            return False

    async def _record_failure(self, name: str, reason: str) -> None:
        async with self._lock:
            state = self._breakers[name]
            state.consecutive_failures += 1
            state.last_error = reason[:200]
            if state.consecutive_failures >= self._config.failure_threshold:
                state.opened_at = time.monotonic()
                _log.warning(
                    "llm.breaker_open",
                    provider=name,
                    consecutive_failures=state.consecutive_failures,
                    cooldown_s=self._config.cooldown_s,
                )

    async def _record_success(self, name: str) -> None:
        async with self._lock:
            state = self._breakers[name]
            state.consecutive_failures = 0
            state.opened_at = None
            state.last_error = ""

    async def _mark_dead(self, name: str, reason: str) -> None:
        async with self._lock:
            state = self._breakers[name]
            state.dead = True
            state.last_error = reason[:200]


# ----------------------------------------------------------------------
# Convenience factory
# ----------------------------------------------------------------------


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_router_from_env() -> LLMRouter:
    """Construct a router from environment variables.

    The router never raises if a provider is misconfigured; it just
    skips that provider. This keeps `aegis up` from failing when only
    Ollama is available, which is the normal dev case.
    """
    providers: list[LLMProvider] = []

    # Ollama is enabled by default (it's local & free). Disable only
    # if AEGIS_DISABLE_OLLAMA=1.
    if not _truthy(os.environ.get("AEGIS_DISABLE_OLLAMA")):
        try:
            from .providers.ollama import OllamaProvider

            providers.append(OllamaProvider())
        except LLMConfigError as exc:
            _log.info("llm.ollama_disabled", reason=str(exc))

    # Groq — only if key set.
    if os.environ.get("GROQ_API_KEY"):
        try:
            from .providers.groq import GroqProvider

            providers.append(GroqProvider())
        except LLMConfigError as exc:
            _log.info("llm.groq_disabled", reason=str(exc))

    # OpenRouter — only if key set.
    if os.environ.get("OPENROUTER_API_KEY"):
        try:
            from .providers.openrouter import OpenRouterProvider

            providers.append(OpenRouterProvider())
        except LLMConfigError as exc:
            _log.info("llm.openrouter_disabled", reason=str(exc))

    # Gemini — only if key set.
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        try:
            from .providers.gemini import GeminiProvider

            providers.append(GeminiProvider())
        except LLMConfigError as exc:
            _log.info("llm.gemini_disabled", reason=str(exc))

    if not providers:
        _log.warning(
            "llm.no_providers_available",
            note="agents will use heuristic-only path",
        )

    return LLMRouter(providers=providers)


# Module-level singleton stored in a mutable container so we avoid
# `global` statements (PLW0603). The container itself is never replaced.
class _RouterState:
    router: LLMRouter | None = None
    lock: asyncio.Lock = asyncio.Lock()


_router_state = _RouterState()


async def get_default_router() -> LLMRouter:
    """Return the process-wide router, building it on first call."""
    if _router_state.router is not None:
        return _router_state.router
    async with _router_state.lock:
        if _router_state.router is None:
            _router_state.router = build_router_from_env()
    return _router_state.router  # type: ignore[return-value]


async def reset_default_router() -> None:
    """Test-only helper: tear down and clear the cached router."""
    async with _router_state.lock:
        if _router_state.router is not None:
            await _router_state.router.close()
            _router_state.router = None
