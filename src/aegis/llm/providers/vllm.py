"""
aegis.llm.providers.vllm — vLLM high-throughput local GPU adapter
==================================================================

vLLM exposes a fully OpenAI-compatible API.  This adapter is the
**secondary local path** — used when a discrete GPU is available and
throughput matters (batch inference, concurrent agent pipelines).

Environment:
  AEGIS_VLLM_BASE_URL   — default: http://localhost:8001
  AEGIS_VLLM_MODEL      — default: Qwen/Qwen2.5-14B-Instruct

Author: AEGIS Engineering
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from aegis.llm.constants import HEALTH_CHECK_TIMEOUT_S, VLLM_DEFAULT_MODEL
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


_log = structlog.get_logger("aegis.llm.providers.vllm")

_VLLM_DEFAULT_CONTEXT: int = 32_768


class VLLMProvider(BaseProvider):
    """
    Adapter for the vLLM OpenAI-compatible inference server.

    vLLM requires a GPU but delivers 10-30× higher throughput than Ollama
    for concurrent requests (continuous batching).  The API surface is
    identical to OpenAI's, so most of the logic mirrors ``OllamaProvider``.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8001",
        model: str = VLLM_DEFAULT_MODEL,
        api_key: str = "EMPTY",  # vLLM ignores this but httpx needs a value
        context_window: int = _VLLM_DEFAULT_CONTEXT,
        timeout_s: float = 120.0,
    ) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._context_window = context_window
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_s),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            http2=_HTTP2_AVAILABLE,
        )

    @property
    def name(self) -> str:
        return "vllm"

    @property
    def context_limit(self) -> int:
        return self._context_window

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
        try:
            resp = await self._client.post("/v1/chat/completions", json=payload)
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot reach vLLM at {self._base_url}: {exc}"
            ) from exc

        if resp.status_code == 401:
            raise ProviderAuthError("vLLM 401", provider=self.name)
        if resp.status_code == 400:
            body = resp.text[:500]
            if "context" in body.lower():
                raise ContextTooLong(body, provider=self.name)
            raise ValueError(f"vLLM 400: {body}")

        resp.raise_for_status()
        latency_ms = (time.perf_counter() - t0) * 1000
        data = resp.json()
        choice = data["choices"][0]
        content: str = choice["message"]["content"]
        usage_raw = data.get("usage", {})

        _log.debug(
            "vllm.complete",
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
                "/health",
                timeout=HEALTH_CHECK_TIMEOUT_S,
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # vLLM-specific: list available models
    # ------------------------------------------------------------------

    async def list_models(self) -> list[str]:
        """Return the models served by this vLLM instance."""
        try:
            resp = await self._client.get("/v1/models")
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception as exc:
            _log.warning("vllm.list_models.failed", error=str(exc))
            return []

    async def aclose(self) -> None:
        await self._client.aclose()
