"""
aegis.llm.registry.model_registry — ModelRegistry
===================================================

Tracks every model available across all providers with their:
- Capabilities (context window, supports streaming, supports vision)
- Performance metrics (average latency, success rate)
- Cost information

This registry enables intelligent model selection beyond simple
provider priority — e.g. "pick the cheapest model that has > 128k
context and supports streaming".

Author: AEGIS Engineering
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger("aegis.llm.registry.model_registry")


@dataclass
class ModelCapabilities:
    """Static capabilities of a model."""

    context_window: int                  # Max tokens in context
    supports_streaming: bool = True      # Supports SSE streaming
    supports_vision: bool = False        # Accepts image inputs
    supports_function_calling: bool = False  # OpenAI-style function calls
    supports_json_mode: bool = False     # Guaranteed JSON output mode
    max_output_tokens: int = 4096


@dataclass
class ModelMetrics:
    """Runtime performance metrics (updated per call)."""

    total_calls: int = 0
    success_calls: int = 0
    total_latency_ms: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 1.0
        return self.success_calls / self.total_calls

    @property
    def avg_latency_ms(self) -> float:
        if self.success_calls == 0:
            return 0.0
        return self.total_latency_ms / self.success_calls

    def record_success(self, latency_ms: float, in_tokens: int, out_tokens: int) -> None:
        self.total_calls += 1
        self.success_calls += 1
        self.total_latency_ms += latency_ms
        self.total_input_tokens += in_tokens
        self.total_output_tokens += out_tokens
        self.last_seen = datetime.now(UTC)

    def record_failure(self) -> None:
        self.total_calls += 1
        self.last_seen = datetime.now(UTC)


@dataclass
class ModelEntry:
    """A single registered model."""

    provider: str
    model_id: str
    capabilities: ModelCapabilities
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0
    metrics: ModelMetrics = field(default_factory=ModelMetrics)
    tags: list[str] = field(default_factory=list)  # e.g. ["fast", "code", "reasoning"]

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "provider": self.provider,
            "model_id": self.model_id,
            "context_window": self.capabilities.context_window,
            "supports_streaming": self.capabilities.supports_streaming,
            "supports_vision": self.capabilities.supports_vision,
            "cost_per_1m_input": self.cost_per_1m_input,
            "cost_per_1m_output": self.cost_per_1m_output,
            "success_rate": round(self.metrics.success_rate, 4),
            "avg_latency_ms": round(self.metrics.avg_latency_ms, 1),
            "total_calls": self.metrics.total_calls,
            "tags": self.tags,
        }


class ModelRegistry:
    """
    Registry of all known models across all providers.

    Provides queries like:
    - "Give me all models with > 128k context and zero cost"
    - "Which model has the lowest average latency on provider=groq?"
    - "Update metrics for ollama/qwen2.5:14b after a successful call"

    The registry is populated at startup with well-known models and
    updated at runtime via ``record_call()``.

    Example
    -------
    .. code-block:: python

        registry = ModelRegistry.default()
        model = registry.find_cheapest(min_context=128_000)
        # model.key == "groq/llama-3.3-70b-versatile"

        registry.record_call("ollama", "qwen2.5:14b", success=True,
                              latency_ms=1200, in_tokens=500, out_tokens=300)
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelEntry] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, entry: ModelEntry) -> None:
        """Register a model entry."""
        self._models[entry.key] = entry
        _log.debug("model_registry.registered", key=entry.key)

    def register_many(self, entries: list[ModelEntry]) -> None:
        for entry in entries:
            self.register(entry)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def find_cheapest(
        self,
        *,
        min_context: int = 0,
        requires_streaming: bool = False,
        requires_vision: bool = False,
        tags: list[str] | None = None,
        provider: str | None = None,
    ) -> ModelEntry | None:
        """Return the cheapest model matching all constraints."""
        candidates = self._filter(
            min_context=min_context,
            requires_streaming=requires_streaming,
            requires_vision=requires_vision,
            tags=tags,
            provider=provider,
        )
        if not candidates:
            return None
        return min(candidates, key=lambda m: m.cost_per_1m_input + m.cost_per_1m_output)

    def find_fastest(
        self,
        *,
        min_context: int = 0,
        tags: list[str] | None = None,
        provider: str | None = None,
    ) -> ModelEntry | None:
        """Return the model with lowest average latency (from recorded metrics)."""
        candidates = self._filter(min_context=min_context, tags=tags, provider=provider)
        with_calls = [m for m in candidates if m.metrics.total_calls > 0]
        if not with_calls:
            return candidates[0] if candidates else None
        return min(with_calls, key=lambda m: m.metrics.avg_latency_ms)

    def find_by_key(self, key: str) -> ModelEntry | None:
        return self._models.get(key)

    def list_all(self) -> list[ModelEntry]:
        return list(self._models.values())

    def list_free(self) -> list[ModelEntry]:
        return [m for m in self._models.values()
                if m.cost_per_1m_input == 0.0 and m.cost_per_1m_output == 0.0]

    def _filter(
        self,
        *,
        min_context: int = 0,
        requires_streaming: bool = False,
        requires_vision: bool = False,
        tags: list[str] | None = None,
        provider: str | None = None,
    ) -> list[ModelEntry]:
        results = []
        for m in self._models.values():
            if m.capabilities.context_window < min_context:
                continue
            if requires_streaming and not m.capabilities.supports_streaming:
                continue
            if requires_vision and not m.capabilities.supports_vision:
                continue
            if provider and m.provider != provider:
                continue
            if tags:
                if not all(t in m.tags for t in tags):
                    continue
            results.append(m)
        return results

    # ------------------------------------------------------------------
    # Metrics recording
    # ------------------------------------------------------------------

    def record_call(
        self,
        provider: str,
        model_id: str,
        *,
        success: bool,
        latency_ms: float = 0.0,
        in_tokens: int = 0,
        out_tokens: int = 0,
    ) -> None:
        """Update runtime metrics for a model after a call."""
        key = f"{provider}/{model_id}"
        entry = self._models.get(key)
        if entry is None:
            # Auto-register unknown models with minimal info
            entry = ModelEntry(
                provider=provider,
                model_id=model_id,
                capabilities=ModelCapabilities(context_window=32_768),
            )
            self._models[key] = entry

        if success:
            entry.metrics.record_success(latency_ms, in_tokens, out_tokens)
        else:
            entry.metrics.record_failure()

    # ------------------------------------------------------------------
    # Factory: well-known model catalog
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> ModelRegistry:
        """Return a pre-populated registry with all well-known models."""
        registry = cls()
        registry.register_many([
            # Ollama local models
            ModelEntry("ollama", "qwen2.5:14b",
                       ModelCapabilities(32_768, supports_streaming=True),
                       tags=["analysis", "reasoning"]),
            ModelEntry("ollama", "qwen2.5-coder:14b",
                       ModelCapabilities(32_768, supports_streaming=True),
                       tags=["code"]),
            ModelEntry("ollama", "llama3.2:3b",
                       ModelCapabilities(128_000, supports_streaming=True),
                       tags=["fast"]),
            ModelEntry("ollama", "phi4",
                       ModelCapabilities(16_384, supports_streaming=True),
                       tags=["fast", "reasoning"]),
            ModelEntry("ollama", "bge-m3",
                       ModelCapabilities(8_192, supports_streaming=False),
                       tags=["embedding"]),
            # Groq
            ModelEntry("groq", "llama-3.3-70b-versatile",
                       ModelCapabilities(131_072, supports_streaming=True),
                       tags=["analysis", "reasoning", "fast"]),
            ModelEntry("groq", "llama-3.1-8b-instant",
                       ModelCapabilities(131_072, supports_streaming=True),
                       tags=["fast"]),
            # OpenRouter free models
            ModelEntry("openrouter", "mistralai/mistral-small-3",
                       ModelCapabilities(32_768, supports_streaming=True),
                       tags=["analysis"]),
            ModelEntry("openrouter", "meta-llama/llama-3.3-70b-instruct:free",
                       ModelCapabilities(131_072, supports_streaming=True),
                       tags=["analysis", "reasoning"]),
            # Gemini
            ModelEntry("gemini", "gemini-2.0-flash",
                       ModelCapabilities(1_048_576, supports_streaming=False, supports_vision=True),
                       tags=["analysis", "reasoning", "fast"]),
            # Anthropic (paid)
            ModelEntry("anthropic", "claude-sonnet-4-20250514",
                       ModelCapabilities(200_000, supports_streaming=False),
                       cost_per_1m_input=3.0, cost_per_1m_output=15.0,
                       tags=["reasoning", "analysis"]),
            # OpenAI (paid)
            ModelEntry("openai", "gpt-4.1-mini",
                       ModelCapabilities(128_000, supports_streaming=True,
                                        supports_function_calling=True, supports_json_mode=True),
                       cost_per_1m_input=0.40, cost_per_1m_output=1.60,
                       tags=["analysis", "structured", "code"]),
        ])
        return registry
