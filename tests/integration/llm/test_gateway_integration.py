"""
tests/integration/test_gateway_integration.py
==============================================

Integration tests for the full LLMGateway call chain.

These tests do NOT call real providers — they wire the gateway with
fully mocked provider HTTP clients and exercise the complete flow:

  Semantic Router → Provider Selection → Completion → Guardrails

Run with:
    pytest tests/integration/ -v

Author: AEGIS Engineering
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_llm_response(content: str = "Integration test response", provider: str = "ollama"):
    from aegis.llm.gateway.response import LLMResponse, TokenUsage

    return LLMResponse(
        content=content,
        provider=provider,
        model="test-model",
        usage=TokenUsage(input_tokens=50, output_tokens=100, total_tokens=150),
        latency_ms=120.0,
    )


def _mock_provider(name: str, response_content: str = "Response") -> MagicMock:
    """Build a fully mocked BaseProvider with controllable response."""
    p = MagicMock()
    p.name = name
    p.context_limit = 32_768
    p.health_check = AsyncMock(return_value=True)
    p._circuit = MagicMock()
    p._circuit.is_open = False
    p.complete = AsyncMock(return_value=_make_llm_response(response_content, provider=name))
    p.aclose = AsyncMock()
    return p


# ===========================================================================
# Full gateway call chain
# ===========================================================================


class TestGatewayIntegration:
    @pytest.fixture()
    async def gateway_with_ollama(self):
        """Gateway wired to a mock Ollama provider."""
        from aegis.llm.config import LLMSettings
        from aegis.llm.gateway.gateway import LLMGateway
        from aegis.llm.guardrails.validator import GuardrailsValidator
        from aegis.llm.routing.selector import ProviderSelector

        provider = _mock_provider("ollama", "Ollama integration response")
        providers = {"ollama": provider}
        settings = LLMSettings(disable_ollama=True, enable_guardrails=True)
        selector = ProviderSelector(providers=providers)
        guardrails = GuardrailsValidator()

        gw = LLMGateway(
            settings=settings,
            providers=providers,
            selector=selector,
            guardrails=guardrails,
        )
        yield gw
        await gw.aclose()

    @pytest.fixture()
    async def gateway_with_fallback(self):
        """Gateway with primary Ollama failing → Groq fallback."""
        from aegis.llm.config import LLMSettings
        from aegis.llm.gateway.gateway import LLMGateway
        from aegis.llm.routing.selector import ProviderSelector

        # Ollama fails after first attempt
        ollama = _mock_provider("ollama")
        ollama.complete = AsyncMock(side_effect=Exception("Ollama unreachable"))
        ollama.health_check = AsyncMock(return_value=False)

        groq = _mock_provider("groq", "Groq fallback response")

        providers = {"ollama": ollama, "groq": groq}
        settings = LLMSettings(disable_ollama=True, enable_guardrails=False)
        selector = ProviderSelector(providers=providers)

        gw = LLMGateway(
            settings=settings,
            providers=providers,
            selector=selector,
            guardrails=None,
        )
        yield gw, ollama, groq
        await gw.aclose()

    @pytest.mark.asyncio()
    async def test_complete_returns_llm_response(self, gateway_with_ollama):
        gw = gateway_with_ollama
        response = await gw.complete(
            [{"role": "user", "content": "Test message"}],
            provider="ollama",
        )
        assert response.content == "Ollama integration response"
        assert response.provider == "ollama"
        assert response.usage.total_tokens > 0

    @pytest.mark.asyncio()
    async def test_complete_records_cost(self, gateway_with_ollama):
        gw = gateway_with_ollama
        await gw.complete(
            [{"role": "user", "content": "Test"}],
            provider="ollama",
        )
        costs = gw.cost_summary()
        assert "ollama" in costs
        assert costs["ollama"] == 0.0  # Ollama is free

    @pytest.mark.asyncio()
    async def test_fallback_chain_skips_failed_provider(self, gateway_with_fallback):
        gw, ollama, groq = gateway_with_fallback
        response = await gw.complete(
            [{"role": "user", "content": "Test with fallback"}]
        )
        # Should have used groq (ollama failed)
        assert response.provider == "groq"
        assert response.content == "Groq fallback response"

    @pytest.mark.asyncio()
    async def test_health_returns_all_providers(self, gateway_with_ollama):
        gw = gateway_with_ollama
        health = await gw.health()
        assert isinstance(health, dict)
        assert "ollama" in health

    @pytest.mark.asyncio()
    async def test_complete_typed_returns_pydantic_model(self, gateway_with_ollama):
        from pydantic import BaseModel
        gw = gateway_with_ollama

        class SimpleOutput(BaseModel):
            answer: str
            score: float

        # Mock returns valid JSON
        gw._providers["ollama"].complete = AsyncMock(
            return_value=_make_llm_response('{"answer": "yes", "score": 0.9}', "ollama")
        )
        result = await gw.complete_typed(
            [{"role": "user", "content": "test"}],
            SimpleOutput,
            provider="ollama",
        )
        assert result.answer == "yes"
        assert result.score == pytest.approx(0.9)

    @pytest.mark.asyncio()
    async def test_guardrail_triggers_on_pii(self, gateway_with_ollama):
        from aegis.llm.errors import AllProvidersFailed, GuardrailBlock

        gw = gateway_with_ollama
        gw._providers["ollama"].complete = AsyncMock(
            return_value=_make_llm_response(
                "The answer is linked to SSN 123-45-6789", "ollama"
            )
        )
        with pytest.raises((GuardrailBlock, AllProvidersFailed)):
            await gw.complete(
                [{"role": "user", "content": "test"}],
                provider="ollama",
            )

    @pytest.mark.asyncio()
    async def test_skip_guardrails_bypasses_pii_check(self, gateway_with_ollama):
        gw = gateway_with_ollama
        gw._providers["ollama"].complete = AsyncMock(
            return_value=_make_llm_response("SSN 123-45-6789 in output", "ollama")
        )
        # skip_guardrails=True should not raise
        response = await gw.complete(
            [{"role": "user", "content": "test"}],
            provider="ollama",
            skip_guardrails=True,
        )
        assert response is not None


# ===========================================================================
# Semantic router integration
# ===========================================================================


class TestSemanticRouterIntegration:
    @pytest.mark.asyncio()
    async def test_router_short_circuits_llm(self):
        """When router matches, LLM provider complete() must NOT be called."""
        from aegis.llm.config import LLMSettings
        from aegis.llm.gateway.gateway import LLMGateway
        from aegis.llm.routing.selector import ProviderSelector
        from aegis.llm.routing.semantic_router import Route, SemanticRouter

        provider = _mock_provider("ollama")

        async def fake_embed(texts):
            # Return a consistent vector for "ping" and "health check"
            return [[1.0, 0.0, 0.0, 0.0]] * len(texts)

        route = Route(
            name="health_check",
            utterances=["ping", "health", "status"],
            response="All systems operational.",
            threshold=0.0,  # Always match for testing
        )
        router = SemanticRouter(routes=[route], embed_fn=fake_embed, threshold=0.0)
        await router.compile()

        gw = LLMGateway(
            settings=LLMSettings(disable_ollama=True, enable_guardrails=False),
            providers={"ollama": provider},
            selector=ProviderSelector(providers={"ollama": provider}),
            guardrails=None,
            semantic_router=router,
        )

        response = await gw.complete([{"role": "user", "content": "ping"}])
        assert response.router_short_circuit is True
        assert response.content == "All systems operational."
        # Provider should NOT have been called
        provider.complete.assert_not_called()

    @pytest.mark.asyncio()
    async def test_router_miss_falls_through_to_llm(self):
        """When router misses (skip_router=True), LLM provider complete() IS called."""
        from aegis.llm.config import LLMSettings
        from aegis.llm.gateway.gateway import LLMGateway
        from aegis.llm.routing.selector import ProviderSelector

        provider = _mock_provider("ollama", "LLM answered")

        gw = LLMGateway(
            settings=LLMSettings(disable_ollama=True, enable_guardrails=False),
            providers={"ollama": provider},
            selector=ProviderSelector(providers={"ollama": provider}),
            guardrails=None,
            semantic_router=None,  # No router — always falls through to LLM
        )

        response = await gw.complete(
            [{"role": "user", "content": "complex analytical question about AI chips"}],
            provider="ollama",
        )
        assert response.router_short_circuit is False
        assert response.content == "LLM answered"
        provider.complete.assert_called_once()


# ===========================================================================
# Streaming integration
# ===========================================================================


class TestStreamingIntegration:
    @pytest.mark.asyncio()
    async def test_collect_stream_reassembles_chunks(self):
        from aegis.llm.gateway.streaming import StreamChunk, collect_stream

        async def mock_stream():
            for i, text in enumerate(["Hello", " world", "!"]):
                yield StreamChunk(
                    delta=text,
                    provider="ollama",
                    model="qwen2.5:14b",
                    finish_reason="stop" if i == 2 else None,
                    chunk_index=i,
                )

        response = await collect_stream(mock_stream())
        assert response.content == "Hello world!"
        assert response.provider == "ollama"

    @pytest.mark.asyncio()
    async def test_collect_empty_stream(self):
        from aegis.llm.gateway.streaming import collect_stream

        async def empty_stream():
            return
            yield  # type: ignore[misc]  # Make it a generator

        response = await collect_stream(empty_stream())
        assert response.content == ""
        assert response.provider == "unknown"


# ===========================================================================
# Cost gate middleware integration
# ===========================================================================


class TestMiddlewareIntegration:
    @pytest.mark.asyncio()
    async def test_rate_limit_middleware_allows_free_providers(self):
        from aegis.llm.gateway.middleware import RateLimitMiddleware
        from aegis.llm.gateway.response import LLMResponse, TokenUsage

        middleware = RateLimitMiddleware()

        async def fake_complete(**kwargs):
            return LLMResponse(
                content="ok",
                provider="ollama",
                model="test",
                usage=TokenUsage(1, 1, 2),
                latency_ms=10.0,
            )

        # Ollama has 1000 RPM — should pass immediately
        result = await middleware(fake_complete, provider="ollama")
        assert result.content == "ok"

    @pytest.mark.asyncio()
    async def test_latency_middleware_passes_through(self):
        from aegis.llm.gateway.middleware import LatencyBudgetMiddleware
        from aegis.llm.gateway.response import LLMResponse, TokenUsage

        middleware = LatencyBudgetMiddleware(warn_ms=60_000)

        async def fast_fn(**kwargs):
            return LLMResponse(
                content="fast",
                provider="ollama",
                model="test",
                usage=TokenUsage(1, 1, 2),
                latency_ms=10.0,
            )

        result = await middleware(fast_fn)
        assert result.content == "fast"
        assert middleware.violation_count == 0

    @pytest.mark.asyncio()
    async def test_cost_gate_blocks_paid_when_budget_zero(self):
        from aegis.llm.gateway.middleware import CostGateMiddleware
        from aegis.llm.gateway.response import LLMResponse, TokenUsage

        middleware = CostGateMiddleware(daily_budget_usd=0.000001)
        # Exhaust the budget manually
        middleware._spent = 1.0  # Way over budget

        async def expensive_fn(**kwargs):
            return LLMResponse(
                content="paid",
                provider="anthropic",
                model="claude-3",
                usage=TokenUsage(100, 200, 300),
                latency_ms=500.0,
            )

        with pytest.raises(RuntimeError, match="budget"):
            await middleware(expensive_fn, provider="anthropic")

    @pytest.mark.asyncio()
    async def test_request_log_middleware_records_calls(self):
        from aegis.llm.gateway.middleware import RequestLogMiddleware
        from aegis.llm.gateway.response import LLMResponse, TokenUsage

        middleware = RequestLogMiddleware()

        async def fn(**kwargs):
            return LLMResponse(
                content="logged",
                provider="groq",
                model="llama",
                usage=TokenUsage(5, 10, 15),
                latency_ms=200.0,
            )

        await middleware(fn)
        calls = middleware.recent_calls(n=5)
        assert len(calls) == 1
        assert calls[0]["status"] == "ok"
