"""Extra API tests to lift coverage on killswitch/health/stream routes."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest
from aegis.execute.api import build_app
from aegis.execute.bridge.types import ComposerInput
from aegis.execute.bus import EventBus
from aegis.execute.config import ExecuteSettings, set_execute_settings
from aegis.execute.killswitch.switch import KillSwitch
from aegis.execute.pipeline import Pipeline
from aegis.execute.schemas.alert import DeliveryAttempt, DeliveryStatus
from aegis.execute.schemas.dashboard import SSEEvent


@pytest.fixture
def settings() -> ExecuteSettings:
    s = ExecuteSettings(api_bearer_token="")
    set_execute_settings(s)
    return s


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


# ---------------------------------------------------------------------------
# Killswitch trip/arm/state with a stub Redis client
# ---------------------------------------------------------------------------
class _StubRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, name):
        return self.store.get(name)

    async def set(self, name, value):
        self.store[name] = value
        return True

    async def delete(self, *names):
        n = 0
        for k in names:
            if k in self.store:
                self.store.pop(k)
                n += 1
        return n


async def test_killswitch_trip_then_arm_via_api(fake_repo, settings):
    rc = _StubRedis()
    ks = KillSwitch(redis_client=rc)
    app = build_app(settings=settings, repo=fake_repo, killswitch=ks)
    async with await _client(app) as c:
        # Initial state ARMED
        r1 = await c.get("/killswitch")
        assert r1.json()["state"] == "ARMED"
        # Trip
        r2 = await c.post("/killswitch/trip", json={"reason": "incident-7"})
        assert r2.status_code == 200
        assert r2.json()["state"] == "TRIPPED"
        # Confirm
        r3 = await c.get("/killswitch")
        assert r3.json()["state"] == "TRIPPED"
        # Arm
        r4 = await c.post("/killswitch/arm", json={"reason": "all clear"})
        assert r4.status_code == 200
        assert r4.json()["state"] == "ARMED"


async def test_killswitch_trip_requires_reason(fake_repo, settings):
    rc = _StubRedis()
    ks = KillSwitch(redis_client=rc)
    app = build_app(settings=settings, repo=fake_repo, killswitch=ks)
    async with await _client(app) as c:
        r = await c.post("/killswitch/trip", json={})
        assert r.status_code == 422  # missing required field


# ---------------------------------------------------------------------------
# Deliveries endpoint
# ---------------------------------------------------------------------------
async def test_deliveries_endpoint_returns_history(fake_repo, settings):
    tid = uuid4()
    # Seed an alert
    pipe = Pipeline(repository=fake_repo)
    out = await pipe.submit(
        ComposerInput(
            tenant_id=tid,
            trend_id="t-deliv",
            phase2_verdict="HOLD",
            phase2_score=0.5,
            phase2_confidence=0.6,
            phase2_priority=2,
        )
    )
    aid = out.alert.alert_id
    # Insert two synthetic delivery attempts
    for n in (1, 2):
        await fake_repo.insert_delivery(
            tenant_id=str(tid),
            attempt=DeliveryAttempt(
                alert_id=aid,
                channel="log",
                attempt_no=n,
                status=DeliveryStatus.SUCCESS,
                latency_ms=1.0 * n,
            ),
        )
    app = build_app(settings=settings, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get(
            f"/alerts/{aid}/deliveries",
            headers={"X-Aegis-Tenant": str(tid)},
        )
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 2
        attempt_nos = sorted(row["attempt_no"] for row in rows)
        assert attempt_nos == [1, 2]


# ---------------------------------------------------------------------------
# Metrics endpoint (degrades to 204 without prometheus_client)
# ---------------------------------------------------------------------------
async def test_metrics_endpoint_returns_something(fake_repo, settings):
    app = build_app(settings=settings, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get("/metrics")
        # Either 200 (with prometheus_client) or 204 (without).
        assert r.status_code in (200, 204)


# ---------------------------------------------------------------------------
# SSE encoding + iterator (unit-level — bypasses ASGI streaming buffering)
# ---------------------------------------------------------------------------
async def test_sse_iterator_emits_connect_and_event(fake_repo, settings):
    """Drive the SSE iterator directly to validate frame encoding.

    We avoid `httpx.stream` over ASGITransport because that transport
    buffers the entire response body until the generator completes,
    which never happens for a long-lived SSE endpoint.
    """

    from aegis.execute.api.routes.stream import _event_iterator, _format_sse
    from aegis.execute.bus import EventBus

    bus = EventBus()

    # Minimal fake Request that never reports disconnected.
    class _FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    fr = _FakeRequest()
    gen = _event_iterator(bus, fr)  # type: ignore[arg-type]

    # First chunk is the connect comment.
    first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert first == b": connected\n\n"

    # Publish and read the next chunk.
    await bus.publish(SSEEvent(event="alert.created", data={"trend_id": "sse-test"}))
    second = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    decoded = second.decode("utf-8")
    assert "event: alert.created" in decoded
    assert "sse-test" in decoded

    # Format helper is deterministic.
    framed = _format_sse(SSEEvent(event="x", data={"k": 1}))
    assert framed.startswith(b"event: x\n")
    assert b'"k": 1' in framed or b'"k":1' in framed


async def test_stream_route_is_registered(fake_repo, settings):
    """The /stream route should appear in the app's route table."""
    bus = EventBus()
    app = build_app(settings=settings, repo=fake_repo, bus=bus)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/stream" in paths
