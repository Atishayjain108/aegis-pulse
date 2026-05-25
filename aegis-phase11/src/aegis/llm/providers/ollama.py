"""
aegis.llm.providers.ollama — Ollama local inference adapter
============================================================

Ollama exposes an OpenAI-compatible ``/v1/chat/completions`` endpoint
locally.  This adapter is the **primary path** — zero cost, fully
private, no internet required.

Configuration (via Settings / env vars):
  AEGIS_OLLAMA_BASE_URL  — default: http://localhost:11434
  AEGIS_OLLAMA_MODEL     — default: qwen2.5:14b

Author: AEGIS Engineering
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from aegis.llm.constants import (
    HEALTH_CHECK_TIMEOUT_S,
    OLLAMA_DEFAULT_MODEL,
    OLLAMA_EMBED_MODEL,
)
from aegis.llm.errors import ContextTooLong, EmbedFailed, ProviderAuthError
from aegis.llm.gateway.response import LLMResponse
from aegis.llm.providers.base import BaseProvider

_log = structlog.get_logger("aegis.llm.providers.ollama")

# Ollama default context window — configurable per model via Modelfile
_OLLAMA_DEFAULT_CONTEXT: int = 32_768


class OllamaProvider(BaseProvider):
    """
    Adapter for the local Ollama inference server.

    Supports:
    - Chat completions (``/v1/chat/completions`` — OpenAI-compatible)
    - Embeddings (``/api/embed`` — native Ollama endpoint)
    - Model listing (``/api/tags``)
    - Health check (``/api/version``)

    The adapter is designed to be re-used across requests (shared httpx client).
    Call ``aclose()`` on shutdown.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = OLLAMA_DEFAULT_MODEL,
        embed_model: str = OLLAMA_EMBED_MODEL,
        context_window: int = _OLLAMA_DEFAULT_CONTEXT,
        timeout_s: float = 120.0,
    ) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._embed_model = embed_model
        self._context_window = context_window
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_s),
            headers={"Content-Type": "application/json"},
            # http2=True causes issues with Ollama's server — keep HTTP/1.1
            http2=False,
        )

    # ------------------------------------------------------------------
    # BaseProvider interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "ollama"

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
        """Send chat completion request to Ollama's OpenAI-compat endpoint."""
        effective_model = model or self._model
        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        # Pass through extra options (e.g. top_p, seed)
        if kwargs.get("options"):
            payload["options"] = kwargs["options"]

        t0 = time.perf_counter()
        try:
            resp = await self._client.post("/v1/chat/completions", json=payload)
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot reach Ollama at {self._base_url}: {exc}"
            ) from exc

        if resp.status_code == 401:
            raise ProviderAuthError(
                "Ollama returned 401 — check OLLAMA_ORIGINS config",
                provider=self.name,
            )
        if resp.status_code == 400:
            body = resp.text[:500]
            if "context" in body.lower() or "token" in body.lower():
                raise ContextTooLong(body, provider=self.name)
            raise ValueError(f"Ollama 400: {body}")

        resp.raise_for_status()
        latency_ms = (time.perf_counter() - t0) * 1000

        data = resp.json()
        choice = data["choices"][0]
        content: str = choice["message"]["content"]
        usage_raw = data.get("usage", {})

        _log.debug(
            "ollama.complete",
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
        """Ping ``/api/version`` — returns ``True`` if Ollama is up."""
        try:
            resp = await self._client.get(
                "/api/version",
                timeout=HEALTH_CHECK_TIMEOUT_S,
            )
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # Embedding support
    # ------------------------------------------------------------------

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        """
        Generate embeddings for a list of texts via Ollama's native API.

        Returns a list of float vectors, one per input text.

        Raises
        ------
        EmbedFailed
            When Ollama returns an error or connection fails.
        """
        effective_model = model or self._embed_model
        try:
            resp = await self._client.post(
                "/api/embed",
                json={"model": effective_model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings: list[list[float]] = data.get("embeddings", [])
            if not embeddings:
                raise EmbedFailed(
                    "Ollama returned empty embeddings",
                    provider=self.name,
                )
            return embeddings
        except EmbedFailed:
            raise
        except Exception as exc:
            raise EmbedFailed(str(exc), provider=self.name) from exc

    # ------------------------------------------------------------------
    # Model management helpers
    # ------------------------------------------------------------------

    async def list_models(self) -> list[str]:
        """Return names of all models currently pulled in Ollama."""
        try:
            resp = await self._client.get("/api/tags")
            resp.raise_for_status()
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as exc:
            _log.warning("ollama.list_models.failed", error=str(exc))
            return []

    async def pull_model(self, model: str) -> None:
        """
        Pull a model from the Ollama registry (idempotent).

        This is a blocking call that streams progress — use during
        bootstrap, not during hot path.
        """
        _log.info("ollama.pull_model.start", model=model)
        async with self._client.stream(
            "POST",
            "/api/pull",
            json={"name": model},
            timeout=3600.0,  # Large models take a while
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    _log.debug("ollama.pull_model.progress", model=model, line=line[:200])
        _log.info("ollama.pull_model.done", model=model)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
