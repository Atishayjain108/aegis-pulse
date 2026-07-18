"""tests/unit/llm/test_routing.py — routing layer unit tests"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestCostAwareRouter:

    def _make_provider(self, name: str) -> MagicMock:
        p = MagicMock()
        p.name = name
        p.health_check = AsyncMock(return_value=True)
        p._circuit = MagicMock()
        p._circuit.is_open = False
        return p

    @pytest.mark.asyncio()
    async def test_free_providers_always_included(self):
        from aegis.llm.routing.cost_router import CostAwareRouter
        ollama = self._make_provider("ollama")
        anthropic = self._make_provider("anthropic")
        router = CostAwareRouter(
            {"ollama": ollama, "anthropic": anthropic},
            max_cost_usd_per_call=0.0001,
        )
        ordered = await router.select(estimated_tokens=1000)
        names = [p.name for p in ordered]
        assert "ollama" in names

    def test_free_providers_list(self):
        from aegis.llm.routing.cost_router import CostAwareRouter
        ollama = self._make_provider("ollama")
        router = CostAwareRouter({"ollama": ollama})
        assert "ollama" in router.free_providers()

    def test_paid_providers_list(self):
        from aegis.llm.routing.cost_router import CostAwareRouter
        anthropic = self._make_provider("anthropic")
        router = CostAwareRouter({"anthropic": anthropic})
        assert "anthropic" in router.paid_providers()

    def test_cost_estimate_zero_for_free(self):
        from aegis.llm.routing.cost_router import CostAwareRouter
        router = CostAwareRouter({})
        assert router.cost_estimate("ollama", 1_000_000) == 0.0

    def test_cost_estimate_positive_for_paid(self):
        from aegis.llm.routing.cost_router import CostAwareRouter
        router = CostAwareRouter({})
        assert router.cost_estimate("anthropic", 1_000_000) > 0


class TestTaskRouter:

    def test_route_code_task(self):
        from aegis.llm.routing.task_router import TaskRouter, TaskType
        router = TaskRouter(available_providers={"ollama", "groq"})
        decision = router.route(TaskType.CODE)
        assert decision.task_type == TaskType.CODE
        assert decision.temperature_override == 0.1

    def test_route_fast_task(self):
        from aegis.llm.routing.task_router import TaskRouter, TaskType
        router = TaskRouter(available_providers={"ollama"})
        decision = router.route(TaskType.FAST)
        assert decision.task_type == TaskType.FAST

    def test_route_falls_back_when_no_match(self):
        from aegis.llm.routing.task_router import TaskRouter, TaskType
        # No providers available — should still return a decision
        router = TaskRouter(available_providers=set())
        decision = router.route(TaskType.ANALYSIS)
        assert decision.provider_name  # not empty

    def test_classify_code(self):
        from aegis.llm.routing.task_router import TaskRouter, TaskType
        task = TaskRouter.classify("Write a Python function to parse JSON")
        assert task == TaskType.CODE

    def test_classify_fast(self):
        from aegis.llm.routing.task_router import TaskRouter, TaskType
        task = TaskRouter.classify("ping")
        assert task == TaskType.FAST

    def test_classify_default_analysis(self):
        from aegis.llm.routing.task_router import TaskRouter, TaskType
        task = TaskRouter.classify("Tell me about this trend")
        assert task == TaskType.ANALYSIS
