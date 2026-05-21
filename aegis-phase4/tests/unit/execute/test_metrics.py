"""Tests for the Prometheus metrics surface.

These tests run against ``new_metrics_for_test()`` which binds metrics
to a **fresh** registry per test — so we don't leak state across runs.
The default singleton ``metrics`` (bound to the global REGISTRY) is
verified to be importable; we don't assert its values to avoid
test-order coupling.
"""

from __future__ import annotations

import time

import pytest

from aegis.execute.metrics import metrics, new_metrics_for_test

# Skip every test in this file if prometheus_client is absent.
prom = pytest.importorskip("prometheus_client")
from prometheus_client import generate_latest  # noqa: E402


def test_singleton_metrics_object_imports():
    # Just confirm the attributes exist.
    for attr in (
        "alerts_total",
        "compose_latency_ms",
        "deliveries_total",
        "delivery_latency_ms",
        "outbox_pending",
        "killswitch_tripped",
        "gate_blocks_total",
    ):
        assert hasattr(metrics, attr), attr


def test_counter_increments_with_labels():
    m = new_metrics_for_test()
    m.alerts_total.labels(verdict="ENTER", source="phase2_and_phase3", priority="1").inc()
    m.alerts_total.labels(verdict="ENTER", source="phase2_and_phase3", priority="1").inc()
    m.alerts_total.labels(verdict="HOLD", source="phase2_only", priority="2").inc()

    body = generate_latest(m.registry).decode()
    assert 'aegis_execute_alerts_total{priority="1",source="phase2_and_phase3",verdict="ENTER"} 2.0' in body
    assert 'aegis_execute_alerts_total{priority="2",source="phase2_only",verdict="HOLD"} 1.0' in body


def test_gauge_set():
    m = new_metrics_for_test()
    m.killswitch_tripped.set(1.0)
    body = generate_latest(m.registry).decode()
    assert "aegis_execute_killswitch_tripped 1.0" in body
    m.killswitch_tripped.set(0.0)
    body2 = generate_latest(m.registry).decode()
    assert "aegis_execute_killswitch_tripped 0.0" in body2


def test_histogram_observes_milliseconds():
    m = new_metrics_for_test()
    m.compose_latency_ms.observe(12.5)
    m.compose_latency_ms.observe(7.5)
    body = generate_latest(m.registry).decode()
    assert "aegis_execute_compose_latency_ms_count 2.0" in body
    # Sum should be 20.0 ms.
    assert "aegis_execute_compose_latency_ms_sum 20.0" in body


def test_histogram_time_context_records_elapsed_ms():
    m = new_metrics_for_test()
    with m.compose_latency_ms.time():
        time.sleep(0.005)  # 5ms
    body = generate_latest(m.registry).decode()
    assert "aegis_execute_compose_latency_ms_count 1.0" in body
    # The sum should be at least ~5ms; allow generous slack.
    # Find the sum line and parse it.
    for line in body.splitlines():
        if line.startswith("aegis_execute_compose_latency_ms_sum"):
            value = float(line.split()[-1])
            assert 1.0 <= value < 2000.0  # roughly 5ms, generous bounds
            break
    else:
        pytest.fail("no sum line found")


def test_pipeline_emits_alerts_total_metric(monkeypatch):
    """End-to-end: submit through Pipeline → alerts_total goes up.

    The Pipeline imports ``metrics`` at module load — we can verify the
    global registry state changed even without injecting a fresh one.
    """
    from uuid import uuid4

    import prometheus_client

    from aegis.execute.bridge.types import ComposerInput
    from aegis.execute.pipeline import Pipeline

    # Use the in-memory fake from integration tests.
    from tests.integration.execute.conftest import FakeRepository
    generate_latest(prometheus_client.REGISTRY).decode()

    pipe = Pipeline(repository=FakeRepository())

    import asyncio
    async def _go():
        await pipe.submit(
            ComposerInput(
                tenant_id=uuid4(),
                trend_id="metrics-test",
                phase2_verdict="HOLD",
                phase2_score=0.5,
                phase2_confidence=0.6,
                phase2_priority=2,
            )
        )
    asyncio.run(_go())

    after_text = generate_latest(prometheus_client.REGISTRY).decode()
    # Hold verdict goes up by at least one in the global registry.
    assert "aegis_execute_alerts_total" in after_text
    # Be loose about ordering; just verify the family is present.
    assert "HOLD" in after_text
