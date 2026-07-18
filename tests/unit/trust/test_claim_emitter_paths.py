"""Exercise ClaimEmitter emit + backfill loops without a live DB or model.

The predict builder + heuristic are monkeypatched to deterministic stubs, and a
query-aware fake pool feeds canned tag/signal rows. This covers the OMEGA Phase
B/C claim-emission paths (and the Phase C opportunity-capture hook) end to end.
"""

from __future__ import annotations

from typing import Any

import pytest

from aegis.trust.claim_emitter import ClaimEmitter


class _FakePred:
    p_decline = 0.3
    stage = "breakout"


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split())
        if "MIN(ts)" in q:                       # backfill anchors
            return self._pool.anchor_rows
        if "COUNT(*) AS n" in q and "GROUP BY tag" in q:   # active tags
            return self._pool.tag_rows
        if "FROM signals" in q:                  # per-trend signal rows
            return self._pool.signal_rows
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "COUNT(*) AS n" in query:             # _signal_count
            return {"n": self._pool.signal_count}
        if "max_gap_h" in query:                 # STAGE 1.3 continuity check
            return {"max_gap_h": 0.0}            # canned: window alive
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
        self.tag_rows: list[dict[str, Any]] = []
        self.anchor_rows: list[dict[str, Any]] = []
        self.signal_rows: list[dict[str, Any]] = []
        self.signal_count = 10

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _patch_predict(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegis.predict.features import builder
    from aegis.predict.models import heuristic

    monkeypatch.setattr(builder, "build_window_from_rows", lambda **_: object())
    monkeypatch.setattr(heuristic, "heuristic_predict", lambda **_: [_FakePred()])


def _signal_row() -> dict[str, Any]:
    from datetime import UTC, datetime

    return {
        "signal_id": "s1", "platform": "reddit", "ts": datetime(2026, 5, 1, tzinfo=UTC),
        "title": "t", "pii_scrubbed_text": "b", "url": "u", "external_id": "e",
        "author_id": None, "views": 1, "likes": 1, "comments": 0, "shares": 0,
        "saves": 0, "source_confidence": 0.5, "completeness": 0.5,
    }


async def test_emit_active_claims_emits_and_captures() -> None:
    pool = _Pool()
    pool.tag_rows = [{"trend_key": "ai", "n": 8}]
    pool.signal_rows = [_signal_row() for _ in range(6)]
    summary = await ClaimEmitter(pool).emit_active_claims(  # type: ignore[arg-type]
        horizon_hours=72, max_trends=5, min_signals=5
    )
    assert summary.examined == 1
    assert summary.emitted == 1


async def test_backfill_heuristic_claims_settles() -> None:
    from datetime import UTC, datetime

    pool = _Pool()
    pool.anchor_rows = [
        {"trend_key": "ai", "claim_ts": datetime(2026, 5, 1, tzinfo=UTC)}
    ]
    pool.signal_rows = [_signal_row() for _ in range(6)]
    pool.signal_count = 10
    summary = await ClaimEmitter(pool).backfill_heuristic_claims(  # type: ignore[arg-type]
        horizon_hours=72, max_claims=10, min_baseline_signals=3, lookback_days=120
    )
    assert summary.examined == 1
    assert summary.emitted == 1
