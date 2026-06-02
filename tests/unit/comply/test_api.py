"""Tests for the optional FastAPI router (skipped if FastAPI is unavailable)."""

from __future__ import annotations

import pytest

from aegis.comply.api import router_available

if not router_available:  # pragma: no cover - exercised only without fastapi
    pytest.skip("fastapi not installed", allow_module_level=True)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aegis.comply.api import build_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(build_router())
    return TestClient(app)


def test_health_endpoint(client):
    resp = client.get("/comply/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["rules_loaded"] >= 10


def test_check_endpoint_clear(client):
    resp = client.post("/comply/check", json={"trend_id": "api-1", "title": "plain mug", "price": 10.0})
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "clear"


def test_check_endpoint_block(client):
    resp = client.post(
        "/comply/check",
        json={"trend_id": "api-2", "title": "gummies", "claims": ["cures diabetes"]},
    )
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "block"


def test_rules_endpoint_filter(client):
    resp = client.get("/comply/rules", params={"jurisdiction": "EU"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    assert all(r["jurisdiction"] == "EU" for r in body["rules"])
