"""Phase C Stage 2 — live capture hooks (MemoryCapture) unit tests.

Verifies the emit hook records a pending opportunity, the settlement hook
upserts to a terminal outcome (UPDATE existing pending, else INSERT), failures
are captured only for incorrect outcomes, and the feature flag fully no-ops.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from aegis.memory import MemoryCapture
from aegis.memory.taxonomy import OpportunityOutcome


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        q = " ".join(query.split())
        if q.startswith("UPDATE opportunities"):
            self._pool.opportunity_updates.append(args)
            return f"UPDATE {self._pool.update_rowcount}"
        if "INSERT INTO opportunities" in q:
            self._pool.opportunity_inserts.append(args)
        elif "INSERT INTO failures" in q:
            self._pool.failure_inserts.append(args)
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return None


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Conn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


class _Pool:
    def __init__(self) -> None:
        self.update_rowcount = 1
        self.opportunity_inserts: list[tuple[Any, ...]] = []
        self.opportunity_updates: list[tuple[Any, ...]] = []
        self.failure_inserts: list[tuple[Any, ...]] = []

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))

    async def close(self) -> None:
        pass


def _settled(**overrides: Any) -> dict[str, Any]:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    base = {
        "prediction_id": "heuristic-ai-2026060100",
        "trend_key": "ai",
        "claimed_direction": "rise",
        "observed_direction": "rise",
        "observed_value": 60.0,
        "prediction_score": 0.7,
        "prediction_confidence": 0.7,
        "baseline_value": 40.0,
        "horizon_hours": 72,
        "settlement_timestamp": now,
        "resolution_status": "correct",
    }
    base.update(overrides)
    return base


async def test_on_claim_records_pending_opportunity() -> None:
    pool = _Pool()
    ok = await MemoryCapture(pool).on_claim(  # type: ignore[arg-type]
        prediction_id="p1", trend_key="ai", claimed_direction="rise",
        prediction_score=0.7, prediction_confidence=0.7,
        baseline_value=40.0, horizon_hours=72,
    )
    assert ok is True
    assert len(pool.opportunity_inserts) == 1
    assert OpportunityOutcome.PENDING.value in pool.opportunity_inserts[0]


async def test_on_settlement_correct_updates_pending_no_failure() -> None:
    pool = _Pool()
    pool.update_rowcount = 1  # a pending opportunity already exists
    ok = await MemoryCapture(pool).on_settlement(_settled())  # type: ignore[arg-type]
    assert ok is True
    assert len(pool.opportunity_updates) == 1
    assert pool.opportunity_inserts == []   # updated, not inserted
    assert pool.failure_inserts == []       # correct → no failure


async def test_on_settlement_inserts_when_no_pending() -> None:
    pool = _Pool()
    pool.update_rowcount = 0  # nothing pending → settle() falls back to INSERT
    ok = await MemoryCapture(pool).on_settlement(_settled())  # type: ignore[arg-type]
    assert ok is True
    assert len(pool.opportunity_inserts) == 1


async def test_on_settlement_incorrect_records_failure() -> None:
    pool = _Pool()
    ok = await MemoryCapture(pool).on_settlement(  # type: ignore[arg-type]
        _settled(
            resolution_status="incorrect",
            claimed_direction="rise",
            observed_direction="fall",
            observed_value=10.0,
        )
    )
    assert ok is True
    assert len(pool.failure_inserts) == 1


async def test_on_settlement_ignores_unsettled_status() -> None:
    pool = _Pool()
    ok = await MemoryCapture(pool).on_settlement(  # type: ignore[arg-type]
        _settled(resolution_status="pending")
    )
    assert ok is False
    assert pool.opportunity_updates == []
    assert pool.opportunity_inserts == []


async def test_on_settlement_swallows_graph_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aegis.memory.graph as graph_mod

    class _BoomGraph:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def relate_opportunity(self, *_: Any) -> int:
            raise RuntimeError("graph down")

    monkeypatch.setattr(graph_mod, "KnowledgeGraph", _BoomGraph)
    pool = _Pool()
    pool.update_rowcount = 1
    # settle still succeeds even though the graph write blows up.
    ok = await MemoryCapture(pool).on_settlement(_settled())  # type: ignore[arg-type]
    assert ok is True


async def test_flag_disabled_no_ops(monkeypatch: pytest.MonkeyPatch) -> None:
    import aegis.memory.capture as cap

    class _S:
        memory_enabled = False

    monkeypatch.setattr(cap, "settings", lambda: _S())
    pool = _Pool()
    claim_ok = await MemoryCapture(pool).on_claim(  # type: ignore[arg-type]
        prediction_id="p1", trend_key="ai", claimed_direction="rise",
        prediction_score=0.7, prediction_confidence=0.7,
        baseline_value=40.0, horizon_hours=72,
    )
    settle_ok = await MemoryCapture(pool).on_settlement(_settled())  # type: ignore[arg-type]
    assert claim_ok is False
    assert settle_ok is False
    assert pool.opportunity_inserts == []
    assert pool.opportunity_updates == []
