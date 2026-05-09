"""Ollama provider — local-first, zero-cost, the AEGIS default.

Ollama exposes an OpenAI-compatible chat completions endpoint at
`/v1/chat/completions` and a native one at `/api/chat`. We use the
native endpoint because it returns token counts directly.

If the user has not set OLLAMA_BASE_URL, we default to
`http://127.0.0.1:11434`. If that endpoint is unreachable, the
router will trip the circuit breaker and fall through to Groq.

Author: AEGIS Pulse core team
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from .base import LLMConfigError, LLMProvider, LLMProviderError, LLMResponse

_DEFAULT_BASE_URL = "http://127.0.0.1:11434"
_DEFAULT_MODEL = "qwen2.5:14b"


class OllamaProvider(LLMProvider):
    """Local Ollama client. Recommended models: qwen2.5:14b (default),
    llama3.3:8b (faster), phi-4 (smaller / CPU-viable)."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL") or _DEFAULT_MODEL
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(30.0, connect=5.0),
            headers={"User-Agent": "aegis-pulse/2.0 (+ollama)"},
        )

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
        stop: list[str] | None = None,
        timeout_s: float = 30.0,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "temperature": float(temperature),
                "num_predict": int(max_tokens),
            },
        }
        if stop:
            payload["options"]["stop"] = list(stop)

        start = time.perf_counter()
        try:
            resp = await self._client.post(
                "/api/chat",
                json=payload,
                timeout=httpx.Timeout(timeout_s, connect=5.0),
            )
        except httpx.ConnectError as exc:
            # Ollama not running locally — recoverable; router trips breaker.
            raise LLMProviderError(f"ollama unreachable at {self.base_url}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"ollama http error: {exc}") from exc

        latency_ms = (time.perf_counter() - start) * 1000.0

        if resp.status_code == 404:
            # Model not pulled — config error, do not retry.
            raise LLMConfigError(
                f"ollama model '{self.model}' not found; run `ollama pull {self.model}`"
            )
        if resp.status_code >= 500:
            raise LLMProviderError(f"ollama 5xx: {resp.status_code} {resp.text[:200]}")
        if resp.status_code >= 400:
            raise LLMProviderError(f"ollama 4xx: {resp.status_code} {resp.text[:200]}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMProviderError(f"ollama returned non-JSON: {exc}") from exc

        # Native Ollama response shape:
        #   { "message": {"role": "assistant", "content": "..."},
        #     "prompt_eval_count": N, "eval_count": M, "done_reason": "stop" }
        message = data.get("message") or {}
        text = (message.get("content") or "").strip()

        return LLMResponse(
            text=text,
            provider=self.name,
            model=str(data.get("model", self.model)),
            tokens_input=int(data.get("prompt_eval_count", 0) or 0),
            tokens_output=int(data.get("eval_count", 0) or 0),
            latency_ms=latency_ms,
            raw=data,
            finish_reason=str(data.get("done_reason", "stop")),
        )

    async def health(self) -> bool:
        """Hits /api/tags — listing pulled models. ~1ms when up."""
        try:
            resp = await self._client.get("/api/tags", timeout=httpx.Timeout(3.0, connect=2.0))
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
