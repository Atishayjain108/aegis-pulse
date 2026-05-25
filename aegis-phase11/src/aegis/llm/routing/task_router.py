"""
aegis.llm.routing.task_router — TaskRouter
==========================================

Routes requests to the most capable provider for each task type.

Task types and their preferred models:
  - ``code``       → qwen2.5-coder:14b (Ollama) or GPT-4.1 (OpenAI)
  - ``analysis``   → qwen2.5:14b (Ollama) or llama-3.3-70b (Groq)
  - ``structured`` → any model with low temperature
  - ``embedding``  → bge-m3 (Ollama) always
  - ``fast``       → llama3.2:3b or Groq instant
  - ``reasoning``  → qwen2.5:14b or claude-sonnet (Anthropic)

The router does NOT call any LLM — it only selects the best provider
and model string to pass to ``LLMGateway.complete()``.

Author: AEGIS Engineering
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import structlog

from aegis.llm.constants import (
    GROQ_FAST_MODEL,
    GROQ_DEFAULT_MODEL,
    OLLAMA_CODER_MODEL,
    OLLAMA_DEFAULT_MODEL,
    OLLAMA_FAST_MODEL,
)

if TYPE_CHECKING:
    from aegis.llm.providers.base import BaseProvider

_log = structlog.get_logger("aegis.llm.routing.task_router")


class TaskType(str, Enum):
    """Task classification used to select the best model."""

    CODE = "code"             # Code generation / review
    ANALYSIS = "analysis"     # Trend / market analysis (default)
    STRUCTURED = "structured" # JSON schema output
    FAST = "fast"             # Low-latency, simple queries
    REASONING = "reasoning"   # Complex multi-step reasoning
    EMBEDDING = "embedding"   # Embedding generation (not completions)
    CREATIVE = "creative"     # Ad copy, product descriptions


@dataclass(frozen=True)
class RoutingDecision:
    """Outcome of ``TaskRouter.route()``."""

    provider_name: str
    model: str
    task_type: TaskType
    temperature_override: float | None


# Preference matrix: task_type → [(provider, model, temp_override)]
# Tried in order; first available provider wins.
_ROUTING_MATRIX: dict[TaskType, list[tuple[str, str, float | None]]] = {
    TaskType.CODE: [
        ("ollama", OLLAMA_CODER_MODEL, 0.1),
        ("vllm", "Qwen/Qwen2.5-Coder-14B-Instruct", 0.1),
        ("openrouter", "qwen/qwen-2.5-coder-32b-instruct", 0.1),
        ("openai", "gpt-4.1-mini", 0.1),
        ("ollama", OLLAMA_DEFAULT_MODEL, 0.1),  # fallback
    ],
    TaskType.ANALYSIS: [
        ("ollama", OLLAMA_DEFAULT_MODEL, None),
        ("vllm", "Qwen/Qwen2.5-14B-Instruct", None),
        ("groq", GROQ_DEFAULT_MODEL, None),
        ("openrouter", "mistralai/mistral-small-3", None),
        ("gemini", "gemini-2.0-flash", None),
    ],
    TaskType.STRUCTURED: [
        ("ollama", OLLAMA_DEFAULT_MODEL, 0.0),
        ("groq", GROQ_DEFAULT_MODEL, 0.0),
        ("openrouter", "meta-llama/llama-3.3-70b-instruct:free", 0.0),
        ("gemini", "gemini-2.0-flash", 0.0),
    ],
    TaskType.FAST: [
        ("ollama", OLLAMA_FAST_MODEL, None),
        ("groq", GROQ_FAST_MODEL, None),
        ("openrouter", "google/gemma-3-27b-it:free", None),
        ("ollama", OLLAMA_DEFAULT_MODEL, None),
    ],
    TaskType.REASONING: [
        ("ollama", OLLAMA_DEFAULT_MODEL, 0.3),
        ("groq", GROQ_DEFAULT_MODEL, 0.3),
        ("anthropic", "claude-sonnet-4-20250514", 0.3),
        ("openai", "gpt-4.1-mini", 0.3),
    ],
    TaskType.CREATIVE: [
        ("ollama", OLLAMA_DEFAULT_MODEL, 0.7),
        ("groq", GROQ_DEFAULT_MODEL, 0.7),
        ("gemini", "gemini-2.0-flash", 0.7),
        ("anthropic", "claude-sonnet-4-20250514", 0.7),
    ],
    TaskType.EMBEDDING: [
        ("ollama", "bge-m3", None),
    ],
}


class TaskRouter:
    """
    Routes LLM requests to the best provider for each task type.

    Parameters
    ----------
    available_providers:
        Set of provider names that are registered and healthy.
        Call ``ProviderSelector.all_health()`` to get this.

    Example
    -------
    .. code-block:: python

        health = await selector.all_health()
        available = {name for name, ok in health.items() if ok}
        router = TaskRouter(available_providers=available)
        decision = router.route(TaskType.CODE)
        response = await gateway.complete(
            messages,
            provider=decision.provider_name,
            model=decision.model,
            temperature=decision.temperature_override or 0.2,
        )
    """

    def __init__(self, available_providers: set[str]) -> None:
        self._available = available_providers

    def route(self, task_type: TaskType = TaskType.ANALYSIS) -> RoutingDecision:
        """
        Return the best ``RoutingDecision`` for ``task_type``.

        Falls back through the preference matrix until a provider from
        ``available_providers`` is found.  If none match, returns the
        first entry unconditionally (gateway will handle the fallback).

        Raises
        ------
        ValueError
            When ``task_type`` is unknown (should not happen with the Enum).
        """
        matrix = _ROUTING_MATRIX.get(task_type, _ROUTING_MATRIX[TaskType.ANALYSIS])

        for provider_name, model, temp in matrix:
            if provider_name in self._available:
                decision = RoutingDecision(
                    provider_name=provider_name,
                    model=model,
                    task_type=task_type,
                    temperature_override=temp,
                )
                _log.debug(
                    "task_router.routed",
                    task=task_type.value,
                    provider=provider_name,
                    model=model,
                )
                return decision

        # No available provider matched — use first entry as best guess
        provider_name, model, temp = matrix[0]
        _log.warning(
            "task_router.no_available_match",
            task=task_type.value,
            fallback_provider=provider_name,
            available=sorted(self._available),
        )
        return RoutingDecision(
            provider_name=provider_name,
            model=model,
            task_type=task_type,
            temperature_override=temp,
        )

    @staticmethod
    def classify(prompt: str) -> TaskType:
        """
        Heuristically classify a prompt into a ``TaskType``.

        This is a lightweight keyword-based classifier — no LLM call.
        For complex classification, use the ``SemanticRouter`` instead.

        Parameters
        ----------
        prompt:
            The user prompt or last user message content.

        Returns
        -------
        TaskType
            Best-guess task type.
        """
        lower = prompt.lower()

        code_signals = ["write code", "function", "implement", "debug", "refactor",
                        "python", "javascript", "def ", "class ", "import "]
        fast_signals = ["ping", "status", "health", "hello", "hi ", "yes", "no"]
        reasoning_signals = ["why", "explain", "reason", "analyse", "compare",
                             "evaluate", "assess", "think through"]
        creative_signals = ["write a", "create a description", "ad copy", "marketing",
                            "tagline", "product title"]
        structured_signals = ["json", "schema", "structured", "extract", "parse",
                               "fill in", "format as"]

        scores: dict[TaskType, int] = {t: 0 for t in TaskType}
        for signal in code_signals:
            if signal in lower:
                scores[TaskType.CODE] += 1
        for signal in fast_signals:
            if signal in lower:
                scores[TaskType.FAST] += 1
        for signal in reasoning_signals:
            if signal in lower:
                scores[TaskType.REASONING] += 1
        for signal in creative_signals:
            if signal in lower:
                scores[TaskType.CREATIVE] += 1
        for signal in structured_signals:
            if signal in lower:
                scores[TaskType.STRUCTURED] += 1

        best = max(scores, key=lambda t: scores[t])
        if scores[best] == 0:
            return TaskType.ANALYSIS  # Default
        return best
