"""PASS5-5A — AegisHealthChecker self-healing tests.

Contract: every check degrades to "warning" (never raises) when its
backend is absent or failing; critical findings trigger fire-and-forget
healing actions that delegate to the autonomous jobs; the report is
JSON-serializable for the aegis:core:health stream.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from aegis.scheduler import autonomous, health_checker
from aegis.scheduler.health_checker import (
    AegisHealthChecker,
    HealthCheck,
    HealthReport,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal async Redis stub backed by plain dicts."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.stream_last_id: str | None = None
        self.redis_time: tuple[int, int] | None = None

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str, nx: bool = False) -> bool | None:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for k in keys:
            removed += 1 if self.kv.pop(k, None) is not None else 0
        return removed

    async def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})

    async def xinfo_stream(self, _name: str) -> dict[str, Any]:
        if self.stream_last_id is None:
            raise RuntimeError("no such stream")
        return {"last-generated-id": self.stream_last_id}

    async def time(self) -> tuple[int, int]:
        if self.redis_time is not None:
            return self.redis_time
        now = time.time()
        return (int(now), int((now % 1) * 1_000_000))


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeConn:
    def __init__(
        self,
        row: dict[str, Any] | None = None,
        fetchvals: list[Any] | None = None,
    ) -> None:
        self.row = row
        self.fetchvals = list(fetchvals or [])
        self.executed: list[str] = []

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append(query)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return self.row

    async def fetchval(self, query: str, *args: Any) -> Any:
        return self.fetchvals.pop(0) if self.fetchvals else None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


async def _drain_background_tasks() -> None:
    if health_checker._BACKGROUND_TASKS:
        await asyncio.gather(*health_checker._BACKGROUND_TASKS, return_exceptions=True)


# ---------------------------------------------------------------------------
# check_and_heal aggregation
# ---------------------------------------------------------------------------


async def test_no_backends_yields_all_warnings_never_raises():
    checker = AegisHealthChecker(redis=None, pool=None)
    report = await checker.check_and_heal()
    assert report.overall_status == "warning"
    assert len(report.checks) == 8
    assert all(c.status == "warning" for c in report.checks)


async def test_check_exception_is_mapped_to_named_warning(monkeypatch: pytest.MonkeyPatch):
    checker = AegisHealthChecker(redis=_FakeRedis(), pool=None)

    async def _boom() -> HealthCheck:
        raise RuntimeError("kaput")

    monkeypatch.setattr(checker, "_check_stream_staleness", _boom)
    report = await checker.check_and_heal()
    stream = next(c for c in report.checks if c.name == "stream_staleness")
    assert stream.status == "warning"
    assert "kaput" in stream.message


async def test_overall_is_critical_when_any_check_is_critical():
    redis = _FakeRedis()
    redis.kv["aegis:execute:killswitch"] = "TRIPPED"
    redis.kv["aegis:core:health:killswitch_tripped_since"] = (
        datetime.now(UTC) - timedelta(hours=3)
    ).isoformat()
    checker = AegisHealthChecker(redis=redis, pool=None)
    report = await checker.check_and_heal()
    assert report.overall_status == "critical"


async def test_report_to_dict_is_json_serializable():
    checker = AegisHealthChecker(redis=None, pool=None)
    report = await checker.check_and_heal()
    payload = json.dumps(report.to_dict())
    decoded = json.loads(payload)
    assert decoded["overall_status"] == "warning"
    assert len(decoded["checks"]) == 8


# ---------------------------------------------------------------------------
# Stream staleness
# ---------------------------------------------------------------------------


async def test_fresh_stream_is_healthy():
    redis = _FakeRedis()
    redis.stream_last_id = f"{int(time.time() * 1000)}-0"
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_stream_staleness()
    assert check.status == "healthy"
    assert check.action_taken is None


async def test_stale_stream_triggers_emergency_scrape(monkeypatch: pytest.MonkeyPatch):
    scrapes: list[bool] = []

    async def _fake_scrape() -> None:
        scrapes.append(True)

    monkeypatch.setattr(autonomous, "job_scrape", _fake_scrape)
    redis = _FakeRedis()
    two_hours_ago_ms = int((time.time() - 7200) * 1000)
    redis.stream_last_id = f"{two_hours_ago_ms}-0"
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_stream_staleness()
    await _drain_background_tasks()
    assert check.status == "critical"
    assert check.action_taken == "triggered_emergency_scrape"
    assert scrapes == [True]


async def test_missing_stream_degrades_to_warning():
    redis = _FakeRedis()  # stream_last_id None → xinfo raises
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_stream_staleness()
    assert check.status == "warning"


# ---------------------------------------------------------------------------
# Adapter health ratio
# ---------------------------------------------------------------------------


def _agent_state(health: str) -> str:
    return json.dumps({"name": "x", "health": health})


async def test_adapter_ratio_healthy_when_no_agents_tracked():
    checker = AegisHealthChecker(redis=_FakeRedis(), pool=None)
    check = await checker._check_adapter_health_ratio()
    assert check.status == "healthy"


async def test_adapter_ratio_critical_above_half_down():
    redis = _FakeRedis()
    redis.hashes["aegis:swarm:agent_health"] = {
        "a": _agent_state("DOWN"),
        "b": _agent_state("DOWN"),
        "c": _agent_state("HEALTHY"),
    }
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_adapter_health_ratio()
    assert check.status == "critical"
    assert check.value == pytest.approx(2 / 3)


async def test_adapter_ratio_warning_between_thresholds():
    redis = _FakeRedis()
    redis.hashes["aegis:swarm:agent_health"] = {
        "a": _agent_state("DOWN"),
        "b": _agent_state("HEALTHY"),
        "c": _agent_state("DEGRADED"),  # degraded is not quarantined
    }
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_adapter_health_ratio()
    assert check.status == "warning"


async def test_adapter_ratio_skips_malformed_entries():
    redis = _FakeRedis()
    redis.hashes["aegis:swarm:agent_health"] = {
        "a": "not json",
        "b": _agent_state("HEALTHY"),
    }
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_adapter_health_ratio()
    assert check.status == "healthy"


# ---------------------------------------------------------------------------
# Model staleness
# ---------------------------------------------------------------------------


async def test_recent_champion_is_healthy():
    conn = _FakeConn(
        row={
            "candidate_id": "cand-1",
            "test_auc": 0.91,
            "champion_since": datetime.now(UTC) - timedelta(days=3),
        }
    )
    checker = AegisHealthChecker(redis=None, pool=_FakePool(conn))
    check = await checker._check_model_staleness()
    assert check.status == "healthy"
    assert "0.9100" in check.message


async def test_stale_champion_with_outcomes_triggers_retrain(
    monkeypatch: pytest.MonkeyPatch,
):
    retrains: list[bool] = []

    async def _fake_retrain() -> None:
        retrains.append(True)

    monkeypatch.setattr(autonomous, "job_weekly_retrain", _fake_retrain)
    conn = _FakeConn(
        row={
            "candidate_id": "cand-1",
            "test_auc": 0.91,
            "champion_since": datetime.now(UTC) - timedelta(days=45),
        },
        fetchvals=[250],
    )
    checker = AegisHealthChecker(redis=None, pool=_FakePool(conn))
    check = await checker._check_model_staleness()
    await _drain_background_tasks()
    assert check.action_taken == "triggered_retrain"
    assert retrains == [True]


async def test_stale_champion_without_outcomes_does_not_retrain():
    conn = _FakeConn(
        row={
            "candidate_id": "cand-1",
            "test_auc": 0.91,
            "champion_since": datetime.now(UTC) - timedelta(days=45),
        },
        fetchvals=[7],
    )
    checker = AegisHealthChecker(redis=None, pool=_FakePool(conn))
    check = await checker._check_model_staleness()
    assert check.status == "warning"
    assert check.action_taken is None


async def test_no_champion_is_warning():
    checker = AegisHealthChecker(redis=None, pool=_FakePool(_FakeConn(row=None)))
    check = await checker._check_model_staleness()
    assert check.status == "warning"
    assert "No champion" in check.message


async def test_model_staleness_sets_tenant_rls():
    conn = _FakeConn(
        row={
            "candidate_id": "c",
            "test_auc": 0.9,
            "champion_since": datetime.now(UTC),
        }
    )
    checker = AegisHealthChecker(redis=None, pool=_FakePool(conn), tenant_id="t-1")
    await checker._check_model_staleness()
    assert any("app.current_tenant" in q for q in conn.executed)


# ---------------------------------------------------------------------------
# Killswitch
# ---------------------------------------------------------------------------


async def test_killswitch_armed_is_healthy_and_clears_marker():
    redis = _FakeRedis()
    redis.kv["aegis:core:health:killswitch_tripped_since"] = "stale-marker"
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_killswitch_state()
    assert check.status == "healthy"
    assert "aegis:core:health:killswitch_tripped_since" not in redis.kv


async def test_killswitch_freshly_tripped_is_warning():
    redis = _FakeRedis()
    redis.kv["aegis:execute:killswitch"] = "TRIPPED"
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_killswitch_state()
    assert check.status == "warning"
    # First observation recorded for the next cycle.
    assert "aegis:core:health:killswitch_tripped_since" in redis.kv


async def test_killswitch_stuck_over_two_hours_is_critical():
    redis = _FakeRedis()
    redis.kv["aegis:execute:killswitch"] = "TRIPPED"
    redis.kv["aegis:core:health:killswitch_tripped_since"] = (
        datetime.now(UTC) - timedelta(hours=2.5)
    ).isoformat()
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_killswitch_state()
    assert check.status == "critical"
    assert check.value is not None and check.value > 2


# ---------------------------------------------------------------------------
# Dynamic thresholds staleness
# ---------------------------------------------------------------------------


async def test_thresholds_absent_is_healthy_defaults():
    checker = AegisHealthChecker(redis=_FakeRedis(), pool=None)
    check = await checker._check_thresholds_staleness()
    assert check.status == "healthy"
    assert "defaults" in check.message


async def test_thresholds_fresh_is_healthy():
    redis = _FakeRedis()
    redis.kv["aegis:core:thresholds"] = json.dumps(
        {"updated_at": datetime.now(UTC).isoformat()}
    )
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_thresholds_staleness()
    assert check.status == "healthy"


async def test_thresholds_stale_triggers_forced_update(monkeypatch: pytest.MonkeyPatch):
    updates: list[bool] = []

    async def _fake_update() -> None:
        updates.append(True)

    monkeypatch.setattr(autonomous, "job_threshold_update", _fake_update)
    redis = _FakeRedis()
    redis.kv["aegis:core:thresholds"] = json.dumps(
        {"updated_at": (datetime.now(UTC) - timedelta(days=10)).isoformat()}
    )
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_thresholds_staleness()
    await _drain_background_tasks()
    assert check.status == "warning"
    assert check.action_taken == "triggered_threshold_update"
    assert updates == [True]


# ---------------------------------------------------------------------------
# Clock drift
# ---------------------------------------------------------------------------


async def test_clock_in_sync_is_healthy(monkeypatch: pytest.MonkeyPatch):
    called: list[bool] = []
    monkeypatch.setattr(
        AegisHealthChecker, "_sync_hwclock", staticmethod(lambda: called.append(True))
    )
    checker = AegisHealthChecker(redis=_FakeRedis(), pool=None)
    check = await checker._check_clock_drift()
    assert check.status == "healthy"
    assert called == []


async def test_clock_drift_triggers_hwclock_sync(monkeypatch: pytest.MonkeyPatch):
    called: list[bool] = []
    monkeypatch.setattr(
        AegisHealthChecker, "_sync_hwclock", staticmethod(lambda: called.append(True))
    )
    redis = _FakeRedis()
    redis.redis_time = (int(time.time()) - 60, 0)  # Redis 60 s behind
    checker = AegisHealthChecker(redis=redis, pool=None)
    check = await checker._check_clock_drift()
    assert check.status == "warning"
    assert check.action_taken == "ran_hwclock_sync"
    assert called == [True]


# ---------------------------------------------------------------------------
# Evolution loop idle
# ---------------------------------------------------------------------------


async def test_evolution_active_is_healthy():
    checker = AegisHealthChecker(redis=None, pool=_FakePool(_FakeConn(fetchvals=[42])))
    check = await checker._check_evolution_loop_idle()
    assert check.status == "healthy"
    assert check.value == 42.0


async def test_evolution_idle_is_warning():
    checker = AegisHealthChecker(redis=None, pool=_FakePool(_FakeConn(fetchvals=[0])))
    check = await checker._check_evolution_loop_idle()
    assert check.status == "warning"


# ---------------------------------------------------------------------------
# Scheduler job wiring
# ---------------------------------------------------------------------------


def test_health_check_job_function_exists():
    # Registration guard: the scheduler module exposes the job for cron wiring.
    assert callable(autonomous.job_health_check)


async def test_job_health_check_never_raises(monkeypatch: pytest.MonkeyPatch):
    import asyncpg

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("pg down")

    monkeypatch.setattr(asyncpg, "create_pool", _boom)
    await autonomous.job_health_check()  # logged, not raised


async def test_job_health_check_publishes_report(monkeypatch: pytest.MonkeyPatch):
    import asyncpg
    import redis.asyncio as redis_lib

    published: list[tuple[str, dict[str, str], int]] = []

    class _JobRedis(_FakeRedis):
        async def xadd(
            self,
            stream: str,
            fields: dict[str, str],
            maxlen: int | None = None,
            approximate: bool = False,
        ) -> str:
            published.append((stream, fields, maxlen or 0))
            return "1-0"

        async def aclose(self) -> None:
            return None

    class _JobPool(_FakePool):
        async def close(self) -> None:
            return None

    async def _fake_create_pool(*_a: Any, **_k: Any) -> _JobPool:
        return _JobPool(_FakeConn(fetchvals=[5, 5]))

    monkeypatch.setattr(asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(redis_lib, "from_url", lambda *_a, **_k: _JobRedis())

    await autonomous.job_health_check()

    assert len(published) == 1
    stream, fields, maxlen = published[0]
    assert stream == "aegis:core:health"
    assert maxlen == 288
    body = json.loads(fields["body"])
    assert body["overall_status"] in {"healthy", "warning", "critical"}
    assert len(body["checks"]) == 8


def test_report_dataclass_shape():
    report = HealthReport(
        checks=[HealthCheck(name="x", status="healthy")],
        checked_at=datetime.now(UTC),
        overall_status="healthy",
    )
    assert report.healing_actions == []
    assert report.to_dict()["checks"][0]["name"] == "x"
