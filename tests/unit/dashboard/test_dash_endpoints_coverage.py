"""Coverage-focused behavioural tests for dashboard endpoint handlers.

Every handler is exercised as a plain async function with the module-level
Redis / PG / docker / httpx helpers monkeypatched — no live infrastructure.
Targets the large uncovered surface of ``aegis.dashboard.app``.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from aegis.dashboard import app as dash

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeRedis:
    def __init__(
        self,
        *,
        entries: list[tuple[str, dict[str, str]]] | None = None,
        kv: dict[str, str] | None = None,
        hashes: dict[str, dict[str, str]] | None = None,
        info: dict[str, Any] | None = None,
    ) -> None:
        self._entries = entries or []
        self._kv = kv or {}
        self._hashes = hashes or {}
        self._info = info or {"used_memory_human": "1M", "connected_clients": 2}

    async def xrevrange(self, stream: str, count: int = 10) -> list[Any]:
        return self._entries

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def hgetall(self, key: str) -> dict[str, str]:
        return self._hashes.get(key, {})

    async def ping(self) -> bool:
        return True

    async def info(self) -> dict[str, Any]:
        return self._info

    async def xlen(self, stream: str) -> int:
        return 5

    async def set(self, key: str, val: str) -> None:
        self._kv[key] = val

    async def xadd(self, stream: str, fields: dict[str, str], **_kw: Any) -> str:
        return "1-1"


class _FakeConn:
    def __init__(
        self,
        *,
        fetchval: Any = 0,
        fetch: list[dict[str, Any]] | None = None,
        fetchrow: dict[str, Any] | None = None,
    ) -> None:
        self._fetchval = fetchval
        self._fetch = fetch or []
        self._fetchrow = fetchrow

    async def fetchval(self, *_a: Any) -> Any:
        return self._fetchval

    async def fetch(self, *_a: Any) -> list[dict[str, Any]]:
        return self._fetch

    async def fetchrow(self, *_a: Any) -> dict[str, Any] | None:
        return self._fetchrow

    async def execute(self, *_a: Any) -> str:
        return "OK"


def _patch_pg(monkeypatch: pytest.MonkeyPatch, conn: Any) -> None:
    @asynccontextmanager
    async def _fake_acquire(_dsn: str, timeout: float = 3.0) -> Any:
        yield conn

    monkeypatch.setattr(dash, "_acquire_pg", _fake_acquire)


def _patch_pg_boom(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def _boom(_dsn: str, timeout: float = 3.0) -> Any:
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(dash, "_acquire_pg", _boom)


# --------------------------------------------------------------------------- #
# /api/system
# --------------------------------------------------------------------------- #


async def test_system_health_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn(
        fetchval=42,
        fetch=[{"platform": "hacker_news", "n": 10}],
        fetchrow={"ts": datetime(2026, 6, 11, tzinfo=UTC)},
    )
    _patch_pg(monkeypatch, conn)
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis())

    async def _no_docker() -> list[dict[str, Any]]:
        return [{"name": "aegis-redis", "status": "running"}]

    monkeypatch.setattr(dash, "_docker_ps", _no_docker)
    out = await dash.system_health()
    assert out["postgres"]["status"] == "healthy"
    assert out["postgres"]["signal_count"] == 42
    assert out["redis"]["status"] == "healthy"
    assert out["docker"]["status"] == "ok"
    # predict/execute hit real httpx → error (no server), but must be present
    assert "predict" in out
    assert "execute" in out


async def test_system_health_degrades_on_pg_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pg_boom(monkeypatch)

    def _boom() -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(dash, "_get_redis", _boom)

    async def _docker_boom() -> list[dict[str, Any]]:
        raise RuntimeError("no docker")

    monkeypatch.setattr(dash, "_docker_ps", _docker_boom)
    out = await dash.system_health()
    assert out["postgres"]["status"] == "error"
    assert out["redis"]["status"] == "error"
    assert out["docker"]["status"] == "error"


# --------------------------------------------------------------------------- #
# /api/signals/stats + /recent
# --------------------------------------------------------------------------- #


async def test_signal_stats_maps_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    hr = datetime(2026, 6, 11, 5, tzinfo=UTC)
    # signal_stats issues several fetch calls (by_platform, hourly, sentiment_avg)
    # each returning the same list; one row carrying every referenced column works.
    conn = _FakeConn(
        fetchval=100,
        fetch=[
            {
                "platform": "hn",
                "n": 50,
                "hr": hr,
                "avg_sentiment": 0.5,
                "avg_commercial_intent": 0.8,
                "avg_novelty": 0.4,
            },
        ],
    )
    _patch_pg(monkeypatch, conn)
    out = await dash.signal_stats()
    assert out["total"] == 100
    assert "by_platform" in out
    assert "platform_metrics" in out


async def test_signal_stats_raises_503_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    _patch_pg_boom(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        await dash.signal_stats()
    assert ei.value.status_code == 503


async def test_signals_recent_all_and_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "signal_id": "s1",
        "platform": "hn",
        "title": "trending earbuds",
        "url": "http://x",
        "scraped_at": datetime(2026, 6, 11, tzinfo=UTC),
        "sentiment": 0.7,
        "commercial_intent": 0.9,
        "novelty": 0.5,
        "views": 100,
        "likes": 5,
        "comments": 3,
        "shares": 1,
    }
    _patch_pg(monkeypatch, _FakeConn(fetch=[row]))
    out_all = await dash.signals_recent(limit=10)
    assert out_all[0]["platform"] == "hn"
    assert out_all[0]["engagement"] == 9
    out_plat = await dash.signals_recent(limit=10, platform="hn")
    assert out_plat[0]["signal_id"] == "s1"


async def test_signals_recent_503_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    _patch_pg_boom(monkeypatch)
    with pytest.raises(HTTPException):
        await dash.signals_recent()


# --------------------------------------------------------------------------- #
# /api/agents/recent
# --------------------------------------------------------------------------- #


async def test_agents_recent_parses_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(
        {
            "trend_id": "t1",
            "final_verdict": "ENTER",
            "final_score": 0.82,
            "final_confidence": 0.7,
            "decisions": [
                {"agent": "scout", "verdict": "proceed", "score": 0.8, "confidence": 0.7},
            ],
            "llm_used": True,
            "reasoning_source": "llm",
        }
    )
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(entries=[("1-1", {"body": body})]))
    out = await dash.agents_recent(limit=5)
    assert out[0]["trend_id"] == "t1"
    assert out[0]["decisions"][0]["agent"] == "scout"
    assert out[0]["llm_used"] is True


async def test_agents_recent_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(dash, "_get_redis", _boom)
    assert await dash.agents_recent() == []


async def test_agents_recent_skips_bad_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dash, "_get_redis", lambda: _FakeRedis(entries=[("1-1", {"body": "not-json{"})])
    )
    assert await dash.agents_recent() == []


# --------------------------------------------------------------------------- #
# phase-stream endpoints (geo / compliance / evolve)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "handler",
    [dash.geo_recent, dash.compliance_recent, dash.evolve_recent],
)
async def test_phase_stream_endpoints(
    monkeypatch: pytest.MonkeyPatch, handler: Any
) -> None:
    body = json.dumps({"id": "x", "value": 1})
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(entries=[("9-1", {"body": body})]))
    out = await handler(limit=5)
    assert out[0]["stream_id"] == "9-1"
    assert out[0]["value"] == 1


async def test_read_phase_stream_skips_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dash, "_get_redis", lambda: _FakeRedis(entries=[("1-1", {"body": "{bad"})])
    )
    assert await dash._read_phase_stream("aegis:phase7:geo_opportunities", 5) == []


# --------------------------------------------------------------------------- #
# /api/search (semantic)
# --------------------------------------------------------------------------- #


async def test_semantic_search_empty_query() -> None:
    out = await dash.semantic_search(q="   ")
    assert out["available"] is False
    assert out["results"] == []


async def test_semantic_search_with_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegis.scrape.semantic_index import SemanticSearchResult

    class _Index:
        async def search(self, q: str, top_k: int = 10) -> list[SemanticSearchResult]:
            return [SemanticSearchResult(signal_id="s1", similarity=0.95, metadata={"k": "v"})]

    import aegis.scrape.semantic_index as si

    monkeypatch.setattr(si, "get_signal_index", lambda: _Index())
    out = await dash.semantic_search(q="earbuds", top_k=5)
    assert out["available"] is True
    assert out["results"][0]["signal_id"] == "s1"


async def test_semantic_search_swallows_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import aegis.scrape.semantic_index as si

    def _boom() -> Any:
        raise RuntimeError("faiss exploded")

    monkeypatch.setattr(si, "get_signal_index", _boom)
    out = await dash.semantic_search(q="earbuds")
    assert out["available"] is False


# --------------------------------------------------------------------------- #
# /api/swarm/latest + history + agents
# --------------------------------------------------------------------------- #


async def test_swarm_latest_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(kv={}))
    out = await dash.swarm_latest()
    assert out["status"] == "no_data"


async def test_swarm_latest_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"run_id": "r1"})
    monkeypatch.setattr(
        dash, "_get_redis", lambda: _FakeRedis(kv={"aegis:swarm:latest": payload})
    )
    out = await dash.swarm_latest()
    assert out["status"] == "ok"
    assert out["data"]["run_id"] == "r1"


async def test_swarm_latest_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(dash, "_get_redis", _boom)
    out = await dash.swarm_latest()
    assert out["status"] == "error"


async def test_swarm_history_maps_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "run_id": "r1",
        "started_at": datetime(2026, 6, 11, tzinfo=UTC),
        "finished_at": datetime(2026, 6, 11, 1, tzinfo=UTC),
        "total_signals": 100,
        "unique_signals": 90,
        "dedup_removed": 10,
        "market_pulse": "bullish",
        "batch_confidence": 0.88,
        "conclusion": "strong",
    }
    _patch_pg(monkeypatch, _FakeConn(fetch=[row]))
    out = await dash.swarm_history(limit=5)
    assert out["status"] == "ok"
    assert out["runs"][0]["run_id"] == "r1"


async def test_swarm_history_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pg_boom(monkeypatch)
    out = await dash.swarm_history()
    assert out["status"] == "error"
    assert out["runs"] == []


async def test_swarm_agents_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(hashes={}))
    out = await dash.swarm_agents()
    assert out["status"] == "no_data"


async def test_swarm_agents_ok_and_bad_value(monkeypatch: pytest.MonkeyPatch) -> None:
    hashes = {
        "aegis:swarm:agent_health": {
            "reddit": json.dumps({"state": "up"}),
            "broken": "not-json{",
        }
    }
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(hashes=hashes))
    out = await dash.swarm_agents()
    assert out["status"] == "ok"
    assert out["agents"]["reddit"]["state"] == "up"
    assert out["agents"]["broken"]["raw"] == "not-json{"


async def test_swarm_agents_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(dash, "_get_redis", _boom)
    out = await dash.swarm_agents()
    assert out["status"] == "error"


# --------------------------------------------------------------------------- #
# /api/docker/status + healthz + index
# --------------------------------------------------------------------------- #


async def test_docker_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _docker() -> list[dict[str, Any]]:
        return [{"name": "x", "status": "running"}]

    monkeypatch.setattr(dash, "_docker_ps", _docker)
    out = await dash.docker_status()
    assert out[0]["name"] == "x"


async def test_healthz() -> None:
    out = await dash.healthz()
    assert out["status"] == "ok"


async def test_index_returns_html() -> None:
    resp = await dash.index()
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Batch 2 — predictions / killswitch / llm / stats / adapters / pipeline
# --------------------------------------------------------------------------- #


async def test_predictions_recent_maps_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "prediction_id": "p1", "trend_id": "t1", "horizon_h": 24,
        "p_breakout": 0.6, "p_decline": 0.1, "p_hold": 0.3,
        "kelly_fraction": 0.25, "confidence": 0.7,
        "finished_at": datetime(2026, 6, 11, tzinfo=UTC),
    }
    _patch_pg(monkeypatch, _FakeConn(fetch=[row]))
    out = await dash.predictions_recent(limit=5)
    assert out[0]["prediction_id"] == "p1"
    assert out[0]["p_breakout"] == 0.6


async def test_predictions_recent_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pg_boom(monkeypatch)
    assert await dash.predictions_recent() == []


async def test_killswitch_state_armed_and_tripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(kv={}))
    out = await dash.killswitch_state()
    assert out["state"] == "ARMED"
    monkeypatch.setattr(
        dash, "_get_redis", lambda: _FakeRedis(kv={"aegis:execute:killswitch": "TRIPPED"})
    )
    out2 = await dash.killswitch_state()
    assert out2["tripped"] is True


async def test_killswitch_state_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(dash, "_get_redis", _boom)
    out = await dash.killswitch_state()
    assert out["state"] == "UNKNOWN"


async def test_killswitch_trip_and_arm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_verify_ops_token", lambda _t: None)
    fake = _FakeRedis(kv={})
    monkeypatch.setattr(dash, "_get_redis", lambda: fake)
    body = dash._KillswitchAction(reason="test")
    out_trip = await dash.killswitch_trip(body, x_ops_token=None)
    assert out_trip["state"] == "TRIPPED"
    out_arm = await dash.killswitch_arm(body, x_ops_token=None)
    assert out_arm["state"] == "ARMED"


async def test_llm_health_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import aegis.llm.bridge.agents_bridge as ab

    async def _gw() -> None:
        return None

    monkeypatch.setattr(ab, "get_gateway", _gw)
    out = await dash.llm_health_check()
    assert out["status"] == "unavailable"


async def test_llm_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    import aegis.llm.bridge.agents_bridge as ab

    class _Gw:
        async def health(self) -> dict[str, bool]:
            return {"ollama": True}

    async def _gw() -> _Gw:
        return _Gw()

    monkeypatch.setattr(ab, "get_gateway", _gw)
    out = await dash.llm_health_check()
    assert out["status"] == "ok"
    assert out["providers"]["ollama"] is True


async def test_agents_trend_history(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"trend_id": "t1", "final_verdict": "ENTER", "decisions": [{}, {}]})
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(entries=[("1-1", {"body": body})]))
    out = await dash.agents_trend_history(limit=10)
    assert out[0]["agent_count"] == 2


async def test_agents_trend_history_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("down")

    monkeypatch.setattr(dash, "_get_redis", _boom)
    assert await dash.agents_trend_history() == []


async def test_predictions_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    horizon = {
        "horizon_h": 24, "n": 5, "avg_breakout": 0.6,
        "avg_decline": 0.1, "avg_hold": 0.3, "avg_kelly": 0.7,
    }
    dist = {"bullish": 3, "bearish": 1, "neutral": 1}
    _patch_pg(monkeypatch, _FakeConn(fetchval=10, fetch=[horizon], fetchrow=dist))
    out = await dash.predictions_stats()
    assert out["total"] == 10
    assert out["verdict_distribution"]["bullish"] == 3


async def test_predictions_stats_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pg_boom(monkeypatch)
    out = await dash.predictions_stats()
    assert out["total"] == 0


async def test_execute_alerts_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    hr = datetime(2026, 6, 11, 5, tzinfo=UTC)
    row = {"verdict": "ENTER", "n": 4, "hr": hr}
    _patch_pg(monkeypatch, _FakeConn(fetchval=8, fetch=[row]))
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(kv={}))
    out = await dash.execute_alerts_stats()
    assert out["total"] == 8
    assert out["killswitch"] == "ARMED"


async def test_execute_alerts_stats_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pg_boom(monkeypatch)
    out = await dash.execute_alerts_stats()
    assert out["killswitch"] == "UNKNOWN"


async def test_datalake_status_runs() -> None:
    out = await dash.datalake_status()
    assert out["status"] in ("ok", "error", "not_installed", "unavailable")


async def test_signals_velocity(monkeypatch: pytest.MonkeyPatch) -> None:
    hr = datetime(2026, 6, 11, 5, tzinfo=UTC)
    rows = [{"platform": "hn", "hr": hr, "n": 7}]
    _patch_pg(monkeypatch, _FakeConn(fetch=rows))
    out = await dash.signals_velocity()
    assert out["status"] == "ok"
    assert out["current_rates"]["hn"] == 7


async def test_signals_velocity_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pg_boom(monkeypatch)
    out = await dash.signals_velocity()
    assert out["status"] == "error"


async def test_dashboard_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    allkeys = {
        "total": 100, "last_24h": 10, "last_1h": 2, "avg_conf": 0.5,
        "last_scraped": datetime(2026, 6, 11, tzinfo=UTC), "active_platforms": 4,
        "last_at": datetime(2026, 6, 11, tzinfo=UTC), "n": 3,
    }
    _patch_pg(monkeypatch, _FakeConn(fetchrow=allkeys, fetch=[{"v": "ENTER", "n": 5}]))
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(entries=[]))
    out = await dash.dashboard_stats()
    assert out["total_signals"] == 100
    assert out["verdict_distribution"]["ENTER"] == 5


async def test_dashboard_stats_pg_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pg_boom(monkeypatch)
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis(entries=[]))
    out = await dash.dashboard_stats()
    assert out["total_signals"] == 0  # defaults survive


async def test_signals_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"platform": "hn", "cnt": 50, "avg_conf": 0.6, "latest": datetime(2026, 6, 11, tzinfo=UTC)}
    _patch_pg(monkeypatch, _FakeConn(fetch=[row]))
    out = await dash.signals_platforms()
    assert out[0]["platform"] == "hn"
    assert out[0]["count"] == 50


async def test_signals_platforms_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pg_boom(monkeypatch)
    assert await dash.signals_platforms() == []


async def test_alerts_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "alert_id": "a1", "trend_id": "t1", "verdict": "enter", "priority": 1,
        "score": 0.8, "confidence": 0.7, "source": "phase2_and_phase3",
        "summary_text": "good", "halt_reason": None, "blocked_by": [],
        "p_breakout_24h": 0.6, "p_decline_6h": 0.1, "expected_margin_usd": 50.0,
        "created_at": datetime(2026, 6, 11, tzinfo=UTC),
    }
    _patch_pg(monkeypatch, _FakeConn(fetch=[row]))
    out = await dash.alerts_recent(limit=5)
    assert out[0]["alert_id"] == "a1"
    assert out[0]["verdict"] == "ENTER"


async def test_alerts_recent_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pg_boom(monkeypatch)
    assert await dash.alerts_recent() == []


async def test_adapters_status(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"platform": "hacker_news", "cnt_24h": 10, "latest": datetime.now(UTC)}
    # also include an unknown platform to exercise the extra-loop
    extra = {"platform": "mystery_src", "cnt_24h": 3, "latest": datetime.now(UTC)}
    _patch_pg(monkeypatch, _FakeConn(fetch=[row, extra]))
    out = await dash.adapters_status()
    names = {a["name"] for a in out}
    assert "hacker_news" in names
    assert "mystery_src" in names
    hn = next(a for a in out if a["name"] == "hacker_news")
    assert hn["status"] == "working"


async def test_adapters_status_db_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pg_boom(monkeypatch)
    out = await dash.adapters_status()
    # All known adapters still listed, marked dead
    assert any(a["name"] == "hacker_news" for a in out)
    assert all(a["status"] == "dead" for a in out)


async def test_pipeline_live(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"duration_ms": 5000, "finished_at": "2026-06-11T00:00:00Z"})
    fake = _FakeRedis(entries=[("1-1", {"body": body})], kv={})
    monkeypatch.setattr(dash, "_get_redis", lambda: fake)
    _patch_pg(monkeypatch, _FakeConn(fetchval=123))
    out = await dash.pipeline_live()
    assert out["db_signal_count"] == 123
    assert out["services"]["postgres"] is True
    assert out["services"]["redis"] is True
    assert out["stream_length"] == 5


# --------------------------------------------------------------------------- #
# watchlist + research history
# --------------------------------------------------------------------------- #


async def test_watchlist_add_list_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    store: list[dict[str, Any]] = []

    async def _load() -> list[dict[str, Any]]:
        return list(store)

    async def _save(wl: list[dict[str, Any]]) -> None:
        store.clear()
        store.extend(wl)

    monkeypatch.setattr(dash, "_load_watchlist", _load)
    monkeypatch.setattr(dash, "_save_watchlist", _save)

    add = await dash.watchlist_add(dash._WatchlistTopicBody(topic="AI chips"))
    assert add["ok"] is True
    # duplicate is a no-op
    dup = await dash.watchlist_add(dash._WatchlistTopicBody(topic="ai chips"))
    assert "already" in dup["message"]
    listing = await dash.watchlist_list()
    assert any(w["topic"] == "AI chips" for w in listing["watchlist"])
    rem = await dash.watchlist_remove(dash._WatchlistTopicBody(topic="AI chips"))
    assert rem["watchlist"] == []


async def test_watchlist_add_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    async def _load() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(dash, "_load_watchlist", _load)
    with pytest.raises(HTTPException):
        await dash.watchlist_add(dash._WatchlistTopicBody(topic="   "))


async def test_research_history() -> None:
    dash._research_jobs["hist1"] = {
        "status": "done", "started_at": "2026-06-11T00:00:00Z",
        "result": {"topic": "x", "verdict": "enter", "score": 0.8},
    }
    out = await dash.research_history()
    assert any(h["job_id"] == "hist1" for h in out["history"])
    dash._research_jobs.pop("hist1", None)


# --------------------------------------------------------------------------- #
# search_signals + research job machinery
# --------------------------------------------------------------------------- #


async def test_search_signals_empty_query() -> None:
    assert await dash.search_signals(q="  ") == []


async def test_search_signals_all_and_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "signal_id": "s1", "platform": "hn", "title": "earbuds deal",
        "url": "http://x", "scraped_at": datetime(2026, 6, 11, tzinfo=UTC),
        "sentiment": 0.5, "commercial_intent": 0.9, "novelty": 0.4,
        "views": 1, "likes": 2, "comments": 3, "shares": 4,
    }
    _patch_pg(monkeypatch, _FakeConn(fetch=[row]))
    out_all = await dash.search_signals(q="earbuds", limit=10)
    assert out_all[0]["signal_id"] == "s1"
    out_plat = await dash.search_signals(q="earbuds", limit=10, platform="hn")
    assert out_plat[0]["engagement"] == 9


async def test_search_signals_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    _patch_pg_boom(monkeypatch)
    with pytest.raises(HTTPException):
        await dash.search_signals(q="x")


def test_cleanup_research_jobs_evicts_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "MAX_RESEARCH_JOBS", 2)
    dash._research_jobs.clear()
    base = datetime(2026, 6, 1, tzinfo=UTC)
    from datetime import timedelta

    for i in range(4):
        dash._research_jobs[f"j{i}"] = {
            "status": "done", "events": [], "_started_dt": base + timedelta(minutes=i),
        }
    dash._cleanup_research_jobs()
    assert len(dash._research_jobs) == 2
    # newest survive
    assert "j3" in dash._research_jobs
    assert "j0" not in dash._research_jobs
    dash._research_jobs.clear()


async def test_run_research_job_guarded_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "RESEARCH_JOB_TIMEOUT_S", 0.01)

    async def _slow(*_a: Any, **_k: Any) -> None:
        import asyncio as _aio

        await _aio.sleep(1)

    monkeypatch.setattr(dash, "_run_research_job", _slow)
    dash._research_jobs["guard1"] = {"status": "running", "events": []}
    await dash._run_research_job_guarded("guard1", "x", 5, False)
    assert dash._research_jobs["guard1"]["status"] == "error"
    dash._research_jobs.pop("guard1", None)


async def test_run_research_job_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    # Avoid real pool creation.
    monkeypatch.setattr(dash, "_research_pool", object())

    async def _fake_scrape_topic(topic: str, **_k: Any) -> Any:
        sig = SimpleNamespace(
            title="wireless earbuds deal",
            url="http://x",
            platform=SimpleNamespace(value="hacker_news"),
            author=SimpleNamespace(handle="bob"),
            scraped_at=datetime(2026, 6, 11, tzinfo=UTC),
            signal_id="s1",
            content_hash="h1",
            raw_text="body",
            engagement=SimpleNamespace(views=1, likes=2, comments=3, shares=4, saves=0),
        )
        return SimpleNamespace(
            total_unique=1, signals=[sig], total_fetched=2,
            duplicates_dropped=1, sources_hit=["hn"], errors=[],
            patterns=[SimpleNamespace(label="p1", signal_count=1, is_high_priority=True, velocity_slope=2.5)],
        )

    def _fake_expand(topic: str) -> Any:
        return SimpleNamespace(search_terms=["a", "b"], category="tech", related_entities=["x"])

    async def _fake_run_trend(**_k: Any) -> Any:
        dec = SimpleNamespace(
            agent="scout", verdict=SimpleNamespace(value="proceed"),
            score=0.8, confidence=0.7, reasoning="looks good",
        )
        return SimpleNamespace(
            final_priority=SimpleNamespace(value="P1"),
            final_verdict=SimpleNamespace(value="ENTER"),
            final_score=0.8, final_confidence=0.7, halt_reason="completed",
            decisions=[dec],
        )

    import aegis.agents.runner as runner_mod
    import aegis.scrape.sentiment as sent_mod
    import aegis.scrape.topic as topic_mod

    monkeypatch.setattr(topic_mod, "scrape_topic", _fake_scrape_topic)
    monkeypatch.setattr(topic_mod, "expand_topic", _fake_expand)
    monkeypatch.setattr(runner_mod, "run_trend", _fake_run_trend)
    monkeypatch.setattr(sent_mod, "score_text", lambda _t: 0.5)

    dash._research_jobs["happy1"] = {
        "status": "running", "events": [], "_started_dt": datetime.now(UTC),
    }
    await dash._run_research_job("happy1", "wireless earbuds", 5, False)
    job = dash._research_jobs["happy1"]
    assert job["status"] == "done"
    assert job["result"]["verdict"] == "ENTER"
    assert any(e["type"] == "result" for e in job["events"])
    dash._research_jobs.pop("happy1", None)


async def test_run_research_job_handles_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import aegis.scrape.topic as topic_mod

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("scrape exploded")

    monkeypatch.setattr(topic_mod, "scrape_topic", _boom)
    monkeypatch.setattr(dash, "_research_pool", object())
    dash._research_jobs["fail1"] = {
        "status": "running", "events": [], "_started_dt": datetime.now(UTC),
    }
    await dash._run_research_job("fail1", "x", 5, False)
    assert dash._research_jobs["fail1"]["status"] == "error"
    dash._research_jobs.pop("fail1", None)


# --------------------------------------------------------------------------- #
# research start / push-alert / stream
# --------------------------------------------------------------------------- #


async def test_topic_research_start(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(dash, "_run_research_job_guarded", _noop)
    out = await dash.topic_research_start(dash._ResearchRequest(topic="AI chips", limit=5))
    assert "job_id" in out
    job = dash._research_jobs.get(out["job_id"])
    assert job is not None
    task = job.get("_task")
    if task is not None:
        task.cancel()
    dash._research_jobs.pop(out["job_id"], None)


async def test_topic_research_start_rejects_empty() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await dash.topic_research_start(dash._ResearchRequest(topic="   "))


async def test_push_research_alert_404() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await dash.push_research_alert("nope")
    assert ei.value.status_code == 404


async def test_push_research_alert_409_incomplete() -> None:
    from fastapi import HTTPException

    dash._research_jobs["pj1"] = {"status": "running", "events": []}
    with pytest.raises(HTTPException) as ei:
        await dash.push_research_alert("pj1")
    assert ei.value.status_code == 409
    dash._research_jobs.pop("pj1", None)


async def test_push_research_alert_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_get_redis", lambda: _FakeRedis())
    dash._research_jobs["pj2"] = {
        "status": "done", "events": [],
        "result": {"verdict": "proceed", "score": 0.8, "confidence": 0.7, "topic": "x"},
    }
    out = await dash.push_research_alert("pj2")
    assert out["ok"] is True
    assert out["p4_verdict"] == "ENTER"
    dash._research_jobs.pop("pj2", None)


async def test_research_job_stream_404() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await dash.research_job_stream("missing")


async def test_research_job_stream_emits(monkeypatch: pytest.MonkeyPatch) -> None:
    dash._research_jobs["sj1"] = {
        "status": "done",
        "events": [{"type": "progress", "msg": "x"}, {"type": "result", "data": {}}],
    }
    resp = await dash.research_job_stream("sj1")
    chunks = [c async for c in resp.body_iterator]
    text = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
    assert "progress" in text
    assert "result" in text
    dash._research_jobs.pop("sj1", None)
