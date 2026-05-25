"""tests/unit/llm/test_registry.py"""
from __future__ import annotations
import pytest


class TestModelRegistry:
    def test_default_registry_has_ollama(self):
        from aegis.llm.registry.model_registry import ModelRegistry
        r = ModelRegistry.default()
        entry = r.find_by_key("ollama/qwen2.5:14b")
        assert entry is not None
        assert entry.provider == "ollama"

    def test_find_cheapest_free(self):
        from aegis.llm.registry.model_registry import ModelRegistry
        r = ModelRegistry.default()
        model = r.find_cheapest()
        assert model is not None
        assert model.cost_per_1m_input == 0.0

    def test_list_free_excludes_paid(self):
        from aegis.llm.registry.model_registry import ModelRegistry
        r = ModelRegistry.default()
        free = r.list_free()
        names = [m.key for m in free]
        assert not any("anthropic" in n for n in names)
        assert not any("openai" in n for n in names)

    def test_find_fastest_with_calls(self):
        from aegis.llm.registry.model_registry import ModelRegistry
        r = ModelRegistry.default()
        r.record_call("ollama", "qwen2.5:14b", success=True, latency_ms=500, in_tokens=10, out_tokens=20)
        r.record_call("groq", "llama-3.3-70b-versatile", success=True, latency_ms=200, in_tokens=10, out_tokens=20)
        fastest = r.find_fastest()
        assert fastest is not None

    def test_record_failure_increments(self):
        from aegis.llm.registry.model_registry import ModelRegistry
        r = ModelRegistry.default()
        r.record_call("ollama", "qwen2.5:14b", success=False)
        entry = r.find_by_key("ollama/qwen2.5:14b")
        assert entry.metrics.total_calls >= 1

    def test_auto_register_unknown_model(self):
        from aegis.llm.registry.model_registry import ModelRegistry
        r = ModelRegistry.default()
        r.record_call("newprovider", "new-model", success=True, latency_ms=100, in_tokens=5, out_tokens=5)
        entry = r.find_by_key("newprovider/new-model")
        assert entry is not None

    def test_find_by_tags(self):
        from aegis.llm.registry.model_registry import ModelRegistry
        r = ModelRegistry.default()
        results = r._filter(tags=["code"])
        assert len(results) > 0
        assert all("code" in m.tags for m in results)
