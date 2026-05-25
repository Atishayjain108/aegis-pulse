"""tests/unit/llm/test_metrics.py — Metrics tests"""
from __future__ import annotations
import pytest


class TestMetrics:
    def test_record_request_no_error(self):
        from aegis.llm import metrics
        metrics.record_request(
            provider="ollama", model="qwen2.5:14b", status="ok",
            latency_ms=500, input_tokens=100, output_tokens=200, cost_usd=0.0
        )

    def test_record_guardrail_block(self):
        from aegis.llm import metrics
        metrics.record_guardrail_block("pii_detection")

    def test_record_circuit_trip(self):
        from aegis.llm import metrics
        metrics.record_circuit_trip("groq")

    def test_record_router_hit(self):
        from aegis.llm import metrics
        metrics.record_router_hit("health_check")

    def test_record_router_miss(self):
        from aegis.llm import metrics
        metrics.record_router_miss()

    def test_update_provider_health(self):
        from aegis.llm import metrics
        metrics.update_provider_health("ollama", healthy=True)
        metrics.update_provider_health("groq", healthy=False)

    def test_is_prometheus_available_returns_bool(self):
        from aegis.llm import metrics
        result = metrics.is_prometheus_available()
        assert isinstance(result, bool)
