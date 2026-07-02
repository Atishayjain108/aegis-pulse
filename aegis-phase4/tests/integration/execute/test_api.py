"""FastAPI endpoint tests using httpx.ASGITransport."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from aegis.execute.api import build_app
from aegis.execute.bridge.types import ComposerInput
from aegis.execute.config import ExecuteSettings, set_execute_settings
from aegis.execute.killswitch.switch import KillSwitch
from aegis.execute.pipeline import Pipeline


def _settings_with_bearer(token: str = "") -> ExecuteSettings:
    s = ExecuteSettings(api_bearer_token=token)
    set_execute_settings(s)
    return s


async def _seed(repo, *, tenant_id):
    pipe = Pipeline(repository=repo)
    ci = ComposerInput(
        tenant_id=tenant_id,
        trend_id="t-api",
        phase2_verdict="ENTER",
        phase2_score=0.80,
        phase2_confidence=0.70,
        phase2_priority=1,
        phase3_p_breakout_24h=0.85,
        phase3_p_decline_6h=0.05,
        phase3_confidence=0.70,
        phase3_policy_action="enter",
        phase3_expected_margin_usd=3.50,
        phase3_loss_probability=0.15,
    )
    outcome = await pipe.submit(ci, unit_cost_usd=1.5)
    return outcome.alert


@pytest.fixture
def settings_no_auth() -> ExecuteSettings:
    return _settings_with_bearer("")


@pytest.fixture
def settings_with_auth() -> ExecuteSettings:
    return _settings_with_bearer("super-secret")


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_healthz_returns_ok(fake_repo, settings_no_auth):
    app = build_app(settings=settings_no_auth, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


async def test_readyz_includes_killswitch(fake_repo, settings_no_auth):
    ks = KillSwitch(redis_client=None, fail_closed=False)
    app = build_app(settings=settings_no_auth, repo=fake_repo, killswitch=ks)
    async with await _client(app) as c:
        r = await c.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["killswitch"] == "ARMED"
        assert body["mode"] == "advisory"


async def test_root_returns_metadata(fake_repo, settings_no_auth):
    app = build_app(settings=settings_no_auth, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get("/")
        body = r.json()
        assert body["service"] == "aegis-execute"
        assert body["version"] == "0.4.0"


async def test_list_alerts_requires_tenant_header(fake_repo, settings_no_auth):
    app = build_app(settings=settings_no_auth, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get("/alerts")
        assert r.status_code == 400  # missing X-Aegis-Tenant


async def test_list_alerts_returns_seeded(fake_repo, settings_no_auth):
    tid = uuid4()
    await _seed(fake_repo, tenant_id=tid)
    app = build_app(settings=settings_no_auth, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get("/alerts", headers={"X-Aegis-Tenant": str(tid)})
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["verdict"] == "ENTER"
        assert rows[0]["trend_id"] == "t-api"


async def test_get_alert_404_when_absent(fake_repo, settings_no_auth):
    app = build_app(settings=settings_no_auth, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get(
            "/alerts/nonexistent",
            headers={"X-Aegis-Tenant": str(uuid4())},
        )
        assert r.status_code == 404


async def test_get_alert_200_when_present(fake_repo, settings_no_auth):
    tid = uuid4()
    alert = await _seed(fake_repo, tenant_id=tid)
    app = build_app(settings=settings_no_auth, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get(
            f"/alerts/{alert.alert_id}",
            headers={"X-Aegis-Tenant": str(tid)},
        )
        assert r.status_code == 200
        assert r.json()["alert_id"] == alert.alert_id


async def test_snapshot_payload(fake_repo, settings_no_auth):
    tid = uuid4()
    await _seed(fake_repo, tenant_id=tid)
    ks = KillSwitch(redis_client=None, fail_closed=False)
    app = build_app(settings=settings_no_auth, repo=fake_repo, killswitch=ks)
    async with await _client(app) as c:
        r = await c.get("/snapshot", headers={"X-Aegis-Tenant": str(tid)})
        assert r.status_code == 200
        body = r.json()
        assert body["killswitch_state"] == "ARMED"
        assert body["pending_outbox"] >= 1
        assert any(c["label"] == "Pending outbox" for c in body["cards"])
        assert len(body["recent_alerts"]) >= 1


async def test_bearer_auth_required_when_token_set(fake_repo, settings_with_auth):
    app = build_app(settings=settings_with_auth, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get("/alerts", headers={"X-Aegis-Tenant": str(uuid4())})
        assert r.status_code == 401


async def test_bearer_auth_accepts_correct_token(fake_repo, settings_with_auth):
    tid = uuid4()
    await _seed(fake_repo, tenant_id=tid)
    app = build_app(settings=settings_with_auth, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get(
            "/alerts",
            headers={
                "X-Aegis-Tenant": str(tid),
                "Authorization": "Bearer super-secret",
            },
        )
        assert r.status_code == 200


async def test_bearer_auth_rejects_wrong_token(fake_repo, settings_with_auth):
    app = build_app(settings=settings_with_auth, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.get(
            "/alerts",
            headers={
                "X-Aegis-Tenant": str(uuid4()),
                "Authorization": "Bearer wrong",
            },
        )
        assert r.status_code == 401


async def test_ack_marks_outbox_delivered(fake_repo, settings_no_auth):
    tid = uuid4()
    alert = await _seed(fake_repo, tenant_id=tid)
    app = build_app(settings=settings_no_auth, repo=fake_repo)
    async with await _client(app) as c:
        r = await c.post(
            f"/alerts/{alert.alert_id}/ack",
            headers={"X-Aegis-Tenant": str(tid)},
        )
        assert r.status_code == 200
        assert r.json()["acked"] is True
    assert fake_repo.outbox[alert.alert_id]["status"] == "delivered"


async def test_killswitch_state_endpoint(fake_repo, settings_no_auth):
    ks = KillSwitch(redis_client=None, fail_closed=False)
    app = build_app(settings=settings_no_auth, repo=fake_repo, killswitch=ks)
    async with await _client(app) as c:
        r = await c.get("/killswitch")
        assert r.status_code == 200
        assert r.json()["state"] == "ARMED"
