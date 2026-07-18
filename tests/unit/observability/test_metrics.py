"""Unit tests for aegis.observability.metrics."""

from __future__ import annotations

import aegis.observability.metrics as m_mod
from aegis.observability.metrics import (
    AEGIS_OBSERVABILITY_VERSION,
    _NoOpMetric,
    agent_task_duration_ms,
    agent_task_errors_total,
    alert_delivered_total,
    alert_delivery_latency_ms,
    cache_hit_ratio,
    database_query_duration_ms,
    dedup_ratio,
    ingest_errors_total,
    ingest_latency_ms,
    ingest_signals_total,
    killswitch_engaged_total,
    llm_cost_usd,
    llm_requests_total,
    llm_tokens_total,
    model_drift_score,
    model_inference_latency_ms,
    model_prediction_precision,
    redis_operation_duration_ms,
    signals_per_source,
    start_metrics_server,
)


class TestVersion:
    def test_version_string(self) -> None:
        assert AEGIS_OBSERVABILITY_VERSION == "14.0.0"


class TestNoOpMetric:
    def test_labels_returns_self(self) -> None:
        m = _NoOpMetric()
        assert m.labels(source="reddit") is m

    def test_all_operations_no_error(self) -> None:
        m = _NoOpMetric()
        m.inc(1)
        m.dec(1)
        m.set(0.5)
        m.observe(100.0)

    def test_context_manager(self) -> None:
        m = _NoOpMetric()
        with m.time():
            pass


class TestPhase1Metrics:
    def test_ingest_signals_total_inc(self) -> None:
        ingest_signals_total.labels(source="reddit-rss").inc()

    def test_ingest_errors_total_inc(self) -> None:
        ingest_errors_total.labels(source="bing-news", error_type="timeout").inc()

    def test_ingest_latency_ms_observe(self) -> None:
        ingest_latency_ms.labels(source="hacker-news").observe(250.0)

    def test_signals_per_source_set(self) -> None:
        signals_per_source.labels(source="amazon").set(42)

    def test_dedup_ratio_set(self) -> None:
        dedup_ratio.labels(source="github-trending").set(0.12)


class TestPhase2Metrics:
    def test_agent_task_duration_observe(self) -> None:
        agent_task_duration_ms.labels(node="scout").observe(350.0)

    def test_agent_task_errors_inc(self) -> None:
        agent_task_errors_total.labels(node="sentinel", error_type="timeout").inc()

    def test_llm_requests_inc(self) -> None:
        llm_requests_total.labels(provider="ollama", model="llama3.2:3b").inc()

    def test_llm_tokens_inc(self) -> None:
        llm_tokens_total.labels(provider="groq", direction="input").inc(512)
        llm_tokens_total.labels(provider="groq", direction="output").inc(128)

    def test_llm_cost_set(self) -> None:
        llm_cost_usd.labels(provider="openrouter").set(0.0042)


class TestPhase3Metrics:
    def test_inference_latency_observe(self) -> None:
        model_inference_latency_ms.labels(model_type="heuristic").observe(8.5)

    def test_model_precision_set(self) -> None:
        model_prediction_precision.set(0.94)

    def test_drift_score_set(self) -> None:
        model_drift_score.labels(feature="velocity_1h").set(0.03)


class TestPhase4Metrics:
    def test_alert_delivered_inc(self) -> None:
        alert_delivered_total.labels(channel="discord").inc()

    def test_alert_latency_observe(self) -> None:
        alert_delivery_latency_ms.labels(channel="ntfy").observe(850.0)

    def test_killswitch_inc(self) -> None:
        killswitch_engaged_total.inc()


class TestCrossCuttingMetrics:
    def test_db_query_observe(self) -> None:
        database_query_duration_ms.labels(operation="select").observe(4.2)

    def test_redis_op_observe(self) -> None:
        redis_operation_duration_ms.labels(command="XADD").observe(0.8)

    def test_cache_hit_ratio_set(self) -> None:
        cache_hit_ratio.set(0.45)


class TestStartMetricsServer:
    def test_idempotent(self, monkeypatch) -> None:
        """Calling start_metrics_server twice must not crash."""
        call_count = 0

        def fake_start(port: int) -> None:
            nonlocal call_count
            call_count += 1

        original = m_mod._metrics_server_started
        m_mod._metrics_server_started = False
        try:
            monkeypatch.setattr(m_mod, "_start_http_server", fake_start)
            monkeypatch.setattr(m_mod, "_PROMETHEUS_AVAILABLE", True)
            start_metrics_server(port=19999)
            start_metrics_server(port=19999)
            assert call_count == 1
        finally:
            m_mod._metrics_server_started = original

    def test_no_crash_without_prometheus(self, monkeypatch) -> None:
        """Should not raise when prometheus_client is absent."""
        original = m_mod._metrics_server_started
        m_mod._metrics_server_started = False
        try:
            monkeypatch.setattr(m_mod, "_PROMETHEUS_AVAILABLE", False)
            start_metrics_server(port=29999)
        finally:
            m_mod._metrics_server_started = original
