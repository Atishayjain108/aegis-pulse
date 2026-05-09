"""Tests for the LLM router and its circuit-breaker semantics."""
from __future__ import annotations

import asyncio

import pytest

from aegis.agents.llm.providers.base import (
    LLMConfigError,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
)
from aegis.agents.llm.router import LLMRouter, RouterConfig


class _MockProvider(LLMProvider):
    """Test double that records calls and returns canned responses."""

    def __init__(
        self,
        name: str,
        *,
        succeed: bool = True,
        response_text: str = "ok",
        raise_class: type[Exception] | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.name = name
        self.succeed = succeed
        self.response_text = response_text
        self.raise_class = raise_class
        self.delay_s = delay_s
        self.call_count = 0

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
        self.call_count += 1
        if self.delay_s:
            # Honor the inner timeout exactly like a real httpx provider:
            # if the configured delay would exceed timeout_s, treat as a
            # transient timeout failure (raises into the router which
            # marks it as a breaker hit).
            await asyncio.sleep(min(self.delay_s, timeout_s + 0.01))
            if self.delay_s > timeout_s:
                raise LLMProviderError(f"{self.name} timed out after {timeout_s}s")
        if self.raise_class is not None:
            raise self.raise_class(f"{self.name} failure")
        return LLMResponse(
            text=self.response_text,
            provider=self.name,
            model=f"{self.name}-test",
            tokens_input=10,
            tokens_output=5,
            latency_ms=1.0,
        )

    async def health(self) -> bool:
        return self.succeed

    async def close(self) -> None:
        pass


class TestRouterBasic:
    async def test_uses_first_available(self) -> None:
        a = _MockProvider("a")
        b = _MockProvider("b")
        router = LLMRouter([a, b])
        resp = await router.complete(system="s", user="u")
        assert resp is not None
        assert resp.provider == "a"
        assert a.call_count == 1
        assert b.call_count == 0

    async def test_falls_through_on_provider_error(self) -> None:
        a = _MockProvider("a", raise_class=LLMProviderError)
        b = _MockProvider("b")
        router = LLMRouter([a, b])
        resp = await router.complete(system="s", user="u")
        assert resp is not None
        assert resp.provider == "b"
        assert a.call_count == 1
        assert b.call_count == 1

    async def test_returns_none_when_all_fail(self) -> None:
        a = _MockProvider("a", raise_class=LLMProviderError)
        b = _MockProvider("b", raise_class=LLMProviderError)
        router = LLMRouter([a, b])
        resp = await router.complete(system="s", user="u")
        assert resp is None

    async def test_no_providers_returns_none(self) -> None:
        router = LLMRouter([])
        resp = await router.complete(system="s", user="u")
        assert resp is None


class TestCircuitBreaker:
    async def test_breaker_trips_after_threshold(self) -> None:
        a = _MockProvider("a", raise_class=LLMProviderError)
        b = _MockProvider("b")
        router = LLMRouter(
            [a, b], config=RouterConfig(failure_threshold=3, cooldown_s=60.0)
        )

        # 3 calls — all should hit a (failing) before falling to b.
        for _ in range(3):
            resp = await router.complete(system="s", user="u")
            assert resp is not None and resp.provider == "b"

        # On the 4th call, breaker for `a` is open → skip directly to b.
        a.call_count = 0
        resp = await router.complete(system="s", user="u")
        assert resp is not None and resp.provider == "b"
        assert a.call_count == 0

    async def test_config_error_marks_dead_permanently(self) -> None:
        a = _MockProvider("a", raise_class=LLMConfigError)
        b = _MockProvider("b")
        router = LLMRouter([a, b])

        for _ in range(5):
            resp = await router.complete(system="s", user="u")
            assert resp is not None and resp.provider == "b"

        # `a` should have been called exactly once — first failure
        # marked it dead and it's never tried again.
        assert a.call_count == 1

    async def test_breaker_half_opens_after_cooldown(self, monkeypatch) -> None:
        # Use a tiny cooldown for this test.
        a = _MockProvider("a", raise_class=LLMProviderError)
        b = _MockProvider("b")
        router = LLMRouter(
            [a, b], config=RouterConfig(failure_threshold=2, cooldown_s=0.05)
        )

        # Trip the breaker.
        for _ in range(2):
            await router.complete(system="s", user="u")
        # Wait past cooldown.
        await asyncio.sleep(0.1)
        # Now `a` recovers.
        a.raise_class = None
        a.call_count = 0
        resp = await router.complete(system="s", user="u")
        assert resp is not None
        assert resp.provider == "a"
        assert a.call_count == 1

    async def test_success_resets_breaker(self) -> None:
        # Provider fails twice, then succeeds. Breaker should not be
        # tripped on the success run, and subsequent failures should
        # restart counting from zero.
        provider = _MockProvider("a")
        router = LLMRouter(
            [provider], config=RouterConfig(failure_threshold=3, cooldown_s=60)
        )

        # First, induce failures.
        provider.raise_class = LLMProviderError
        await router.complete(system="s", user="u")
        await router.complete(system="s", user="u")

        # Recover.
        provider.raise_class = None
        resp = await router.complete(system="s", user="u")
        assert resp is not None

        # Breaker reset → 2 more failures should not trip.
        provider.raise_class = LLMProviderError
        await router.complete(system="s", user="u")
        await router.complete(system="s", user="u")

        # 3rd consecutive failure trips it.
        await router.complete(system="s", user="u")
        # Now health/availability check.
        assert any(
            v.consecutive_failures >= 3 or v.opened_at is not None
            for v in router._breakers.values()
        )


class TestRouterTimeout:
    async def test_timeout_treated_as_failure(self) -> None:
        slow = _MockProvider("slow", delay_s=2.0)
        fast = _MockProvider("fast")
        router = LLMRouter([slow, fast])
        resp = await router.complete(
            system="s", user="u", timeout_s=0.05
        )
        # Slow times out → fall to fast.
        assert resp is not None
        assert resp.provider == "fast"


class TestRouterStats:
    async def test_stats_track_provider_usage(self) -> None:
        a = _MockProvider("a", raise_class=LLMProviderError)
        b = _MockProvider("b")
        router = LLMRouter([a, b])
        for _ in range(3):
            await router.complete(system="s", user="u")
        s = router.stats()
        assert s["total_calls"] == 3
        # All three completions went to b.
        assert s["by_provider"].get("b", 0) == 3


class TestProviders:
    """Smoke-tests that the concrete providers at least construct."""

    def test_groq_requires_key(self, monkeypatch) -> None:
        from aegis.agents.llm.providers.groq import GroqProvider

        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with pytest.raises(LLMConfigError):
            GroqProvider()

    def test_openrouter_requires_key(self, monkeypatch) -> None:
        from aegis.agents.llm.providers.openrouter import OpenRouterProvider

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(LLMConfigError):
            OpenRouterProvider()

    def test_gemini_requires_key(self, monkeypatch) -> None:
        from aegis.agents.llm.providers.gemini import GeminiProvider

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(LLMConfigError):
            GeminiProvider()

    def test_ollama_constructs_without_server(self, monkeypatch) -> None:
        # Ollama provider doesn't need a key — constructor must succeed
        # even when the daemon is unreachable. Health check handles
        # reachability separately.
        from aegis.agents.llm.providers.ollama import OllamaProvider

        provider = OllamaProvider()
        assert provider.name == "ollama"


class TestBuildRouterFromEnv:
    async def test_no_keys_returns_empty(self, monkeypatch) -> None:
        from aegis.agents.llm import router as router_module

        monkeypatch.setenv("AEGIS_DISABLE_OLLAMA", "1")
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        r = router_module.build_router_from_env()
        assert r.providers == []
        # Empty router returns None for completions.
        assert await r.complete(system="s", user="u") is None
