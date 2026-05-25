"""LLM provider abstraction with multi-tier free-first routing.

Routing order (legacy LLMRouter — still used by agent nodes):
    Ollama (local)  →  Groq (free)  →  OpenRouter (free)  →  Gemini (free)
                  →  (optional paid: Anthropic / OpenAI)

Phase 11 upgrade path:
    ``get_gateway()`` / ``complete_for_agent()`` from ``aegis.llm.bridge.agents_bridge``
    are re-exported here so agent nodes can call Phase 11 directly without
    changing their import path. If Phase 11 is absent these names are ``None``
    and callers must fall back to the legacy ``LLMRouter``.
"""

from .router import LLMResponse, LLMRouter, get_default_router

# ---------------------------------------------------------------------------
# Phase 11 gateway — re-exported for forward-compat. Agent nodes may use
# ``complete_for_agent()`` instead of router.complete() to go through the
# Phase 11 circuit-breaker + provider-selector + guardrails stack.
# Graceful no-op if aegis.llm is not yet installed (e.g. fresh checkout).
# ---------------------------------------------------------------------------
_phase11_available: bool = False
try:
    from aegis.llm.bridge.agents_bridge import (
        close_gateway,
        complete_for_agent,
        get_gateway,
        set_gateway,
    )
    _phase11_available = True
except Exception:
    get_gateway = None  # type: ignore[assignment]
    set_gateway = None  # type: ignore[assignment]
    close_gateway = None  # type: ignore[assignment]
    complete_for_agent = None  # type: ignore[assignment]

__all__ = [
    # Legacy (Phase 0-5)
    "LLMRouter",
    "LLMResponse",
    "get_default_router",
    # Phase 11
    "get_gateway",
    "set_gateway",
    "close_gateway",
    "complete_for_agent",
    "_phase11_available",
]
