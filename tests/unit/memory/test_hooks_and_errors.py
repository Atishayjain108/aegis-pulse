"""Phase C — cover the best-effort error branches and the live hook wrappers.

Memory persistence is best-effort: on any DB error every method must log and
return a safe empty value, never raise into the prediction/settlement path.
These tests inject a raising pool to exercise those branches, and drive the
settler/emitter wrapper methods directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aegis.memory import MemoryBackfill, OpportunityMemory
from aegis.memory.failure import FailureMemory
from aegis.memory.schemas import Opportunity


# --------------------------------------------------------------------------- #
# A pool that raises on acquire — forces every except branch.                  #
# --------------------------------------------------------------------------- #
class _BoomPool:
    def acquire(self) -> Any:
        raise RuntimeError("db down")

    async def close(self) -> None:
        pass


def _opp() -> Opportunity:
    return Opportunity(trend_key="ai", prediction_id="p1")


async def test_opportunity_methods_swallow_errors() -> None:
    mem = OpportunityMemory(_BoomPool())  # type: ignore[arg-type]
    assert await mem.record(_opp()) is False
    assert await mem.settle(_opp()) is False
    assert await mem.count() == 0
    assert await mem.patterns() == []


async def test_failure_methods_swallow_errors() -> None:
    from aegis.memory import classify_failure

    mem = FailureMemory(_BoomPool())  # type: ignore[arg-type]
    f = classify_failure(
        opportunity_id="o1", trend_key="ai", prediction_id="p1",
        claimed_direction="rise", observed_direction="fall",
        prediction_confidence=0.6, baseline_value=40.0,
    )
    assert await mem.record(f) is False
    assert await mem.category_counts() == {}


async def test_backfill_swallows_fetch_error() -> None:
    summary = await MemoryBackfill(_BoomPool()).run()  # type: ignore[arg-type]
    assert summary.examined == 0
    assert summary.opportunities == 0


# --------------------------------------------------------------------------- #
# Capture-capable fake pool to drive the settler/emitter hook wrappers.        #
# --------------------------------------------------------------------------- #
class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        q = " ".join(query.split())
        if q.startswith("UPDATE opportunities"):
            return "UPDATE 0"
        if "INSERT INTO opportunities" in q:
            self._pool.opportunity_inserts.append(args)
        elif "INSERT INTO failures" in q:
            self._pool.failure_inserts.append(args)
        return "OK"

    async def fetch(self, *_: Any) -> list[dict[str, Any]]:
        return []

    async def fetchrow(self, *_: Any) -> dict[str, Any] | None:
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
        self.opportunity_inserts: list[tuple[Any, ...]] = []
        self.failure_inserts: list[tuple[Any, ...]] = []

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))

    async def close(self) -> None:
        pass


async def test_settler_capture_hook_records_settlement() -> None:
    from aegis.evolve.settlement_loop import SignalOutcomeSettler

    pool = _Pool()
    settler = SignalOutcomeSettler(pool)  # type: ignore[arg-type]
    row = {
        "prediction_id": "heuristic-ai-2026060100",
        "trend_key": "ai",
        "claimed_direction": "rise",
        "prediction_score": 0.7,
        "prediction_confidence": 0.7,
        "baseline_value": 40.0,
        "horizon_hours": 72,
        "settlement_timestamp": datetime(2026, 6, 1, tzinfo=UTC),
    }
    # incorrect outcome → opportunity (FAILED) + a failure row.
    await settler._capture_settlement(
        row, observed=10.0, observed_dir="fall", is_correct=False
    )
    assert len(pool.opportunity_inserts) == 1
    assert len(pool.failure_inserts) == 1


async def test_settler_capture_hook_never_raises_on_bad_row() -> None:
    from aegis.evolve.settlement_loop import SignalOutcomeSettler

    settler = SignalOutcomeSettler(_Pool())  # type: ignore[arg-type]
    # Missing keys must be swallowed, not raised.
    await settler._capture_settlement(
        {"resolution_status": "correct"}, observed=1.0, observed_dir="rise",
        is_correct=True,
    )


async def test_capture_enabled_check_swallows_settings_error(
    monkeypatch: Any,
) -> None:
    import aegis.memory.capture as cap
    from aegis.memory import MemoryCapture

    def _boom() -> Any:
        raise RuntimeError("no settings")

    monkeypatch.setattr(cap, "settings", _boom)
    # _enabled() returns False on error → on_claim short-circuits to False.
    ok = await MemoryCapture(_Pool()).on_claim(  # type: ignore[arg-type]
        prediction_id="p1", trend_key="ai", claimed_direction="rise",
        prediction_score=0.7, prediction_confidence=0.7,
        baseline_value=40.0, horizon_hours=72,
    )
    assert ok is False


async def test_on_settlement_swallows_mapping_error() -> None:
    from aegis.memory import MemoryCapture

    # 'incorrect' passes the status guard but the row is missing required keys,
    # so map_settled_row raises — the outer except must catch and return False.
    ok = await MemoryCapture(_Pool()).on_settlement(  # type: ignore[arg-type]
        {"resolution_status": "incorrect"}
    )
    assert ok is False


async def test_on_claim_swallows_inner_error() -> None:
    from aegis.memory import MemoryCapture

    cap = MemoryCapture(_Pool())  # type: ignore[arg-type]

    class _BoomOpps:
        async def record(self, *_: Any) -> bool:
            raise RuntimeError("insert blew up")

    cap._opps = _BoomOpps()  # type: ignore[assignment]
    ok = await cap.on_claim(
        prediction_id="p1", trend_key="ai", claimed_direction="rise",
        prediction_score=0.7, prediction_confidence=0.7,
        baseline_value=40.0, horizon_hours=72,
    )
    assert ok is False


async def test_emitter_capture_hook_records_pending() -> None:
    from aegis.evolve.schemas import SignalOutcome
    from aegis.trust.claim_emitter import ClaimEmitter

    pool = _Pool()
    emitter = ClaimEmitter(pool)  # type: ignore[arg-type]
    outcome = SignalOutcome(
        prediction_id="heuristic-ai-2026060100",
        trend_key="ai",
        claimed_direction="rise",
        prediction_score=0.7,
        prediction_confidence=0.7,
        baseline_value=40.0,
        horizon_hours=72,
        settle_after=datetime(2026, 6, 4, tzinfo=UTC),
    )
    await emitter._capture_claim(outcome)
    assert len(pool.opportunity_inserts) == 1
