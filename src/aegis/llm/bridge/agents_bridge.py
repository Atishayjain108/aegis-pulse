"""
aegis.llm.bridge.agents_bridge — Phase 2 ↔ Phase 11 Integration Bridge
========================================================================

Replaces the legacy ``aegis.agents.llm`` router with the Phase 11
``LLMGateway``.  This module is the single integration seam: Phase 2
agent nodes call ``get_gateway()`` instead of directly instantiating
providers.

Design principles:
- No LangGraph imports here — keeps Phase 11 import-clean.
- Gateway is a process-level singleton, created once at first call.
- ``set_gateway()`` enables test injection without monkey-patching.
- Graceful fallback: if Phase 11 fails to initialise, the legacy
  Ollama→Groq→OpenRouter→Gemini chain in ``aegis.agents.llm`` still works.

Usage in agent nodes::

    from aegis.llm.bridge.agents_bridge import get_gateway

    async def scout_node(state: GraphState) -> dict:
        gw = await get_gateway()
        response = await gw.complete([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        return {"scout_output": response.content}

Author: AEGIS Engineering
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from aegis.llm.gateway.gateway import LLMGateway

_log = structlog.get_logger("aegis.llm.bridge.agents")

_gateway: LLMGateway | None = None
_lock = asyncio.Lock()


async def get_gateway() -> LLMGateway:
    """
    Return the process-level ``LLMGateway`` singleton.

    Creates the gateway on the first call using settings from the
    environment.  Thread-safe via asyncio lock.

    Returns
    -------
    LLMGateway
        Fully configured and ready-to-use gateway.
    """
    global _gateway  # noqa: PLW0603

    if _gateway is not None:
        return _gateway

    async with _lock:
        if _gateway is not None:
            return _gateway

        try:
            from aegis.llm.config import LLMSettings
            from aegis.llm.gateway.gateway import LLMGateway

            settings = LLMSettings()
            _gateway = await LLMGateway.create(settings)
            _log.info(
                "agents_bridge.gateway_created",
                providers=settings.enabled_providers(),
            )
        except Exception as exc:
            _log.error(
                "agents_bridge.gateway_create_failed",
                error=str(exc),
                hint="Phase 11 gateway unavailable — agent nodes will use legacy LLM router",
            )
            # Return a no-op gateway that raises on any call
            raise

    return _gateway


def set_gateway(gw: LLMGateway) -> None:
    """
    Inject a gateway for testing or custom configuration.

    Call before any agent node is invoked to replace the singleton.

    Parameters
    ----------
    gw:
        Pre-configured ``LLMGateway`` instance.
    """
    global _gateway  # noqa: PLW0603
    _gateway = gw
    _log.info("agents_bridge.gateway_injected")


async def close_gateway() -> None:
    """
    Close the singleton gateway and release resources.

    Call during application shutdown.
    """
    global _gateway  # noqa: PLW0603
    if _gateway is not None:
        await _gateway.aclose()
        _gateway = None
        _log.info("agents_bridge.gateway_closed")


async def complete_for_agent(
    agent_name: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    require_structured: bool = False,
) -> str:
    """
    Convenience wrapper: complete and return raw content string.

    Used by agent nodes that only need the text output (not the full
    ``LLMResponse`` object).

    Parameters
    ----------
    agent_name:
        Name of the calling agent — used in structured logs.
    messages:
        Chat messages to send.
    temperature:
        Sampling temperature.
    max_tokens:
        Maximum output tokens.
    require_structured:
        If ``True``, lower temperature to 0.1 for more deterministic output.

    Returns
    -------
    str
        Raw text content from the LLM.
    """
    gw = await get_gateway()
    effective_temp = 0.1 if require_structured else temperature

    # Multi-model council: when enabled and structured output is NOT required
    # (council fuses free-form reasoning), debate across local models and
    # return the synthesized answer. Falls back to single-model on any error.
    cfg = getattr(gw, "_settings", None) or getattr(gw, "settings", None)
    if cfg is not None and getattr(cfg, "council_enabled", False) and not require_structured:
        try:
            from aegis.llm.council import council_from_settings

            council = council_from_settings(gw, cfg)
            if council.n_models >= 2:
                result = await council.deliberate(messages)
                if result.final:
                    _log.info(
                        "agents_bridge.council",
                        agent=agent_name,
                        models=result.participating_models,
                        rounds=result.rounds_run,
                        synth=result.synth_model,
                    )
                    return result.final
        except Exception as exc:
            _log.warning("agents_bridge.council_failed", agent=agent_name, error=str(exc)[:200])

    response = await gw.complete(
        messages,
        temperature=effective_temp,
        max_tokens=max_tokens,
    )
    _log.debug(
        "agents_bridge.complete",
        agent=agent_name,
        provider=response.provider,
        latency_ms=round(response.latency_ms, 1),
        router_short_circuit=response.router_short_circuit,
    )
    return response.content
