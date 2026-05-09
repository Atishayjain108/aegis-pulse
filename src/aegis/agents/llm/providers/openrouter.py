"""OpenRouter provider — third-tier fallback.

OpenRouter aggregates many models behind one OpenAI-compatible API
and offers several free-tier models (those with a `:free` suffix).
We default to a free model so this provider stays $0 by construction.

Auth: Authorization: Bearer <OPENROUTER_API_KEY>
URL:  https://openrouter.ai/api/v1/chat/completions

Author: AEGIS Pulse core team
"""
from __future__ import annotations

import os
import time

import httpx

from .base import LLMConfigError, LLMProvider, LLMProviderError, LLMResponse

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# A widely available free-tier model; users can override via env.
_DEFAULT_MODEL = "meta-llama/llama-3.3-8b-instruct:free"


class OpenRouterProvider(LLMProvider):
    """OpenRouter client; defaults to a free-tier model."""

    name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        referer: str | None = None,
        title: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise LLMConfigError("OPENROUTER_API_KEY is not set")
        self._api_key = key
        self.model = model or os.environ.get("OPENROUTER_MODEL") or _DEFAULT_MODEL
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")

        # OpenRouter requests these headers for attribution / rate routing.
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "aegis-pulse/2.0 (+openrouter)",
            "Content-Type": "application/json",
            "HTTP-Referer": referer or os.environ.get("OPENROUTER_REFERER", "https://aegis-pulse.local"),
            "X-Title": title or os.environ.get("OPENROUTER_TITLE", "AEGIS Pulse"),
        }

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(45.0, connect=5.0),
            headers=headers,
        )

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
        stop: list[str] | None = None,
        timeout_s: float = 45.0,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "stream": False,
        }
        if stop:
            payload["stop"] = list(stop)

        start = time.perf_counter()
        try:
            resp = await self._client.post(
                "/chat/completions",
                json=payload,
                timeout=httpx.Timeout(timeout_s, connect=5.0),
            )
        except httpx.ConnectError as exc:
            raise LLMProviderError(f"openrouter unreachable: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"openrouter http error: {exc}") from exc

        latency_ms = (time.perf_counter() - start) * 1000.0

        if resp.status_code == 401:
            raise LLMConfigError("openrouter returned 401 (invalid API key)")
        if resp.status_code == 402:
            # Out of credits on a paid model — permanent for this provider
            # until topped up; demote to config error.
            raise LLMConfigError("openrouter 402 — insufficient credits")
        if resp.status_code == 429:
            raise LLMProviderError(f"openrouter 429: {resp.text[:200]}")
        if resp.status_code >= 500:
            raise LLMProviderError(f"openrouter 5xx: {resp.status_code} {resp.text[:200]}")
        if resp.status_code >= 400:
            raise LLMProviderError(f"openrouter 4xx: {resp.status_code} {resp.text[:200]}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMProviderError(f"openrouter non-JSON: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMProviderError("openrouter returned no choices")
        first = choices[0] or {}
        message = first.get("message") or {}
        text = (message.get("content") or "").strip()
        usage = data.get("usage") or {}

        return LLMResponse(
            text=text,
            provider=self.name,
            model=str(data.get("model", self.model)),
            tokens_input=int(usage.get("prompt_tokens", 0) or 0),
            tokens_output=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=latency_ms,
            raw=data,
            finish_reason=str(first.get("finish_reason", "stop")),
        )

    async def health(self) -> bool:
        try:
            resp = await self._client.get("/models", timeout=httpx.Timeout(5.0, connect=3.0))
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
