"""Unit coverage for TrustStore persistence + TrustScorer DB aggregation.

Both use a query-aware in-memory fake pool — no live Postgres. These exercise
the OMEGA Phase B/C trust persistence + scoring paths that the calibration-only
tests do not reach.
"""

from __future__ import annotations

from typing import Any

from aegis.trust.calibrator import Calibrator
from aegis.trust.schemas import CalibrationReport, TrustScore
from aegis.trust.scores import TrustScorer
from aegis.trust.store import TrustStore


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        self._pool.executed.append(" ".join(query.split()))
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split())
        if "JOIN LATERAL" in q:
            return self._pool.source_rows
        if "FROM signal_outcomes" in q:
            return self._pool.settled_rows
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return self._pool.map_row


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Conn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


class _Pool:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.settled_rows: list[dict[str, Any]] = []
        self.source_rows: list[dict[str, Any]] = []
        self.map_row: dict[str, Any] | None = None

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))

    async def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# TrustStore                                                                   #
# --------------------------------------------------------------------------- #
async def test_store_save_snapshot_and_trust() -> None:
    pool = _Pool()
    store = TrustStore(pool)  # type: ignore[arg-type]
    rep = CalibrationReport(entity_kind="model", entity_id="heuristic", n=10)
    assert await store.save_snapshot(rep) is True
    score = TrustScore(entity_kind="model", entity_id="heuristic", trust=0.7)
    assert await store.save_trust(score) is True
    assert any("calibration_snapshots" in q for q in pool.executed)
    assert any("trust_scores" in q for q in pool.executed)


async def test_store_save_and_load_map() -> None:
    pool = _Pool()
    store = TrustStore(pool)  # type: ignore[arg-type]
    cal = Calibrator.identity()
    assert await store.save_map(cal) is True
    # load with a stored map → returns a Calibrator built from the knots.
    pool.map_row = {"knots_json": cal.to_json()}
    loaded = await store.load_map()
    assert isinstance(loaded, Calibrator)


async def test_store_load_map_missing_returns_identity() -> None:
    pool = _Pool()
    pool.map_row = None
    loaded = await TrustStore(pool).load_map()  # type: ignore[arg-type]
    assert isinstance(loaded, Calibrator)


# --------------------------------------------------------------------------- #
# TrustScorer                                                                  #
# --------------------------------------------------------------------------- #
async def test_scorer_model_trust() -> None:
    pool = _Pool()
    pool.settled_rows = [
        {"p": 0.6 + 0.01 * i, "s": "correct" if i % 2 == 0 else "incorrect"}
        for i in range(20)
    ]
    score = await TrustScorer(pool).model_trust("heuristic")  # type: ignore[arg-type]
    assert score.entity_kind == "model"
    assert score.entity_id == "heuristic"
    assert 0.0 <= score.trust <= 1.0
    assert score.n_outcomes == 20


class _BoomPool:
    def acquire(self) -> Any:
        raise RuntimeError("db down")

    async def close(self) -> None:
        pass


async def test_store_methods_swallow_errors() -> None:
    store = TrustStore(_BoomPool())  # type: ignore[arg-type]
    assert await store.save_snapshot(CalibrationReport()) is False
    assert await store.save_map(Calibrator.identity()) is False
    assert await store.save_trust(
        TrustScore(entity_kind="model", entity_id="x", trust=0.5)
    ) is False
    # load_map degrades to an identity calibrator on error, never raises.
    assert isinstance(await store.load_map(), Calibrator)


async def test_scorer_source_trust_filters_min_outcomes() -> None:
    pool = _Pool()
    # 'reddit' has 12 outcomes (kept), 'hn' has 2 (dropped by min_outcomes=10).
    rows = [
        {"platform": "reddit", "p": 0.6, "st": "correct" if i % 2 else "incorrect"}
        for i in range(12)
    ]
    rows += [{"platform": "hn", "p": 0.7, "st": "correct"} for _ in range(2)]
    pool.source_rows = rows
    scores = await TrustScorer(pool).source_trust(min_outcomes=10)  # type: ignore[arg-type]
    ids = {s.entity_id for s in scores}
    assert "reddit" in ids
    assert "hn" not in ids
