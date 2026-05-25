"""
aegis.llm.routing.selector — ProviderSelector
=============================================

Selects the cheapest, healthiest provider for each request.

Selection algorithm:
  1. Filter to providers that are healthy (cached health, refresh every 60 s).
  2. Filter to providers whose circuit breaker is closed.
  3. Sort by ``PROVIDER_PRIORITY`` (ascending — lower = higher priority).
  4. Return the ordered list; the gateway tries each in order.

The selector is intentionally stateless with respect to request context —
it does not look at the prompt content.  Prompt-level routing (e.g.
"use the coder model for code tasks") is handled by the ``SemanticRouter``.

Author: AEGIS Engineering
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from aegis.llm.constants import HEALTH_CHECK_TIMEOUT_S, PROVIDER_PRIORITY

if TYPE_CHECKING:
    from aegis.llm.providers.base import BaseProvider

_log = structlog.get_logger("aegis.llm.routing.selector")

# Health cache TTL — recheck provider health every N seconds
_HEALTH_CACHE_TTL_S: float = 60.0


@dataclass
class _HealthEntry:
    healthy: bool
    checked_at: float = field(default_factory=time.monotonic)

    def is_stale(self) -> bool:
        return (time.monotonic() - self.checked_at) > _HEALTH_CACHE_TTL_S


class ProviderSelector:
    """
    Selects and orders providers for each LLM request.

    Parameters
    ----------
    providers:
        Mapping of ``provider_name → BaseProvider`` instance.
    priority_override:
        Optional custom priority map; overrides ``PROVIDER_PRIORITY`` constants.

    Usage
    -----
    .. code-block:: python

        selector = ProviderSelector(providers={"ollama": ollama, "groq": groq})
        ordered = await selector.select()
        for provider in ordered:
            try:
                response = await provider.complete(messages)
                break
            except Exception:
                continue
    """

    def __init__(
        self,
        providers: dict[str, "BaseProvider"],
        *,
        priority_override: dict[str, int] | None = None,
    ) -> None:
        self._providers = providers
        self._priority = priority_override or PROVIDER_PRIORITY
        self._health_cache: dict[str, _HealthEntry] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def select(
        self,
        *,
        require_providers: list[str] | None = None,
        exclude_providers: list[str] | None = None,
    ) -> list["BaseProvider"]:
        """
        Return an ordered list of healthy providers to try.

        Parameters
        ----------
        require_providers:
            If set, only these provider names are considered.
        exclude_providers:
            Provider names to skip this request (e.g. already failed).
        """
        exclude = set(exclude_providers or [])

        candidates: list[str] = [
            name
            for name in self._providers
            if name not in exclude
            and (require_providers is None or name in require_providers)
        ]

        # Refresh stale health checks in parallel
        await self._refresh_health(candidates)

        healthy = [
            name
            for name in candidates
            if self._health_cache.get(name, _HealthEntry(healthy=False)).healthy
        ]

        if not healthy:
            # Fall back to ALL providers if health check is failing for everyone
            _log.warning(
                "provider_selector.no_healthy_providers",
                candidates=candidates,
            )
            healthy = candidates

        # Sort by priority (lower = higher priority, unknown = 99)
        ordered = sorted(healthy, key=lambda n: self._priority.get(n, 99))

        _log.debug(
            "provider_selector.selected",
            ordered=[f"{n}(p={self._priority.get(n, 99)})" for n in ordered],
        )
        return [self._providers[n] for n in ordered]

    async def health_of(self, name: str) -> bool:
        """Return current health status of a named provider (may trigger check)."""
        await self._refresh_health([name])
        return self._health_cache.get(name, _HealthEntry(healthy=False)).healthy

    async def all_health(self) -> dict[str, bool]:
        """Return health status for every registered provider."""
        await self._refresh_health(list(self._providers))
        return {
            name: self._health_cache.get(name, _HealthEntry(healthy=False)).healthy
            for name in self._providers
        }

    def register(self, name: str, provider: "BaseProvider") -> None:
        """Register a new provider at runtime (hot-plug)."""
        self._providers[name] = provider
        _log.info("provider_selector.registered", provider=name)

    def deregister(self, name: str) -> None:
        """Remove a provider from the pool."""
        self._providers.pop(name, None)
        self._health_cache.pop(name, None)
        _log.info("provider_selector.deregistered", provider=name)

    # ------------------------------------------------------------------
    # Health management
    # ------------------------------------------------------------------

    async def _refresh_health(self, names: list[str]) -> None:
        """Check health for stale entries in parallel."""
        stale = [
            n
            for n in names
            if n not in self._health_cache or self._health_cache[n].is_stale()
        ]
        if not stale:
            return

        async with self._lock:
            # Re-check inside lock to avoid duplicate concurrent checks
            stale = [
                n
                for n in stale
                if n not in self._health_cache or self._health_cache[n].is_stale()
            ]
            if not stale:
                return

            results = await asyncio.gather(
                *[self._check_one(n) for n in stale],
                return_exceptions=True,
            )
            for name, result in zip(stale, results, strict=True):
                healthy = isinstance(result, bool) and result
                self._health_cache[name] = _HealthEntry(healthy=healthy)
                _log.debug(
                    "provider_selector.health_check",
                    provider=name,
                    healthy=healthy,
                )

    async def _check_one(self, name: str) -> bool:
        try:
            return await asyncio.wait_for(
                self._providers[name].health_check(),
                timeout=HEALTH_CHECK_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001
            return False
