"""
aegis.llm.providers.openai — OpenAI adapter
============================================

PAID PATH (OPTIONAL) — last-resort fallback. Uses OpenAI's
chat completions API directly (no openai SDK dependency —
just httpx to keep the dependency tree lean).

Environment:
  AEGIS_OPENAI_API_KEY  — required to enable this provider
  AEGIS_OPENAI_MODEL    — default: gpt-4.1-mini

Author: AEGIS Engineering
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from aegis.llm.constants import HEALTH_CHECK_TIMEOUT_S, OPENAI_DEFAULT_MODEL
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


_log = structlog.get_logger("aegis.llm.providers.openai")

_OPENAI_BASE_URL = "https://api.openai.com/v1"
_OPENAI_CONTEXT: int = 128_000  # gpt-4.1-mini context


class OpenAIProvider(BaseProvider):
    """
    Adapter for OpenAI's chat completions API.

    Uses the standard ``/v1/chat/completions`` endpoint with the
    OpenAI-format message schema (which is already our canonical format).
    PAID PATH — only activate when ``AEGIS_OPENAI_API_KEY`` is set.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = OPENAI_DEFAULT_MODEL,
        base_url: str = _OPENAI_BASE_URL,
        timeout_s: float = 60.0,
    ) -> None:
        super().__init__()
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            http2=_HTTP2_AVAILABLE,
        )

    @property
    def name(self) -> str:
        return "openai"

    @property
    def context_limit(self) -> int:
        return _OPENAI_CONTEXT

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
        }

        t0 = time.perf_counter()
        resp = await self._client.post("/chat/completions", json=payload)

        if resp.status_code == 401:
            raise ProviderAuthError("OpenAI: invalid API key", provider=self.name)
        if resp.status_code == 400:
            body = resp.text[:500]
            if "context_length" in body or "maximum context" in body:
                raise ContextTooLong(body, provider=self.name)
            raise ValueError(f"OpenAI 400: {body}")
        if resp.status_code == 429:
            raise RuntimeError("OpenAI rate limit or quota exceeded")

        resp.raise_for_status()
        latency_ms = (time.perf_counter() - t0) * 1000
        data = resp.json()
        choice = data["choices"][0]
        content: str = choice["message"]["content"]
        usage_raw = data.get("usage", {})

        _log.debug(
            "openai.complete",
            model=effective_model,
            latency_ms=round(latency_ms, 1),
            finish_reason=choice.get("finish_reason"),
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
            meta={"finish_reason": choice.get("finish_reason", "stop")},
        )

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/models", timeout=HEALTH_CHECK_TIMEOUT_S)
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
