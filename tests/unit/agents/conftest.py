"""
Agent-test fixtures — scoped to tests/unit/agents/.

These fixtures handle Phase 2 singletons that must be reset between tests:
  * The LLM router (cached module-level singleton).
  * The compiled LangGraph graph cache (keyed by constructor params).

We do NOT modify sys.path here — the package is installed in development
mode via `uv sync`, so `aegis.*` is importable from the installed tree.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _reset_agent_singletons() -> pytest.FixtureResult[None]:
    """Reset LLM router and compiled-graph cache before/after every agent test."""
    from aegis.agents import runner as runner_module
    from aegis.agents.llm import router as router_module

    asyncio.run(router_module.reset_default_router())
    asyncio.run(runner_module.reset_graph_cache())

    yield

    asyncio.run(router_module.reset_default_router())
    asyncio.run(runner_module.reset_graph_cache())


@pytest.fixture
def trend_factory():
    """Build a TrendCandidate with sensible defaults for agent unit tests."""
    from aegis.agents.schemas import TrendCandidate

    def _build(**overrides):
        defaults = {
            "trend_id": "trend-test-001",
            "title": "Test trend",
            "summary": "A test trend summary for unit tests.",
            "velocity_1h": 10.0,
            "velocity_6h": 40.0,
            "velocity_24h": 120.0,
            "sentiment": 0.4,
            "commercial_intent": 0.6,
            "novelty": 0.5,
            "coordination_risk": 0.1,
            "signal_count": 30,
            "unique_authors": 18,
            "platforms": ["reddit", "tiktok"],
            "sample_signal_ids": ["s1", "s2"],
            "representative_text": "People love this product, life-changing!",
        }
        defaults.update(overrides)
        return TrendCandidate(**defaults)

    return _build
