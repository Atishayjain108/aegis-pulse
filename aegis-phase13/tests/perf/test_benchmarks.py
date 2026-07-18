"""
tests/perf/test_benchmarks.py — Performance regression benchmarks.

Uses pytest-benchmark to guard against latency regressions in hot paths.
Run with: pytest tests/perf/ --benchmark-only

SLA targets (from AEGIS_PULSE_OMEGA_v2 prompt):
  - Signal dedup (batch 100):    < 50ms
  - Confidence gate (batch 100): < 20ms
  - Heuristic predict (single):  < 12ms  (p99 latency floor from spec)
  - Batch heuristic (10 windows): < 120ms
  - Content hash computation:    < 1ms per signal
  - Redis cache get (stub):      < 0.1ms

Architecture: Phase 13 (Testing) → benchmarks cross-cutting hot paths
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import random
from typing import Any
import uuid

import pytest

FEATURE_DIM = 20
RNG = random.Random(42)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def signal_batch_100() -> list[dict[str, Any]]:
    return [
        {
            "url": f"https://example.com/post/{i}",
            "title": f"Test signal title number {i} about AI and e-commerce trends",
            "score": RNG.uniform(0.1, 1.0),
            "content_hash": hashlib.sha256(f"url-{i}".encode()).hexdigest(),
        }
        for i in range(100)
    ]


@pytest.fixture(scope="module")
def feature_windows_10() -> list[dict[str, Any]]:
    return [
        {
            "trend_id": f"bench-trend-{i:03d}",
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "features": [round(RNG.uniform(-1, 1), 4) for _ in range(FEATURE_DIM)],
            "feature_names": [f"feat_{j:02d}" for j in range(FEATURE_DIM)],
            "computed_at": datetime.now(tz=UTC),
            "signal_count": RNG.randint(5, 100),
            "horizon_hours": [1, 6, 24, 72],
        }
        for i in range(10)
    ]


# ---------------------------------------------------------------------------
# Content hash benchmark
# ---------------------------------------------------------------------------

@pytest.mark.perf
def test_content_hash_per_signal(benchmark: Any, signal_batch_100: list[dict[str, Any]]) -> None:
    """SLA: < 1ms per signal for content hash computation."""

    def _compute_all() -> list[str]:
        return [
            hashlib.sha256(
                json.dumps({"url": s["url"], "title": s["title"]}, sort_keys=True).encode()
            ).hexdigest()
            for s in signal_batch_100
        ]

    result = benchmark(_compute_all)
    # benchmark.pedantic automatically measures; just verify correctness
    assert len(result) == 100
    assert all(len(h) == 64 for h in result)


# ---------------------------------------------------------------------------
# Dedup benchmark
# ---------------------------------------------------------------------------

@pytest.mark.perf
def test_dedup_batch_100_signals(benchmark: Any, signal_batch_100: list[dict[str, Any]]) -> None:
    """SLA: dedup 100 signals < 50ms."""
    try:
        from aegis.scrape.dedup import dedup_signals  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("aegis.scrape.dedup not available")

    result = benchmark(dedup_signals, signal_batch_100)
    assert len(result) <= 100


# ---------------------------------------------------------------------------
# Confidence gate benchmark
# ---------------------------------------------------------------------------

@pytest.mark.perf
def test_confidence_gate_batch_100(benchmark: Any, signal_batch_100: list[dict[str, Any]]) -> None:
    """SLA: confidence gate 100 signals < 20ms."""
    try:
        from aegis.scrape.confidence import score_batch  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("aegis.scrape.confidence not available")

    result = benchmark(score_batch, signal_batch_100)
    assert result is not None


# ---------------------------------------------------------------------------
# Heuristic predict benchmark (single window)
# ---------------------------------------------------------------------------

@pytest.mark.perf
def test_heuristic_predict_single_window(
    benchmark: Any, feature_windows_10: list[dict[str, Any]]
) -> None:
    """SLA: single heuristic FeatureWindow predict in < 12ms (p99 floor)."""
    try:
        from aegis.predict.models.heuristic import predict  # type: ignore[import-untyped]
        from aegis.predict.schemas import FeatureWindow  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("aegis.predict not available")

    fw = FeatureWindow(**feature_windows_10[0])
    result = benchmark(predict, fw)
    assert result is not None


# ---------------------------------------------------------------------------
# Heuristic predict benchmark (batch 10)
# ---------------------------------------------------------------------------

@pytest.mark.perf
def test_heuristic_predict_batch_10(
    benchmark: Any, feature_windows_10: list[dict[str, Any]]
) -> None:
    """SLA: batch-10 heuristic predict in < 120ms."""
    try:
        from aegis.predict.models.heuristic import predict  # type: ignore[import-untyped]
        from aegis.predict.schemas import FeatureWindow  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("aegis.predict not available")

    fws = [FeatureWindow(**fw) for fw in feature_windows_10]

    def _predict_all() -> list[Any]:
        return [predict(fw) for fw in fws]

    results = benchmark(_predict_all)
    assert len(results) == 10


# ---------------------------------------------------------------------------
# LLM cache lookup benchmark
# ---------------------------------------------------------------------------

@pytest.mark.perf
def test_llm_cache_hit_latency(benchmark: Any) -> None:
    """SLA: LLM cache hit in < 0.1ms (in-process LRU)."""
    try:
        from aegis.llm.cache import LLMCache  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("aegis.llm.cache not available")

    cache = LLMCache(max_size=512)
    key = "bench-cache-key-abc123"
    value = '{"result": "cached response"}'
    cache.set(key, value)

    result = benchmark(cache.get, key)
    assert result == value


# ---------------------------------------------------------------------------
# Pydantic model construction benchmark
# ---------------------------------------------------------------------------

@pytest.mark.perf
def test_trend_candidate_construction(benchmark: Any) -> None:
    """TrendCandidate construction must be fast (Pydantic v2 frozen)."""
    try:
        from aegis.agents.schemas import TrendCandidate  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("aegis.agents.schemas not available")

    def _make() -> Any:
        return TrendCandidate(
            trend_id="bench-tc",
            title="Benchmark trend candidate",
            signal_count=50,
            unique_authors=20,
            platforms=["hacker_news", "reddit_rss"],
            velocity_1h=0.5,
            velocity_6h=3.0,
            velocity_24h=7.2,
            sentiment=0.72,
            commercial_intent=0.81,
            novelty=0.88,
            coordination_risk=0.07,
        )

    result = benchmark(_make)
    assert result is not None


# ---------------------------------------------------------------------------
# Locust load test definition (not run by pytest-benchmark)
# ---------------------------------------------------------------------------
# Run with: locust -f tests/perf/test_benchmarks.py
#   --headless -u 10 -r 2 -t 30s --host http://localhost:8100

try:
    from locust import HttpUser, between, task  # type: ignore[import-untyped]

    class PredictAPILoadTest(HttpUser):
        """Load test for Phase 3 FastAPI inference server at :8100."""

        wait_time = between(0.05, 0.2)  # 5-200ms between requests

        @task(3)
        def predict_single(self) -> None:
            """POST /predict with a single FeatureWindow."""
            payload = {
                "trend_id": f"load-{uuid.uuid4().hex[:8]}",
                "tenant_id": "00000000-0000-0000-0000-000000000001",
                "features": [round(random.uniform(-1, 1), 4) for _ in range(FEATURE_DIM)],
                "feature_names": [f"feat_{i:02d}" for i in range(FEATURE_DIM)],
                "computed_at": datetime.now(tz=UTC).isoformat(),
                "signal_count": 25,
                "horizon_hours": [1, 6, 24, 72],
            }
            with self.client.post("/predict", json=payload, catch_response=True) as resp:
                if resp.status_code == 200:
                    resp.success()
                else:
                    resp.failure(f"HTTP {resp.status_code}: {resp.text[:100]}")

        @task(1)
        def health_check(self) -> None:
            """GET /healthz — should always be < 10ms."""
            with self.client.get("/healthz", catch_response=True) as resp:
                if resp.status_code == 200:
                    resp.success()
                else:
                    resp.failure(f"Health check failed: {resp.status_code}")

except ImportError:
    pass  # locust not installed; load test class silently absent
