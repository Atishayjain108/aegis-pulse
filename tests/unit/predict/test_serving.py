"""Tests for `aegis.predict.serving.app`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from aegis.predict.serving import create_app  # noqa: E402


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def signals_iso():
    base = datetime.now(UTC) - timedelta(hours=72)
    return [
        {
            "id": f"sig-{i}",
            "platform": "twitter",
            "captured_at": (base + timedelta(hours=i)).isoformat(),
            "title": None,
            "body": f"msg {i}",
            "url": None,
            "content_hash": f"h{i}",
            "author_id": f"a{i % 5}",
            "views": i * 10,
            "likes": i,
            "comments": i // 2,
            "shares": 0,
            "saves": 0,
            "sentiment": 0.0,
            "commercial_intent": 0.0,
            "novelty": 0.5,
        }
        for i in range(72)
    ]


class TestHealth:
    def test_healthz(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_readyz(self, client):
        r = client.get("/readyz")
        assert r.status_code == 200


class TestPredict:
    def test_predict_with_signals(self, client, signals_iso):
        r = client.post(
            "/predict",
            json={"tenant_id": "t1", "trend_id": "trend-1", "signals": signals_iso},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["bundle"]["predictions"]
        assert "correlation_id" in body
        assert body["duration_ms"] > 0

    def test_predict_rejects_both_inputs(self, client, signals_iso):
        r = client.post(
            "/predict",
            json={
                "tenant_id": "t1",
                "trend_id": "trend-1",
                "signals": signals_iso,
                "feature_window": {"foo": "bar"},
            },
        )
        assert r.status_code == 400

    def test_predict_rejects_neither_input(self, client):
        r = client.post("/predict", json={"tenant_id": "t1", "trend_id": "trend-1"})
        assert r.status_code == 400

    def test_predict_correlation_id_propagated(self, client, signals_iso):
        r = client.post(
            "/predict",
            headers={"x-correlation-id": "trace-abc-123"},
            json={"tenant_id": "t1", "trend_id": "trend-1", "signals": signals_iso},
        )
        assert r.status_code == 200
        assert r.headers["x-correlation-id"] == "trace-abc-123"
        body = r.json()
        # Body's correlation_id always reflects the request id.
        assert body["correlation_id"] == "trace-abc-123"

    def test_invalid_iso_datetime_returns_400(self, client, signals_iso):
        signals_iso[0]["captured_at"] = "not a date"
        r = client.post(
            "/predict",
            json={"tenant_id": "t1", "trend_id": "trend-1", "signals": signals_iso},
        )
        assert r.status_code == 400


class TestBatchPredict:
    def test_batch_predict(self, client, signals_iso):
        body = {
            "requests": [
                {"tenant_id": "t1", "trend_id": "trend-A", "signals": signals_iso},
                {"tenant_id": "t1", "trend_id": "trend-B", "signals": signals_iso},
            ]
        }
        r = client.post("/predict/batch", json=body)
        assert r.status_code == 200
        responses = r.json()["responses"]
        assert len(responses) == 2
        assert responses[0]["bundle"]["trend_id"] != responses[1]["bundle"]["trend_id"]

    def test_batch_size_cap_enforced(self, client, signals_iso):
        # PREDICT_BATCH_MAX = 32 in constants.
        too_many = {
            "requests": [
                {"tenant_id": "t1", "trend_id": f"trend-{i}", "signals": signals_iso}
                for i in range(33)
            ]
        }
        r = client.post("/predict/batch", json=too_many)
        assert r.status_code == 422  # pydantic validation


class TestMetrics:
    def test_metrics_endpoint(self, client):
        r = client.get("/metrics")
        # When prometheus_client is installed, returns text/plain;
        # otherwise returns the disabled JSON.
        assert r.status_code == 200
