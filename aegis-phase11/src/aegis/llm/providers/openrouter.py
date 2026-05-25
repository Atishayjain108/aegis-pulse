"""
aegis.llm.providers.openrouter — OpenRouter free-model adapter
==============================================================

OpenRouter aggregates 100+ models, many available on a free tier.
This adapter is the **second cloud fallback**.

Environment:
  AEGIS_OPENROUTER_API_KEY  — required for cloud burst
  AEGIS_OPENROUTER_MODEL    — default: mistralai/mistral-small-3

Author: AEGIS Engineering
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from aegis.llm.constants import (
    HEALTH_CHECK_TIMEOUT_S,
    OPENROUTER_DEFAULT_MODEL,
    OPENROUTER_FREE_MODELS,
)
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


_log = structlog.get_logger("aegis.llm.providers.openrouter")

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_CONTEXT: int = 32_768  # Conservative default; varies by model


class OpenRouterProvider(BaseProvider):
    """
    Adapter for OpenRouter's aggregated model marketplace.

    Automatically rotates through ``free_models`` on rate-limit (429)
    to maximise throughput within the free tier.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = OPENROUTER_DEFAULT_MODEL,
        free_models: list[str] | None = None,
        site_url: str = "https://aegis.internal",
        site_name: str = "AEGIS Pulse",
        timeout_s: float = 45.0,
    ) -> None:
        super().__init__()
        self._model = model
        self._free_models = free_models or OPENROUTER_FREE_MODELS
        self._current_model_idx = 0
        self._client = httpx.AsyncClient(
            base_url=_OPENROUTER_BASE_URL,
            timeout=httpx.Timeout(timeout_s),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": site_url,
                "X-Title": site_name,
            },
            http2=_HTTP2_AVAILABLE,
        )

    @property
    def name(self) -> str:
        return "openrouter"

    @property
    def context_limit(self) -> int:
        return _OPENROUTER_CONTEXT

    def _next_free_model(self) -> str:
        """Rotate to the next free model in the pool."""
        model = self._free_models[self._current_model_idx % len(self._free_models)]
        self._current_model_idx += 1
        _log.info("openrouter.rotate_model", new_model=model)
        return model

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
            raise ProviderAuthError("OpenRouter: invalid API key", provider=self.name)
        if resp.status_code == 413:
            raise ContextTooLong("OpenRouter: payload too large", provider=self.name)
        if resp.status_code == 429:
            # Rate-limited on current model → rotate and raise to trigger retry
            self._next_free_model()
            raise RuntimeError("OpenRouter rate limit — rotating model")

        resp.raise_for_status()
        latency_ms = (time.perf_counter() - t0) * 1000
        data = resp.json()
        choice = data["choices"][0]
        content: str = choice["message"]["content"]
        usage_raw = data.get("usage", {})

        _log.debug(
            "openrouter.complete",
            model=effective_model,
            latency_ms=round(latency_ms, 1),
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
            resp = await self._client.get(
                "/models",
                timeout=HEALTH_CHECK_TIMEOUT_S,
            )
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
