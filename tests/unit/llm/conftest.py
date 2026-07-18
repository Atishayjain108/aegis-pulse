"""
tests/unit/llm/conftest.py — shared pytest fixtures for Phase 11 tests
=======================================================================

Author: AEGIS Engineering
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Gateway fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_ollama_provider():
    """A fully mocked OllamaProvider that returns a clean response."""
    from aegis.llm.gateway.response import LLMResponse, TokenUsage

    p = MagicMock()
    p.name = "ollama"
    p.context_limit = 32_768
    p.health_check = AsyncMock(return_value=True)
    p._circuit = MagicMock()
    p._circuit.is_open = False
    p.complete = AsyncMock(
        return_value=LLMResponse(
            content="Mock Ollama response",
            provider="ollama",
            model="qwen2.5:14b",
            usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
            latency_ms=42.0,
        )
    )
    return p


@pytest.fixture()
def mock_groq_provider():
    """A fully mocked GroqProvider."""
    from aegis.llm.gateway.response import LLMResponse, TokenUsage

    p = MagicMock()
    p.name = "groq"
    p.context_limit = 131_072
    p.health_check = AsyncMock(return_value=True)
    p._circuit = MagicMock()
    p._circuit.is_open = False
    p.complete = AsyncMock(
        return_value=LLMResponse(
            content="Mock Groq response",
            provider="groq",
            model="llama-3.3-70b-versatile",
            usage=TokenUsage(input_tokens=15, output_tokens=25, total_tokens=40),
            latency_ms=180.0,
        )
    )
    return p


@pytest.fixture()
async def gateway(mock_ollama_provider):
    """
    An LLMGateway wired to a mock Ollama provider.
    Guardrails disabled so tests focus on routing logic.
    """
    from aegis.llm.config import LLMSettings
    from aegis.llm.gateway.gateway import LLMGateway
    from aegis.llm.routing.selector import ProviderSelector

    settings = LLMSettings(disable_ollama=True, enable_guardrails=False)
    providers = {"ollama": mock_ollama_provider}
    selector = ProviderSelector(providers=providers)

    gw = LLMGateway(
        settings=settings,
        providers=providers,
        selector=selector,
        guardrails=None,
        semantic_router=None,
    )
    yield gw
    await gw.aclose()


# ---------------------------------------------------------------------------
# Template directory fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def minimal_template_dir(tmp_path: Path) -> Path:
    """Create a minimal template directory with one test template."""
    content = """\
---
name: minimal
version: 1
required_vars: [topic]
description: "Minimal test template"
---
Analyse the topic: {{ topic }}
"""
    (tmp_path / "minimal.jinja2").write_text(content, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def loaded_registry(minimal_template_dir: Path):
    """A PromptRegistry loaded from the minimal template directory."""
    from aegis.llm.registry.prompt_registry import PromptRegistry

    registry = PromptRegistry(minimal_template_dir)
    registry.load_all()
    return registry


# ---------------------------------------------------------------------------
# Embed function fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def deterministic_embed_fn():
    """
    Deterministic embedding function for SemanticRouter tests.
    Produces a 4-dimensional vector based on text length.
    """

    async def embed(texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            n = len(text)
            v = [n / 200.0, (n % 7) / 7.0, 0.5, 0.3]
            # Normalise to unit vector
            mag = sum(x * x for x in v) ** 0.5
            result.append([x / mag for x in v] if mag > 0 else v)
        return result

    return embed
