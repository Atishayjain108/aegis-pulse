"""
aegis.llm.providers.groq — Groq cloud burst adapter
=====================================================

Groq provides a free tier with generous rate limits (14k req/day on
free tier as of Q2 2026).  This is the **first cloud fallback** when
local providers are unavailable.

Environment:
  AEGIS_GROQ_API_KEY  — required for cloud burst
  AEGIS_GROQ_MODEL    — default: llama-3.3-70b-versatile

Author: AEGIS Engineering
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from aegis.llm.constants import GROQ_DEFAULT_MODEL, HEALTH_CHECK_TIMEOUT_S
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


_log = structlog.get_logger("aegis.llm.providers.groq")

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_GROQ_CONTEXT_LIMIT = 131_072  # llama-3.3-70b context window


class GroqProvider(BaseProvider):
    """
    Adapter for Groq's LPU-accelerated inference cloud.

    Uses Groq's OpenAI-compatible ``/chat/completions`` endpoint.
    Rate-limit headers (``x-ratelimit-remaining-requests``) are read
    and propagated to metrics for budget tracking.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = GROQ_DEFAULT_MODEL,
        timeout_s: float = 30.0,
    ) -> None:
        super().__init__()
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=_GROQ_BASE_URL,
            timeout=httpx.Timeout(timeout_s),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            http2=_HTTP2_AVAILABLE,
        )

    @property
    def name(self) -> str:
        return "groq"

    @property
    def context_limit(self) -> int:
        return _GROQ_CONTEXT_LIMIT

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
        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        t0 = time.perf_counter()
        resp = await self._client.post("/chat/completions", json=payload)

        if resp.status_code == 401:
            raise ProviderAuthError("Groq: invalid API key", provider=self.name)
        if resp.status_code == 413:
            raise ContextTooLong("Groq: payload too large", provider=self.name)
        if resp.status_code == 429:
            raise RuntimeError("Groq rate limit exceeded — retry after back-off")

        resp.raise_for_status()
        latency_ms = (time.perf_counter() - t0) * 1000
        data = resp.json()
        choice = data["choices"][0]
        content: str = choice["message"]["content"]
        usage_raw = data.get("usage", {})

        # Surface remaining quota for monitoring
        remaining = resp.headers.get("x-ratelimit-remaining-requests", "?")
        _log.debug(
            "groq.complete",
            model=effective_model,
            latency_ms=round(latency_ms, 1),
            remaining_requests=remaining,
        )

        return LLMResponse(
            content=content,
            provider=self.name,
            model=effective_model,
            usage=self._make_usage(
                input_tokens=usage_raw.get("prompt_tokens", 0),
                output_tokens=usage_raw.get("completion_tokens", 0),
            ),
            latency_ms=latency_ms,
            meta={
                "finish_reason": choice.get("finish_reason", "stop"),
                "remaining_requests": remaining,
            },
        )

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get(
                "/models",
                timeout=HEALTH_CHECK_TIMEOUT_S,
            )
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
