"""Groq provider — free-tier, fast (Llama 3.3 70B at ~700 tokens/s).

Used when local Ollama is unavailable or rate-limited. Free tier as
of 2026: 14,400 requests/day with conservative per-minute caps. The
router records cost as $0 here, but we still meter token usage so the
nightly cost report flags drift before any plan upgrade is needed.

API is OpenAI-compatible: POST /openai/v1/chat/completions with
`Authorization: Bearer <GROQ_API_KEY>`.

If GROQ_API_KEY is unset, the provider raises LLMConfigError on
construction and the router permanently skips it.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import os
import time

import httpx

from .base import LLMConfigError, LLMProvider, LLMProviderError, LLMResponse

_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
_DEFAULT_MODEL = "llama-3.3-70b-versatile"


class GroqProvider(LLMProvider):
    """Groq Cloud client. Free-tier-aware."""

    name = "groq"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise LLMConfigError("GROQ_API_KEY is not set")
        self._api_key = key
        self.model = model or os.environ.get("GROQ_MODEL") or _DEFAULT_MODEL
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(30.0, connect=5.0),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": "aegis-pulse/2.0 (+groq)",
                "Content-Type": "application/json",
            },
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
            raise LLMProviderError(f"groq unreachable: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"groq http error: {exc}") from exc

        latency_ms = (time.perf_counter() - start) * 1000.0

        # 401 = bad key — permanent.
        if resp.status_code == 401:
            raise LLMConfigError("groq returned 401 (invalid API key)")
        # 429 = rate limit — recoverable; circuit breaker handles backoff.
        if resp.status_code == 429:
            raise LLMProviderError(f"groq 429 rate limited: {resp.text[:200]}")
        if resp.status_code >= 500:
            raise LLMProviderError(f"groq 5xx: {resp.status_code} {resp.text[:200]}")
        if resp.status_code >= 400:
            raise LLMProviderError(f"groq 4xx: {resp.status_code} {resp.text[:200]}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMProviderError(f"groq returned non-JSON: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMProviderError("groq returned no choices")
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
        """Models endpoint is the cheapest — a single HEAD-equivalent."""
        try:
            resp = await self._client.get("/models", timeout=httpx.Timeout(5.0, connect=3.0))
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
