"""
aegis.llm.routing.cost_router — CostAwareRouter
================================================

Extends ``ProviderSelector`` with real-time cost tracking.
Selects the cheapest provider that can handle the estimated token count,
while respecting the free-tier budget floor.

The router always prefers free providers (Ollama, vLLM, Groq free tier)
over paid ones, falling back only when necessary.

Author: AEGIS Engineering
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from aegis.llm.constants import (
    PROVIDER_COST_PER_1M_INPUT,
    PROVIDER_COST_PER_1M_OUTPUT,
    PROVIDER_PRIORITY,
)
from aegis.llm.routing.selector import ProviderSelector

if TYPE_CHECKING:
    from aegis.llm.providers.base import BaseProvider

_log = structlog.get_logger("aegis.llm.routing.cost_router")


class CostAwareRouter(ProviderSelector):
    """
    Provider selector that factors in cost per token.

    Selection order:
    1. Free providers first (cost == 0), sorted by ``PROVIDER_PRIORITY``.
    2. Paid providers sorted by estimated cost per token (cheapest first).
    3. Circuit-open or unhealthy providers excluded.

    Parameters
    ----------
    providers:
        Mapping of ``provider_name → BaseProvider`` instance.
    max_cost_usd_per_call:
        Hard ceiling per call. Paid providers exceeding this estimate
        are excluded. ``None`` = no ceiling.

    Example
    -------
    .. code-block:: python

        router = CostAwareRouter(
            providers={"ollama": ollama, "anthropic": anthropic},
            max_cost_usd_per_call=0.01,  # 1 cent ceiling
        )
        ordered = await router.select(estimated_tokens=500)
    """

    def __init__(
        self,
        providers: dict[str, "BaseProvider"],
        *,
        max_cost_usd_per_call: float | None = None,
    ) -> None:
        super().__init__(providers=providers)
        self._max_cost = max_cost_usd_per_call

    async def select(
        self,
        *,
        require_providers: list[str] | None = None,
        exclude_providers: list[str] | None = None,
        estimated_tokens: int = 1000,
    ) -> list["BaseProvider"]:  # type: ignore[override]
        """
        Select providers ordered by cost.

        Parameters
        ----------
        estimated_tokens:
            Estimated total tokens for this call (input + output).
            Used to pre-filter providers that would exceed ``max_cost_usd_per_call``.
        """
        all_ordered = await super().select(
            require_providers=require_providers,
            exclude_providers=exclude_providers,
        )

        if not self._max_cost:
            return all_ordered

        filtered: list["BaseProvider"] = []
        for p in all_ordered:
            in_cost = PROVIDER_COST_PER_1M_INPUT.get(p.name, 0.0)
            out_cost = PROVIDER_COST_PER_1M_OUTPUT.get(p.name, 0.0)
            avg_cost = (in_cost + out_cost) / 2.0
            estimated_usd = estimated_tokens / 1_000_000 * avg_cost

            if estimated_usd <= self._max_cost:
                filtered.append(p)
            else:
                _log.debug(
                    "cost_router.excluded_expensive",
                    provider=p.name,
                    estimated_usd=round(estimated_usd, 6),
                    max_cost=self._max_cost,
                )

        if not filtered:
            _log.warning(
                "cost_router.all_providers_too_expensive",
                estimated_tokens=estimated_tokens,
                max_cost_usd=self._max_cost,
                falling_back="all",
            )
            return all_ordered  # Fall back to full list rather than empty

        return filtered

    def cost_estimate(self, provider_name: str, tokens: int) -> float:
        """Return estimated USD cost for ``tokens`` tokens on ``provider_name``."""
        in_cost = PROVIDER_COST_PER_1M_INPUT.get(provider_name, 0.0)
        out_cost = PROVIDER_COST_PER_1M_OUTPUT.get(provider_name, 0.0)
        avg = (in_cost + out_cost) / 2.0
        return tokens / 1_000_000 * avg

    def free_providers(self) -> list[str]:
        """Return names of registered providers with zero cost."""
        return [
            name
            for name in self._providers
            if PROVIDER_COST_PER_1M_INPUT.get(name, 0.0) == 0.0
            and PROVIDER_COST_PER_1M_OUTPUT.get(name, 0.0) == 0.0
        ]

    def paid_providers(self) -> list[str]:
        """Return names of registered providers with non-zero cost."""
        return [
            name
            for name in self._providers
            if PROVIDER_COST_PER_1M_INPUT.get(name, 0.0) > 0.0
            or PROVIDER_COST_PER_1M_OUTPUT.get(name, 0.0) > 0.0
        ]
