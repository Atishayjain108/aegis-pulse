"""LLM provider abstraction with multi-tier free-first routing.

Routing order:
    Ollama (local)  →  Groq (free)  →  OpenRouter (free)  →  Gemini (free)
                  →  (optional paid: Anthropic / OpenAI)

Every provider conforms to `LLMProvider` (see `providers/base.py`).
The `LLMRouter` (router.py) tries providers in order, circuit-breaks
on three consecutive failures, and falls through to `None` if every
provider is unavailable. Agents must handle a `None` LLM result by
falling back to their heuristic-only path.
"""

from .router import LLMResponse, LLMRouter, get_default_router

__all__ = ["LLMRouter", "LLMResponse", "get_default_router"]
