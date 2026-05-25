"""
aegis.llm.providers.anthropic — Anthropic Claude adapter
=========================================================

PAID PATH (OPTIONAL) — only used for high-stakes decisions when every
free-tier provider is unavailable or insufficient.

Uses Anthropic's Messages API directly (no SDK dependency).

Environment:
  AEGIS_ANTHROPIC_API_KEY  — required to enable this provider
  AEGIS_ANTHROPIC_MODEL    — default: claude-sonnet-4-20250514

Author: AEGIS Engineering
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from aegis.llm.constants import ANTHROPIC_DEFAULT_MODEL, HEALTH_CHECK_TIMEOUT_S
from aegis.llm.errors import ContextTooLong, ProviderAuthError
from aegis.llm.gateway.response import LLMResponse
from aegis.llm.providers.base import BaseProvider

# ---------------------------------------------------------------------------
# HTTP/2 availability check — graceful fallback to HTTP/1.1
# ---------------------------------------------------------------------------

try:
    import h2  # noqa: F401
    _HTTP2_AVAILABLE = True
except ImportError:
    _HTTP2_AVAILABLE = False


_log = structlog.get_logger("aegis.llm.providers.anthropic")

_ANTHROPIC_API_BASE = "https://api.anthropic.com/v1"
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_CONTEXT: int = 200_000  # claude-sonnet-4 context window


class AnthropicProvider(BaseProvider):
    """
    Adapter for Anthropic's Claude API.

    Converts AEGIS's OpenAI-style ``messages`` format to Anthropic's
    ``system`` + ``messages`` format internally.  System messages are
    extracted and passed as the top-level ``system`` parameter.

    PAID PATH — only activate when ``AEGIS_ANTHROPIC_API_KEY`` is set.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = ANTHROPIC_DEFAULT_MODEL,
        timeout_s: float = 60.0,
    ) -> None:
        super().__init__()
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=_ANTHROPIC_API_BASE,
            timeout=httpx.Timeout(timeout_s),
            headers={
                "x-api-key": api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            http2=_HTTP2_AVAILABLE,
        )

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def context_limit(self) -> int:
        return _ANTHROPIC_CONTEXT

    @staticmethod
    def _split_messages(
        messages: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, str]]]:
        """
        Separate system messages from conversation messages.

        Returns ``(system_text, conversation_messages)``.
        Anthropic requires system as a top-level param, not in messages.
        """
        system_parts: list[str] = []
        conversation: list[dict[str, str]] = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
            else:
                conversation.append(msg)
        return "\n\n".join(system_parts), conversation

    async def _raw_complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> LLMResponse:
        effective_model = model or self._model
        system_text, conversation = self._split_messages(messages)

        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": conversation,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_text:
            payload["system"] = system_text

        t0 = time.perf_counter()
        resp = await self._client.post("/messages", json=payload)

        if resp.status_code == 401:
            raise ProviderAuthError("Anthropic: invalid API key", provider=self.name)
        if resp.status_code == 400:
            body = resp.text[:500]
            if "context" in body.lower() or "token" in body.lower():
                raise ContextTooLong(body, provider=self.name)
            raise ValueError(f"Anthropic 400: {body}")
        if resp.status_code == 529:
            raise RuntimeError("Anthropic API overloaded — retry later")

        resp.raise_for_status()
        latency_ms = (time.perf_counter() - t0) * 1000
        data = resp.json()

        content = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage_raw = data.get("usage", {})

        _log.debug(
            "anthropic.complete",
            model=effective_model,
            latency_ms=round(latency_ms, 1),
            stop_reason=data.get("stop_reason"),
        )

        return LLMResponse(
            content=content,
            provider=self.name,
            model=effective_model,
            usage=self._make_usage(
                input_tokens=usage_raw.get("input_tokens", 0),
                output_tokens=usage_raw.get("output_tokens", 0),
            ),
            latency_ms=latency_ms,
            meta={"stop_reason": data.get("stop_reason", "end_turn")},
        )

    async def health_check(self) -> bool:
        """Ping the models list endpoint — lightweight check."""
        try:
            resp = await self._client.get("/models", timeout=HEALTH_CHECK_TIMEOUT_S)
            return resp.status_code == 200
        except Exception:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
