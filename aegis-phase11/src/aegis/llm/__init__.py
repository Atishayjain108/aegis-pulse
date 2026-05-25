"""
aegis.llm — Phase 11: Local LLM Orchestration Layer
=====================================================

Provides a unified, provider-agnostic LLM gateway supporting:

* **Ollama** (local, zero cost, private) — primary path
* **vLLM** (GPU-accelerated throughput server) — high-load local path
* **Groq** (free-tier cloud burst, generous RPM)
* **OpenRouter** (free-model cloud burst)
* **Google Gemini** (free-tier multimodal backup)
* **Anthropic / OpenAI** (optional paid, high-stakes only)

All providers are routed through a single ``LLMGateway`` that:
- Applies ``SemanticRouter`` to bypass LLMs for simple rule-based queries
- Selects the cheapest capable provider via ``ProviderSelector``
- Validates outputs with ``GuardrailsValidator``
- Returns typed, pydantic-validated responses via ``InstructorAdapter``
- Records every call in the ``PromptRegistry`` for audit and eval

Architecture relationship:
  Phase 0-5 code calls ``aegis.llm.gateway.LLMGateway.complete()``
  which replaces the legacy ``aegis.agents.llm`` router.

Author: AEGIS Engineering
"""

from aegis.llm.gateway.gateway import LLMGateway
from aegis.llm.gateway.response import LLMResponse
from aegis.llm.routing.selector import ProviderSelector
from aegis.llm.routing.semantic_router import SemanticRouter

__all__ = [
    "LLMGateway",
    "LLMResponse",
    "ProviderSelector",
    "SemanticRouter",
]

# Package version — keep in sync with pyproject.toml
__version__ = "11.0.0"
