"""Gemini provider — Google AI Studio free-tier fallback.

Endpoint: https://generativelanguage.googleapis.com/v1beta
Auth:     ?key=<GEMINI_API_KEY>  (or x-goog-api-key header)

Used last in the cloud rotation. Free tier as of 2026 allows ~15 RPM
on `gemini-2.0-flash` — generous for our P0/P1 alert volume.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import os
import time

import httpx

from .base import LLMConfigError, LLMProvider, LLMProviderError, LLMResponse

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider(LLMProvider):
    """Google AI Studio Gemini client. Free-tier-friendly defaults."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMConfigError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
        self._api_key = key
        self.model = model or os.environ.get("GEMINI_MODEL") or _DEFAULT_MODEL
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(30.0, connect=5.0),
            headers={
                "User-Agent": "aegis-pulse/2.0 (+gemini)",
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
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
        # Gemini API differs from OpenAI shape. systemInstruction goes
        # in its own field; user content goes into `contents`.
        payload: dict = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "user", "parts": [{"text": user}]},
            ],
            "generationConfig": {
                "temperature": float(temperature),
                "maxOutputTokens": int(max_tokens),
            },
        }
        if stop:
            payload["generationConfig"]["stopSequences"] = list(stop)

        path = f"/models/{self.model}:generateContent"
        start = time.perf_counter()
        try:
            resp = await self._client.post(
                path,
                json=payload,
                timeout=httpx.Timeout(timeout_s, connect=5.0),
            )
        except httpx.ConnectError as exc:
            raise LLMProviderError(f"gemini unreachable: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"gemini http error: {exc}") from exc

        latency_ms = (time.perf_counter() - start) * 1000.0

        if resp.status_code in (401, 403):
            raise LLMConfigError(f"gemini auth error {resp.status_code}: {resp.text[:200]}")
        if resp.status_code == 429:
            raise LLMProviderError(f"gemini 429: {resp.text[:200]}")
        if resp.status_code >= 500:
            raise LLMProviderError(f"gemini 5xx: {resp.status_code} {resp.text[:200]}")
        if resp.status_code >= 400:
            raise LLMProviderError(f"gemini 4xx: {resp.status_code} {resp.text[:200]}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMProviderError(f"gemini non-JSON: {exc}") from exc

        # Extract first candidate's text.
        candidates = data.get("candidates") or []
        if not candidates:
            # Sometimes Gemini blocks via promptFeedback; surface clearly.
            block_reason = (data.get("promptFeedback") or {}).get("blockReason")
            if block_reason:
                raise LLMProviderError(f"gemini blocked content: {block_reason}")
            raise LLMProviderError("gemini returned no candidates")

        first = candidates[0] or {}
        parts = ((first.get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts).strip()

        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            tokens_input=int(usage.get("promptTokenCount", 0) or 0),
            tokens_output=int(usage.get("candidatesTokenCount", 0) or 0),
            latency_ms=latency_ms,
            raw=data,
            finish_reason=str(first.get("finishReason", "STOP")),
        )

    async def health(self) -> bool:
        try:
            resp = await self._client.get(
                f"/models/{self.model}",
                timeout=httpx.Timeout(5.0, connect=3.0),
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
