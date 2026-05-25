"""
tests/unit/llm/test_providers.py — Provider adapter unit tests
==============================================================

Tests every provider adapter in isolation using httpx mocking.
No real HTTP calls are made.

Author: AEGIS Engineering
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_openai_response(content: str = "Test") -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


# ===========================================================================
# OllamaProvider
# ===========================================================================

class TestOllamaProvider:

    @pytest.mark.asyncio()
    async def test_health_check_returns_true_on_200(self):
        from aegis.llm.providers.ollama import OllamaProvider

        provider = OllamaProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        provider._client.get = AsyncMock(return_value=mock_resp)
        assert await provider.health_check() is True

    @pytest.mark.asyncio()
    async def test_health_check_returns_false_on_error(self):
        from aegis.llm.providers.ollama import OllamaProvider

        provider = OllamaProvider()
        provider._client.get = AsyncMock(side_effect=Exception("connection refused"))
        assert await provider.health_check() is False

    @pytest.mark.asyncio()
    async def test_raw_complete_parses_response(self):
        from aegis.llm.providers.ollama import OllamaProvider

        provider = OllamaProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_openai_response("Ollama says hi")
        mock_resp.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        response = await provider._raw_complete(
            [{"role": "user", "content": "hello"}]
        )
        assert response.content == "Ollama says hi"
        assert response.provider == "ollama"

    @pytest.mark.asyncio()
    async def test_embed_returns_vectors(self):
        from aegis.llm.providers.ollama import OllamaProvider

        provider = OllamaProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embeddings": [[0.1, 0.2, 0.3]]}
        mock_resp.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        vectors = await provider.embed(["test text"])
        assert len(vectors) == 1
        assert len(vectors[0]) == 3

    @pytest.mark.asyncio()
    async def test_list_models_returns_names(self):
        from aegis.llm.providers.ollama import OllamaProvider

        provider = OllamaProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "qwen2.5:14b"}, {"name": "bge-m3"}]}
        mock_resp.raise_for_status = MagicMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        models = await provider.list_models()
        assert "qwen2.5:14b" in models
        assert "bge-m3" in models

    @pytest.mark.asyncio()
    async def test_401_raises_provider_auth_error(self):
        from aegis.llm.errors import ProviderAuthError
        from aegis.llm.providers.ollama import OllamaProvider

        provider = OllamaProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ProviderAuthError):
            await provider._raw_complete([{"role": "user", "content": "hi"}])

    def test_name_property(self):
        from aegis.llm.providers.ollama import OllamaProvider
        assert OllamaProvider().name == "ollama"

    def test_context_limit_default(self):
        from aegis.llm.providers.ollama import OllamaProvider
        assert OllamaProvider().context_limit == 32_768


# ===========================================================================
# VLLMProvider
# ===========================================================================

class TestVLLMProvider:

    @pytest.mark.asyncio()
    async def test_health_check_200(self):
        from aegis.llm.providers.vllm import VLLMProvider

        p = VLLMProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        p._client.get = AsyncMock(return_value=mock_resp)
        assert await p.health_check() is True

    def test_name_property(self):
        from aegis.llm.providers.vllm import VLLMProvider
        assert VLLMProvider().name == "vllm"


# ===========================================================================
# GroqProvider
# ===========================================================================

class TestGroqProvider:

    @pytest.mark.asyncio()
    async def test_rate_limit_raises(self):
        from aegis.llm.providers.groq import GroqProvider

        p = GroqProvider(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        p._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="rate limit"):
            await p._raw_complete([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio()
    async def test_raw_complete_parses_content(self):
        from aegis.llm.providers.groq import GroqProvider

        p = GroqProvider(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            **_make_openai_response("Groq says hello"),
            "model": "llama-3.3-70b-versatile",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {}
        p._client.post = AsyncMock(return_value=mock_resp)

        response = await p._raw_complete([{"role": "user", "content": "hi"}])
        assert response.content == "Groq says hello"
        assert response.provider == "groq"

    def test_name(self):
        from aegis.llm.providers.groq import GroqProvider
        assert GroqProvider(api_key="k").name == "groq"


# ===========================================================================
# OpenRouterProvider
# ===========================================================================

class TestOpenRouterProvider:

    @pytest.mark.asyncio()
    async def test_429_rotates_model_and_raises(self):
        from aegis.llm.providers.openrouter import OpenRouterProvider

        p = OpenRouterProvider(api_key="test-key")
        initial_idx = p._current_model_idx
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        p._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RuntimeError):
            await p._raw_complete([{"role": "user", "content": "hi"}])

        assert p._current_model_idx > initial_idx

    def test_name(self):
        from aegis.llm.providers.openrouter import OpenRouterProvider
        assert OpenRouterProvider(api_key="k").name == "openrouter"


# ===========================================================================
# GeminiProvider
# ===========================================================================

class TestGeminiProvider:

    def test_split_system_messages(self):
        from aegis.llm.providers.gemini import GeminiProvider

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, contents = GeminiProvider._to_gemini_contents(messages)
        assert system == "You are helpful."
        assert len(contents) == 1
        assert contents[0]["role"] == "user"

    @pytest.mark.asyncio()
    async def test_raw_complete_parses_gemini_format(self):
        from aegis.llm.providers.gemini import GeminiProvider

        p = GeminiProvider(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Gemini says hi"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10},
        }
        mock_resp.raise_for_status = MagicMock()
        p._client.post = AsyncMock(return_value=mock_resp)

        response = await p._raw_complete([{"role": "user", "content": "hello"}])
        assert response.content == "Gemini says hi"
        assert response.provider == "gemini"

    def test_name(self):
        from aegis.llm.providers.gemini import GeminiProvider
        assert GeminiProvider(api_key="k").name == "gemini"


# ===========================================================================
# AnthropicProvider
# ===========================================================================

class TestAnthropicProvider:

    def test_split_messages_extracts_system(self):
        from aegis.llm.providers.anthropic import AnthropicProvider

        messages = [
            {"role": "system", "content": "System instruction"},
            {"role": "user", "content": "User message"},
            {"role": "assistant", "content": "Response"},
        ]
        system, conversation = AnthropicProvider._split_messages(messages)
        assert system == "System instruction"
        assert len(conversation) == 2
        assert conversation[0]["role"] == "user"

    @pytest.mark.asyncio()
    async def test_raw_complete_parses_anthropic_format(self):
        from aegis.llm.providers.anthropic import AnthropicProvider

        p = AnthropicProvider(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "Claude says hi"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
        mock_resp.raise_for_status = MagicMock()
        p._client.post = AsyncMock(return_value=mock_resp)

        response = await p._raw_complete([{"role": "user", "content": "hi"}])
        assert response.content == "Claude says hi"
        assert response.provider == "anthropic"

    def test_name(self):
        from aegis.llm.providers.anthropic import AnthropicProvider
        assert AnthropicProvider(api_key="k").name == "anthropic"

    def test_context_limit(self):
        from aegis.llm.providers.anthropic import AnthropicProvider
        assert AnthropicProvider(api_key="k").context_limit == 200_000


# ===========================================================================
# OpenAIProvider
# ===========================================================================

class TestOpenAIProvider:

    @pytest.mark.asyncio()
    async def test_raw_complete(self):
        from aegis.llm.providers.openai import OpenAIProvider

        p = OpenAIProvider(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_openai_response("OpenAI says hi")
        mock_resp.raise_for_status = MagicMock()
        p._client.post = AsyncMock(return_value=mock_resp)

        response = await p._raw_complete([{"role": "user", "content": "hi"}])
        assert response.content == "OpenAI says hi"
        assert response.provider == "openai"

    def test_name(self):
        from aegis.llm.providers.openai import OpenAIProvider
        assert OpenAIProvider(api_key="k").name == "openai"
