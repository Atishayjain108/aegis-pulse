"""
aegis.llm.gateway.response — canonical LLMResponse dataclass
=============================================================

Every provider adapter returns one of these; callers never depend
on provider-specific response shapes.

Author: AEGIS Engineering
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token consumption for a single call."""

    input_tokens: int
    output_tokens: int
    total_tokens: int

    @property
    def cost_usd(
        self,
        input_cost_per_1m: float = 0.0,
        output_cost_per_1m: float = 0.0,
    ) -> float:
        """Approximate USD cost (zero for local/free providers)."""
        return (
            self.input_tokens / 1_000_000 * input_cost_per_1m
            + self.output_tokens / 1_000_000 * output_cost_per_1m
        )


@dataclass(slots=True)
class LLMResponse:
    """
    Unified, provider-agnostic response returned by ``LLMGateway.complete()``.

    Attributes
    ----------
    content:
        Raw text output from the model.
    provider:
        Name of the provider that served this response (e.g. ``"ollama"``).
    model:
        Exact model identifier used (e.g. ``"qwen2.5:14b"``).
    usage:
        Token consumption breakdown.
    latency_ms:
        Wall-clock time from request send to response received (milliseconds).
    request_id:
        UUID assigned by the gateway for tracing; correlates with logs/metrics.
    created_at:
        UTC timestamp when the response was received.
    structured:
        Populated by ``InstructorAdapter`` when a typed schema was requested.
    meta:
        Catch-all dict for provider-specific extras (finish reason, logprobs…).
    router_short_circuit:
        ``True`` if the semantic router answered without calling an LLM.
    """

    content: str
    provider: str
    model: str
    usage: TokenUsage
    latency_ms: float
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    structured: Any | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    router_short_circuit: bool = False

    # Cost helpers ---------------------------------------------------------

    def cost_usd(self, provider_costs: dict[str, tuple[float, float]]) -> float:
        """Return estimated cost given a ``{provider: (input_$/1M, output_$/1M)}`` map."""
        if self.provider not in provider_costs:
            return 0.0
        in_cost, out_cost = provider_costs[self.provider]
        return (
            self.usage.input_tokens / 1_000_000 * in_cost
            + self.usage.output_tokens / 1_000_000 * out_cost
        )

    # Serialisation --------------------------------------------------------

    def to_log_dict(self) -> dict[str, Any]:
        """Return a structlog-safe dict (no PII, bounded size)."""
        return {
            "request_id": self.request_id,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": round(self.latency_ms, 1),
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "router_short_circuit": self.router_short_circuit,
            "has_structured": self.structured is not None,
        }
