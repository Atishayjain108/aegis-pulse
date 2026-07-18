"""Abstract LLM provider interface.

Every concrete provider (Ollama, Groq, OpenRouter, Gemini, ...) returns
an `LLMResponse` from `complete()`. Providers MUST raise
`LLMProviderError` for any recoverable error (timeout, 429, transient
5xx) and `LLMConfigError` for un-recoverable misconfiguration (missing
key, bad URL). The router treats these classes differently:
   - `LLMProviderError` → trip circuit breaker for that provider.
   - `LLMConfigError`   → mark the provider DEAD for the lifetime
     of the router (do not retry).

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


class LLMProviderError(RuntimeError):
    """Recoverable provider failure — router will try the next one."""


class LLMConfigError(RuntimeError):
    """Permanent provider misconfiguration — router will skip forever."""


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Normalized response from any provider."""

    text: str
    provider: str
    model: str
    tokens_input: int = 0
    tokens_output: int = 0
    latency_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = "stop"


class LLMProvider(abc.ABC):
    """Interface every provider implements.

    Implementations should be thread-safe and async-only. Constructors
    may raise `LLMConfigError` immediately if required configuration
    is missing (e.g., API key) — the router catches this and removes
    the provider from rotation.
    """

    name: str

    @abc.abstractmethod
    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
        stop: list[str] | None = None,
        timeout_s: float = 30.0,
    ) -> LLMResponse:
        """Run a single completion. Raises LLMProviderError on transient
        failure; LLMConfigError on permanent misconfiguration."""

    @abc.abstractmethod
    async def health(self) -> bool:
        """Cheap reachability check — does not consume tokens."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Release any HTTP clients, sockets, etc."""
