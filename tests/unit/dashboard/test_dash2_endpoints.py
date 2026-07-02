"""DASH-2 / DASH-3 / ENV-2 / ENV-3 — dashboard backend behavioural tests.

Endpoints are exercised as plain async functions with the module-level Redis /
PG helpers monkeypatched — no live infrastructure, no TestClient lifespan.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from aegis.dashboard import app as dash

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeRedis:
    """Covers xrevrange (phase streams) + xinfo_stream (health endpoint)."""

    def __init__(
        self,
        *,
        entries: list[tuple[str, dict[str, str]]] | None = None,
        xinfo: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._entries = entries or []
        self._xinfo = xinfo or {}
        self.xrevrange_calls: list[tuple[str, int]] = []

    async def xrevrange(self, stream: str, count: int = 10) -> list[Any]:
        self.xrevrange_calls.append((stream, count))
        return self._entries

    async def xinfo_stream(self, stream: str) -> dict[str, Any]:
        if stream in self._xinfo:
            return self._xinfo[stream]
        raise Exception("ERR no such key")


def _use_redis(monkeypatch: pytest.MonkeyPatch, fake: _FakeRedis) -> None:
    monkeypatch.setattr(dash, "_get_redis", lambda: fake)


# --------------------------------------------------------------------------- #
# DASH-2: stream-backed endpoints
# --------------------------------------------------------------------------- #


async def test_swarm_recent_parses_body_field(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"run_id": "r1", "total_signals": 42})
    _use_redis(monkeypatch, _FakeRedis(entries=[("1-1", {"body": body})]))
    out = await dash.swarm_recent(limit=5)
    assert out[0]["run_id"] == "r1"
    assert out[0]["total_signals"] == 42
    assert out[0]["stream_id"] == "1-1"


async def test_anomalies_recent_empty_stream_returns_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_redis(monkeypatch, _FakeRedis(entries=[]))
    assert await dash.anomalies_recent(limit=5) == []


async def test_anomalies_recent_redis_down_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(dash, "_get_redis", _boom)
    assert await dash.anomalies_recent(limit=5) == []


async def test_stream_health_traffic_lights(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    fresh_ms = int(time.time() * 1000) - 5_000        # 5s old → healthy
    stale_ms = int(time.time() * 1000) - 7_200_000    # 2h old → stale
    fake = _FakeRedis(
        xinfo={
            "aegis:phase2:graph_results": {
                "length": 100, "last-generated-id": f"{fresh_ms}-0",
            },
            "aegis:swarm:results": {
                "length": 7, "last-generated-id": f"{stale_ms}-0",
            },
        }
    )
    _use_redis(monkeypatch, fake)
    out = await dash.stream_health()
    assert out["aegis:phase2:graph_results"]["status"] == "healthy"
    assert out["aegis:swarm:results"]["status"] == "stale"
    # streams missing from Redis report "empty", not an error
    assert out["aegis:scrape:anomalies"]["status"] == "empty"
    # every canonical stream is present in the report
    assert set(out) == set(dash._CANONICAL_STREAMS)


# --------------------------------------------------------------------------- #
# DASH-2: capital + DR endpoints
# --------------------------------------------------------------------------- #


async def test_capital_recent_maps_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    row = {
        "plan_id": "11111111-1111-1111-1111-111111111111",
        "trend_id": "t-1",
        "execution_mode": "advisory",
        "quantity": 5,
        "fulfillment_method": "pod",
        "total_capital_usd": 100.0,
        "estimated_profit_usd": 25.0,
        "kelly_fraction_used": 0.25,
        "risk_score": 0.2,
        "requires_approval": True,
        "status": "pending",
        "created_at": datetime(2026, 6, 11, tzinfo=UTC),
    }

    class _Conn:
        async def fetch(self, *_a: Any) -> list[dict[str, Any]]:
            return [row]

    @asynccontextmanager
    async def _fake_acquire(_dsn: str, timeout: float = 3.0) -> Any:
        yield _Conn()

    monkeypatch.setattr(dash, "_acquire_pg", _fake_acquire)
    out = await dash.capital_recent(limit=5)
    assert out["status"] == "ok"
    plan = out["plans"][0]
    assert plan["plan_status"] == "pending"
    assert plan["kelly_fraction_used"] == 0.25
    assert plan["created_at"].startswith("2026-06-11")


async def test_capital_recent_degrades_when_table_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def _fake_acquire(_dsn: str, timeout: float = 3.0) -> Any:
        raise RuntimeError('relation "execution_plans" does not exist')
        yield  # pragma: no cover

    monkeypatch.setattr(dash, "_acquire_pg", _fake_acquire)
    out = await dash.capital_recent()
    assert out["status"] == "error"
    assert out["plans"] == []


async def test_dr_status_unavailable_when_module_absent() -> None:
    # aegis-phase15 is a standalone venv — not importable here by design.
    out = await dash.dr_status()
    assert out["status"] in ("unavailable", "ok", "error")
    if out["status"] == "unavailable":
        assert "not installed" in out["message"]


# --------------------------------------------------------------------------- #
# ENV-2: ops token startup enforcement
# --------------------------------------------------------------------------- #


def _cfg_stub(env: str, token: Any) -> SimpleNamespace:
    return SimpleNamespace(env=env, dashboard_ops_token=token)


def test_ops_token_required_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_cfg", _cfg_stub("prod", None))
    with pytest.raises(RuntimeError, match="AEGIS_DASHBOARD_OPS_TOKEN"):
        dash._require_ops_token_in_prod()


def test_ops_token_required_in_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_cfg", _cfg_stub("staging", None))
    with pytest.raises(RuntimeError):
        dash._require_ops_token_in_prod()


def test_ops_token_optional_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_cfg", _cfg_stub("dev", None))
    dash._require_ops_token_in_prod()  # no raise


def test_ops_token_set_in_prod_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_cfg", _cfg_stub("prod", "sekret"))
    dash._require_ops_token_in_prod()  # no raise


# --------------------------------------------------------------------------- #
# ENV-3: LLM startup probe — never raises, warns on unreachable
# --------------------------------------------------------------------------- #


async def test_llm_probe_handles_missing_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    import aegis.agents.llm as agents_llm

    monkeypatch.setattr(agents_llm, "get_gateway", None)
    await dash._probe_llm_backends()  # must not raise


async def test_llm_probe_all_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    import aegis.agents.llm as agents_llm

    class _Gw:
        async def health(self) -> dict[str, bool]:
            return {"ollama": False, "groq": False}

    async def _get_gateway() -> _Gw:
        return _Gw()

    monkeypatch.setattr(agents_llm, "get_gateway", _get_gateway)
    await dash._probe_llm_backends()  # must not raise


async def test_llm_probe_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    import aegis.agents.llm as agents_llm

    async def _get_gateway() -> Any:
        raise ConnectionError("no provider reachable")

    monkeypatch.setattr(agents_llm, "get_gateway", _get_gateway)
    await dash._probe_llm_backends()  # must not raise


# --------------------------------------------------------------------------- #
# PASS6-6B: deep research job (ResearchEngine) endpoint
# --------------------------------------------------------------------------- #


async def test_deep_research_start_returns_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_redis(monkeypatch, _FakeRedis())
    body = dash._DeepResearchRequest(topic="wireless earbuds", depth="surface")
    out = await dash.deep_research_start(body)
    assert "job_id" in out
    job = dash._research_jobs[out["job_id"]]
    assert job["status"] == "running"
    # cancel the spawned task so it doesn't leak into other tests
    task = job.get("_task")
    if task is not None:
        task.cancel()


async def test_deep_research_start_rejects_empty_topic() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await dash.deep_research_start(dash._DeepResearchRequest(topic="   "))


async def test_run_deep_research_job_emits_report(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_redis(monkeypatch, _FakeRedis())

    class _FakeReport:
        def to_dict(self) -> dict[str, Any]:
            return {"query": "x", "trend_verdict": "emerging", "signal_count": 7}

    class _FakeEngine:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def research(self, topic: str, **kwargs: Any) -> _FakeReport:
            return _FakeReport()

    import aegis.intelligence.research_engine as re_mod

    monkeypatch.setattr(re_mod, "ResearchEngine", _FakeEngine)

    job_id = "deepjob01"
    dash._research_jobs[job_id] = {"status": "running", "events": [], "_started_dt": None}
    await dash._run_deep_research_job(job_id, "x", "deep", 50)

    job = dash._research_jobs[job_id]
    assert job["status"] == "done"
    assert job["result"]["trend_verdict"] == "emerging"
    assert any(e["type"] == "result" for e in job["events"])


async def test_run_deep_research_job_handles_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_redis(monkeypatch, _FakeRedis())

    class _BoomEngine:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def research(self, topic: str, **kwargs: Any) -> Any:
            raise RuntimeError("scrape exploded")

    import aegis.intelligence.research_engine as re_mod

    monkeypatch.setattr(re_mod, "ResearchEngine", _BoomEngine)

    job_id = "deepjob02"
    dash._research_jobs[job_id] = {"status": "running", "events": [], "_started_dt": None}
    await dash._run_deep_research_job(job_id, "x", "deep", 50)

    job = dash._research_jobs[job_id]
    assert job["status"] == "error"
    assert any(e["type"] == "error" for e in job["events"])
