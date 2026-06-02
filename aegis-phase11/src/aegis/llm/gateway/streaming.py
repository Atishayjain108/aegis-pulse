"""
aegis.llm.gateway.streaming — Server-Sent Event streaming wrapper
=================================================================

Provides an async generator interface over streaming LLM completions.
Callers can iterate over ``StreamChunk`` objects as they arrive, rather
than waiting for the full response.

Supports Ollama and OpenAI-compatible providers (Groq, vLLM, OpenRouter).
Gemini uses a different streaming format and is handled separately.

Usage::

    async for chunk in stream_complete(gateway, messages):
        print(chunk.delta, end="", flush=True)
    print()  # newline after stream

Author: AEGIS Engineering
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from aegis.llm.constants import LLM_HARD_TIMEOUT_S
from aegis.llm.gateway.response import LLMResponse, TokenUsage

if TYPE_CHECKING:
    from aegis.llm.gateway.gateway import LLMGateway

_log = structlog.get_logger("aegis.llm.gateway.streaming")


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """A single delta chunk from a streaming LLM response."""

    delta: str                   # New text content in this chunk
    provider: str                # Which provider sent this chunk
    model: str                   # Model name
    finish_reason: str | None    # Set on the final chunk ("stop", "length", …)
    chunk_index: int             # Zero-based index of this chunk


@dataclass
class StreamAccumulator:
    """Accumulates streaming chunks into a final ``LLMResponse``."""

    provider: str
    model: str
    _chunks: list[str] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter)
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, delta: str) -> None:
        self._chunks.append(delta)

    def finalise(self) -> LLMResponse:
        latency_ms = (time.perf_counter() - self._t0) * 1000
        content = "".join(self._chunks)
        return LLMResponse(
            content=content,
            provider=self.provider,
            model=self.model,
            usage=TokenUsage(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens or len(content) // 4,  # estimate
                total_tokens=self.input_tokens + (self.output_tokens or len(content) // 4),
            ),
            latency_ms=latency_ms,
            meta={"streaming": True},
        )


async def stream_openai_compat(
    client: httpx.AsyncClient,
    endpoint: str,
    payload: dict[str, Any],
    *,
    provider: str,
    model: str,
    timeout_s: float = LLM_HARD_TIMEOUT_S,
) -> AsyncIterator[StreamChunk]:
    """
    Stream from an OpenAI-compatible ``/v1/chat/completions`` endpoint.

    Parses Server-Sent Events line by line and yields ``StreamChunk``
    objects.  The final ``[DONE]`` sentinel terminates the stream.

    Parameters
    ----------
    client:
        Shared ``httpx.AsyncClient`` for the provider.
    endpoint:
        URL path, e.g. ``"/v1/chat/completions"``.
    payload:
        Request payload (``stream`` key will be forced to ``True``).
    provider:
        Provider name for attribution in chunks.
    model:
        Model name for attribution.
    timeout_s:
        Hard timeout for the entire stream.
    """
    payload = {**payload, "stream": True}
    idx = 0

    async with client.stream(
        "POST",
        endpoint,
        json=payload,
        timeout=httpx.Timeout(timeout_s),
    ) as resp:
        resp.raise_for_status()
        async for raw_line in resp.aiter_lines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("data: "):
                line = line[6:]
            if line == "[DONE]":
                break
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                _log.debug("streaming.skip_non_json", line=line[:80])
                continue

            choices = data.get("choices", [])
            if not choices:
                continue
            choice = choices[0]
            delta_obj = choice.get("delta", {})
            delta_text: str = delta_obj.get("content") or ""
            finish_reason: str | None = choice.get("finish_reason")

            if delta_text or finish_reason:
                yield StreamChunk(
                    delta=delta_text,
                    provider=provider,
                    model=model,
                    finish_reason=finish_reason,
                    chunk_index=idx,
                )
                idx += 1


async def stream_complete(
    gateway: LLMGateway,
    messages: list[dict[str, str]],
    *,
    provider_name: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    timeout_s: float = LLM_HARD_TIMEOUT_S,
) -> AsyncIterator[StreamChunk]:
    """
    High-level streaming entry point.

    Selects a provider (same logic as ``LLMGateway.complete``), opens a
    streaming connection, and yields ``StreamChunk`` objects.

    Ollama and all OpenAI-compatible providers are supported.
    Falls back to non-streaming ``complete()`` for Gemini and Anthropic,
    yielding a single chunk with the full content.

    Parameters
    ----------
    gateway:
        The configured ``LLMGateway`` instance.
    messages:
        Conversation messages.
    provider_name:
        Optional forced provider.
    model:
        Optional model override.
    temperature:
        Sampling temperature.
    max_tokens:
        Maximum output tokens.
    timeout_s:
        Hard timeout for the full stream.

    Yields
    ------
    StreamChunk
        One per SSE event from the provider.
    """
    # Determine which provider to use
    if provider_name and provider_name in gateway._providers:
        provider = gateway._providers[provider_name]
    else:
        ordered = await gateway._selector.select()
        provider = ordered[0] if ordered else None

    if provider is None:
        from aegis.llm.errors import AllProvidersFailed
        raise AllProvidersFailed("No providers available for streaming")

    pname = provider.name

    # Providers with OpenAI-compat streaming
    openai_compat_providers = {"ollama", "vllm", "groq", "openrouter", "openai"}

    if pname in openai_compat_providers:
        # Build endpoint mapping
        endpoint_map = {
            "ollama": "/v1/chat/completions",
            "vllm": "/v1/chat/completions",
            "groq": "/chat/completions",
            "openrouter": "/chat/completions",
            "openai": "/chat/completions",
        }
        payload: dict[str, Any] = {
            "model": model or getattr(provider, "_model", "default"),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        endpoint = endpoint_map[pname]
        client = provider._client  # type: ignore[attr-defined]

        async for chunk in stream_openai_compat(
            client, endpoint, payload,
            provider=pname,
            model=model or getattr(provider, "_model", "unknown"),
            timeout_s=timeout_s,
        ):
            yield chunk
    else:
        # Fallback: non-streaming providers (Gemini, Anthropic) — yield single chunk
        _log.debug("streaming.non_streaming_fallback", provider=pname)
        response = await provider.complete(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
        yield StreamChunk(
            delta=response.content,
            provider=pname,
            model=response.model,
            finish_reason="stop",
            chunk_index=0,
        )


async def collect_stream(stream: AsyncIterator[StreamChunk]) -> LLMResponse:
    """
    Consume a ``StreamChunk`` iterator and return a complete ``LLMResponse``.

    Useful when you need streaming for display but a full response for
    downstream processing.

    Parameters
    ----------
    stream:
        Async iterator of ``StreamChunk`` objects.

    Returns
    -------
    LLMResponse
        Assembled from all chunks.
    """
    acc: StreamAccumulator | None = None
    async for chunk in stream:
        if acc is None:
            acc = StreamAccumulator(provider=chunk.provider, model=chunk.model)
        acc.add(chunk.delta)

    if acc is None:
        # Empty stream
        return LLMResponse(
            content="",
            provider="unknown",
            model="unknown",
            usage=TokenUsage(0, 0, 0),
            latency_ms=0.0,
        )
    return acc.finalise()
