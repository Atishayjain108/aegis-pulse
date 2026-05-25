"""
aegis.llm.providers.gemini — Google Gemini free-tier adapter
=============================================================

Gemini 2.0 Flash is free at 15 RPM / 1M tokens/day (as of Q2 2026).
This is the **third cloud fallback**.  Uses the Google Generative AI
REST API directly (no client library dependency).

Environment:
  AEGIS_GEMINI_API_KEY  — required for cloud burst
  AEGIS_GEMINI_MODEL    — default: gemini-2.0-flash

Author: AEGIS Engineering
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from aegis.llm.constants import GEMINI_DEFAULT_MODEL, HEALTH_CHECK_TIMEOUT_S
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


_log = structlog.get_logger("aegis.llm.providers.gemini")

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_GEMINI_CONTEXT: int = 1_048_576  # 1M token context for 2.0 Flash


class GeminiProvider(BaseProvider):
    """
    Adapter for Google's Gemini API using the REST interface.

    Converts AEGIS's OpenAI-style ``messages`` list to Gemini's
    ``contents`` / ``parts`` format internally.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = GEMINI_DEFAULT_MODEL,
        timeout_s: float = 45.0,
    ) -> None:
        super().__init__()
        self._model = model
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=_GEMINI_API_BASE,
            timeout=httpx.Timeout(timeout_s),
            headers={"Content-Type": "application/json"},
            http2=_HTTP2_AVAILABLE,
        )

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def context_limit(self) -> int:
        return _GEMINI_CONTEXT

    # ------------------------------------------------------------------
    # Message format conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _to_gemini_contents(
        messages: list[dict[str, str]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """
        Convert OpenAI-style messages to Gemini ``contents`` format.

        Returns ``(system_instruction, contents)``.
        """
        system_instruction: str | None = None
        contents: list[dict[str, Any]] = []

        for msg in messages:
            role = msg["role"]
            text = msg["content"]
            if role == "system":
                system_instruction = text
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": text}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": text}]})

        return system_instruction, contents

    # ------------------------------------------------------------------
    # BaseProvider
    # ------------------------------------------------------------------

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
        system_instruction, contents = self._to_gemini_contents(messages)

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        url = f"/models/{effective_model}:generateContent?key={self._api_key}"
        t0 = time.perf_counter()
        resp = await self._client.post(url, json=payload)

        if resp.status_code == 400:
            body = resp.text[:500]
            if "context" in body.lower() or "token" in body.lower():
                raise ContextTooLong(body, provider=self.name)
            raise ValueError(f"Gemini 400: {body}")
        if resp.status_code in {401, 403}:
            raise ProviderAuthError("Gemini: invalid API key", provider=self.name)
        if resp.status_code == 429:
            raise RuntimeError("Gemini rate limit (15 RPM free tier)")

        resp.raise_for_status()
        latency_ms = (time.perf_counter() - t0) * 1000
        data = resp.json()

        # Extract text from Gemini response structure
        candidate = data["candidates"][0]
        content = "".join(
            part.get("text", "")
            for part in candidate["content"]["parts"]
        )
        usage_meta = data.get("usageMetadata", {})

        _log.debug(
            "gemini.complete",
            model=effective_model,
            latency_ms=round(latency_ms, 1),
        )

        return LLMResponse(
            content=content,
            provider=self.name,
            model=effective_model,
            usage=self._make_usage(
                input_tokens=usage_meta.get("promptTokenCount", 0),
                output_tokens=usage_meta.get("candidatesTokenCount", 0),
            ),
            latency_ms=latency_ms,
            meta={"finish_reason": candidate.get("finishReason", "STOP")},
        )

    async def health_check(self) -> bool:
        try:
            url = f"/models/{self._model}?key={self._api_key}"
            resp = await self._client.get(url, timeout=HEALTH_CHECK_TIMEOUT_S)
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
