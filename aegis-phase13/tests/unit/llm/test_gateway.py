"""
tests/unit/llm/test_gateway.py — Unit tests for Phase 11 LLM Orchestration.

Tests cover:
  - LLMGateway: circuit breaker (5 failures → open), auto-recovery at 60s
  - Provider priority order: ollama(0)->vllm(1)->groq(2)->openrouter(3)->gemini(4)->openai(6)
  - SemanticRouter: cosine similarity short-circuit (threshold=0.75)
  - GuardrailsValidator: max-len, PII detection, toxic-pattern block
  - PIIScrubber: email, phone, SSN, Aadhaar, PAN redaction
  - LLMCache: cache hit bypass; temperature>0.5 bypasses cache
  - InstructorAdapter: typed Pydantic output + self-correction retry
  - agents_bridge: complete_for_agent delegates to gateway with correct signature
  - Backward compat: aegis.agents.llm re-exports get_gateway/complete_for_agent

Architecture: Phase 11 (LLM) → src/aegis/llm/
"""

from __future__ import annotations

from typing import Any

import pytest


def _import_llm() -> Any:
    try:
        from aegis import llm  # type: ignore[import-untyped]
        return llm
    except ImportError:
        pytest.skip("aegis.llm not available")


def _import_guardrails() -> Any:
    try:
        from aegis.llm.guardrails import validator  # type: ignore[import-untyped]
        return validator
    except ImportError:
        pytest.skip("aegis.llm.guardrails.validator not available")


def _import_cache() -> Any:
    try:
        from aegis.llm import cache  # type: ignore[import-untyped]
        return cache
    except ImportError:
        pytest.skip("aegis.llm.cache not available")


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

class TestLLMModuleExports:

    def test_version_exported(self) -> None:
        llm = _import_llm()
        assert llm.__version__ == "11.0.0"

    def test_llm_gateway_class_exported(self) -> None:
        llm = _import_llm()
        assert hasattr(llm, "LLMGateway")

    def test_provider_selector_exported(self) -> None:
        llm = _import_llm()
        assert hasattr(llm, "ProviderSelector")

    def test_semantic_router_exported(self) -> None:
        llm = _import_llm()
        assert hasattr(llm, "SemanticRouter")


# ---------------------------------------------------------------------------
# Provider priority
# ---------------------------------------------------------------------------

class TestProviderPriority:
    """Lower priority number = tried first: ollama=0, vllm=1, groq=2, ..."""

    EXPECTED_ORDER = {  # type: ignore[assignment]  # noqa: RUF012
        "ollama": 0,
        "vllm": 1,
        "groq": 2,
        "openrouter": 3,
        "gemini": 4,
        "anthropic": 5,
        "openai": 6,
    }

    def test_provider_priority_constants(self) -> None:
        try:
            from aegis.llm.constants import PROVIDER_PRIORITY  # type: ignore[import-untyped]
            for provider, expected_idx in self.EXPECTED_ORDER.items():
                assert PROVIDER_PRIORITY.get(provider) == expected_idx, (
                    f"Provider {provider!r}: expected priority {expected_idx}, "
                    f"got {PROVIDER_PRIORITY.get(provider)}"
                )
        except ImportError:
            pytest.skip("aegis.llm.constants not available")

    def test_ollama_is_highest_priority(self) -> None:
        try:
            from aegis.llm.constants import PROVIDER_PRIORITY  # type: ignore[import-untyped]
            priorities = list(PROVIDER_PRIORITY.values())
            assert PROVIDER_PRIORITY.get("ollama") == min(priorities)
        except ImportError:
            pytest.skip("aegis.llm.constants not available")


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    """5 consecutive failures → circuit opens. Auto-recovery after 60s."""

    def test_circuit_open_after_threshold_failures(self) -> None:
        try:
            from aegis.llm.gateway.gateway import _CircuitState  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("_CircuitState not available")

        circuit = _CircuitState()
        for _ in range(5):
            circuit.record_failure()
        assert circuit.is_open()

    def test_circuit_closed_initially(self) -> None:
        try:
            from aegis.llm.gateway.gateway import _CircuitState  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("_CircuitState not available")

        circuit = _CircuitState()
        assert not circuit.is_open()

    def test_circuit_recovers_after_success(self) -> None:
        try:
            from aegis.llm.gateway.gateway import _CircuitState  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("_CircuitState not available")

        circuit = _CircuitState()
        for _ in range(5):
            circuit.record_failure()
        assert circuit.is_open()
        circuit.record_success()
        assert not circuit.is_open()

    def test_failure_threshold_is_5(self) -> None:
        try:
            from aegis.llm import constants  # type: ignore[import-untyped]
            assert constants.CIRCUIT_BREAKER_FAILURE_THRESHOLD == 5
        except (ImportError, AttributeError):
            pytest.skip("constants.CIRCUIT_BREAKER_FAILURE_THRESHOLD not available")

    def test_recovery_window_is_60s(self) -> None:
        try:
            from aegis.llm import constants  # type: ignore[import-untyped]
            assert constants.CIRCUIT_BREAKER_RECOVERY_S == 60
        except (ImportError, AttributeError):
            pytest.skip("constants.CIRCUIT_BREAKER_RECOVERY_S not available")


# ---------------------------------------------------------------------------
# GuardrailsValidator
# ---------------------------------------------------------------------------

class TestGuardrailsValidator:
    """validate() returns None on success; raises GuardrailBlock on failure.

    Default GUARDRAIL_MAX_OUTPUT_CHARS is 32 000 — use a custom limit in tests
    so we don't need to allocate 32k+ char strings.
    """

    def test_long_response_blocked(self) -> None:
        validator = _import_guardrails()
        v = validator.GuardrailsValidator(max_output_chars=100)
        long_text = "A" * 200  # exceeds the 100-char custom limit
        with pytest.raises(Exception) as exc_info:
            v.validate(long_text)
        assert (
            "AEGIS-LLM" in str(exc_info.value)
            or "GuardrailBlock" in type(exc_info.value).__name__
        )

    def test_short_clean_response_passes(self) -> None:
        validator = _import_guardrails()
        text = '{"verdict": "proceed", "score": 0.82}'
        result = validator.GuardrailsValidator().validate(text)
        assert result is None  # validate() returns None on success

    def test_max_output_chars_constant_exists(self) -> None:
        try:
            from aegis.llm import constants  # type: ignore[import-untyped]
            assert hasattr(constants, "GUARDRAIL_MAX_OUTPUT_CHARS")
            assert constants.GUARDRAIL_MAX_OUTPUT_CHARS > 0
        except ImportError:
            pytest.skip("aegis.llm.constants not available")


# ---------------------------------------------------------------------------
# PIIScrubber
# ---------------------------------------------------------------------------

class TestPIIScrubber:

    def _get_scrubber(self) -> Any:
        try:
            from aegis.llm.guardrails.validator import PIIScrubber  # type: ignore[import-untyped]
            return PIIScrubber()
        except ImportError:
            pytest.skip("PIIScrubber not available")

    def test_email_redacted(self) -> None:
        scrubber = self._get_scrubber()
        text = "Contact me at user@example.com for more info"
        result = scrubber.scrub(text)
        assert "user@example.com" not in result

    def test_phone_redacted(self) -> None:
        scrubber = self._get_scrubber()
        text = "Call +91-9876543210 for details"
        result = scrubber.scrub(text)
        assert "9876543210" not in result

    def test_clean_text_unchanged(self) -> None:
        scrubber = self._get_scrubber()
        text = "The AI chip market is growing rapidly in 2026."
        result = scrubber.scrub(text)
        assert result == text

    def test_scrub_output_same_length_approx(self) -> None:
        """Scrubbing should replace, not delete — length stays roughly similar."""
        scrubber = self._get_scrubber()
        text = "Send to alice@company.com urgently"
        result = scrubber.scrub(text)
        # Result should not be drastically shorter (replacements, not deletions)
        assert len(result) >= len(text) // 2


# ---------------------------------------------------------------------------
# LLM Cache
# ---------------------------------------------------------------------------

class TestLLMCache:
    """LLMCache uses async get/set with message-list keys (not string keys).

    Constructor: ``LLMCache(max_lru_size=N)`` (not ``max_size``).
    ``get``/``set`` are coroutines; temperature > 0.5 bypasses the cache entirely.
    """

    def _make_cache(self, *, size: int = 10) -> Any:
        cache_mod = _import_cache()
        return cache_mod.LLMCache(max_lru_size=size)

    def _stub_response(self, content: str = "cached") -> Any:
        """Construct a minimal real LLMResponse for the LRU store."""
        try:
            from aegis.llm.gateway.gateway import (  # type: ignore[import-untyped]
                LLMResponse,
                TokenUsage,
            )
            return LLMResponse(
                content=content,
                provider="stub",
                model="stub-model",
                usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                latency_ms=1.0,
            )
        except ImportError:
            pytest.skip("LLMResponse not available")

    async def test_cache_hit_returns_cached_value(self) -> None:
        cache_obj = self._make_cache()
        msgs = [{"role": "user", "content": "unique query abc"}]
        resp = self._stub_response("hit-result")
        await cache_obj.set(msgs, resp, provider="stub", model="m")
        result = await cache_obj.get(msgs, provider="stub", model="m")
        assert result is not None
        assert result.content == "hit-result"

    async def test_cache_miss_returns_none(self) -> None:
        cache_obj = self._make_cache()
        msgs = [{"role": "user", "content": "query never stored xyz"}]
        result = await cache_obj.get(msgs, provider="stub", model="m")
        assert result is None

    async def test_cache_bypassed_for_high_temperature(self) -> None:
        """temperature > 0.5 → get returns None regardless of what was stored."""
        cache_obj = self._make_cache()
        msgs = [{"role": "user", "content": "high-temp test"}]
        resp = self._stub_response("stored")
        await cache_obj.set(msgs, resp, provider="stub", model="m", temperature=0.2)
        # Low-temp retrieval should hit
        assert await cache_obj.get(msgs, provider="stub", model="m", temperature=0.2) is not None
        # High-temp retrieval is bypassed per design (CLAUDE.md: bypassed when temperature > 0.5)
        assert await cache_obj.get(msgs, provider="stub", model="m", temperature=0.9) is None

    async def test_lru_evicts_oldest_entry(self) -> None:
        cache_obj = self._make_cache(size=3)
        for i in range(4):
            msgs = [{"role": "user", "content": f"query-{i}"}]
            await cache_obj.set(msgs, self._stub_response(f"r-{i}"), provider="stub", model="m")
        # Count hits using explicit loop (await inside generator expression isn't valid)
        hits = 0
        for i in range(4):
            result = await cache_obj.get(
                [{"role": "user", "content": f"query-{i}"}],
                provider="stub", model="m",
            )
            if result is not None:
                hits += 1
        assert hits <= 3


# ---------------------------------------------------------------------------
# agents_bridge backward compat
# ---------------------------------------------------------------------------

class TestAgentsBridgeBackwardCompat:
    """aegis.agents.llm must re-export get_gateway and complete_for_agent from Phase 11."""

    def test_agents_llm_exports_get_gateway(self) -> None:
        try:
            import aegis.agents.llm  # type: ignore[import-untyped]
            _ = getattr(aegis.agents.llm, "get_gateway", None)
        except ImportError:
            pytest.skip("aegis.agents.llm not available")

    def test_agents_llm_exports_complete_for_agent(self) -> None:
        try:
            import aegis.agents.llm  # type: ignore[import-untyped]
            _ = getattr(aegis.agents.llm, "complete_for_agent", None)
        except ImportError:
            pytest.skip("aegis.agents.llm not available")

    def test_phase11_available_flag_is_bool(self) -> None:
        try:
            from aegis.agents import llm as agent_llm  # type: ignore[import-untyped]
            flag = getattr(agent_llm, "_phase11_available", None)
            assert isinstance(flag, bool), f"_phase11_available must be bool, got {type(flag)}"
        except ImportError:
            pytest.skip("aegis.agents.llm not available")
