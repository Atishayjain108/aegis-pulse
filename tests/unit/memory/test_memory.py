"""Phase C — Knowledge Expansion: opportunity + failure memory unit tests.

All DB access uses a query-aware in-memory fake pool — no live Postgres.
The headline assertions enforce the falsifiability invariant (Rule 12):
knowledge is derived only from settled outcomes; a failure row exists only for a
settled-incorrect outcome.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from aegis.memory import (
    FailureCategory,
    MemoryBackfill,
    OpportunityMemory,
    OpportunityOutcome,
    OpportunityType,
    classify_failure,
)
from aegis.memory.failure import FailureMemory


# --------------------------------------------------------------------------- #
# Query-aware fake asyncpg pool                                               #
# --------------------------------------------------------------------------- #
class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        q = " ".join(query.split())
        if "INSERT INTO opportunities" in q:
            self._pool.opportunity_inserts.append(args)
        elif "INSERT INTO failures" in q:
            self._pool.failure_inserts.append(args)
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split())
        if "FROM signal_outcomes" in q:
            return self._pool.signal_rows
        if "FROM opportunities" in q:
            return self._pool.pattern_rows
        if "FROM failures" in q:
            return self._pool.failure_count_rows
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "COUNT(*)" in query:
            return {"n": len(self._pool.opportunity_inserts)}
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
        self.signal_rows: list[dict[str, Any]] = []
        self.pattern_rows: list[dict[str, Any]] = []
        self.failure_count_rows: list[dict[str, Any]] = []
        self.opportunity_inserts: list[tuple[Any, ...]] = []
        self.failure_inserts: list[tuple[Any, ...]] = []

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))

    async def close(self) -> None:
        pass


def _settled_row(**overrides: Any) -> dict[str, Any]:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    base = {
        "prediction_id": "heuristic-ai-20260601",
        "trend_key": "ai",
        "claimed_direction": "rise",
        "observed_direction": "rise",
        "prediction_score": 0.71,
        "prediction_confidence": 0.71,
        "baseline_value": 40.0,
        "observed_value": 60.0,
        "horizon_hours": 72,
        "settled_at": now,
        "settlement_timestamp": now,
        "resolution_status": "correct",
        "metadata": {},
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Pure classifier (Rule 5) — no DB                                            #
# --------------------------------------------------------------------------- #
def test_classify_false_breakout() -> None:
    f = classify_failure(
        opportunity_id="o1", trend_key="ai", prediction_id="p1",
        claimed_direction="rise", observed_direction="fall",
        prediction_confidence=0.6, baseline_value=40.0,
    )
    assert f.failure_category is FailureCategory.FALSE_BREAKOUT
    # confidence_error = conf - 0.0 for an incorrect outcome.
    assert f.confidence_error == pytest.approx(0.6)


def test_classify_missed_breakout() -> None:
    f = classify_failure(
        opportunity_id="o1", trend_key="ai", prediction_id="p1",
        claimed_direction="fall", observed_direction="rise",
        prediction_confidence=0.5, baseline_value=30.0,
    )
    assert f.failure_category is FailureCategory.MISSED_BREAKOUT


def test_classify_decayed_when_flat() -> None:
    f = classify_failure(
        opportunity_id="o1", trend_key="ai", prediction_id="p1",
        claimed_direction="rise", observed_direction="flat",
        prediction_confidence=0.5, baseline_value=20.0,
    )
    assert f.failure_category is FailureCategory.DECAYED_OR_STALLED


def test_classify_thin_evidence_dominates() -> None:
    # Below the thin-evidence threshold the category becomes a data problem,
    # regardless of the direction mismatch.
    f = classify_failure(
        opportunity_id="o1", trend_key="ai", prediction_id="p1",
        claimed_direction="rise", observed_direction="fall",
        prediction_confidence=0.9, baseline_value=2.0,
    )
    assert f.failure_category is FailureCategory.EVIDENCE_THIN
    assert "sufficient_signal_volume" in f.missing_information
    assert f.evidence_quality == pytest.approx(2.0 / 50.0)


def test_classify_unclassified_when_directions_agree() -> None:
    # No direction mismatch and not flat → falls through to UNCLASSIFIED.
    f = classify_failure(
        opportunity_id="o1", trend_key="ai", prediction_id="p1",
        claimed_direction="rise", observed_direction="rise",
        prediction_confidence=0.5, baseline_value=40.0,
    )
    assert f.failure_category is FailureCategory.UNCLASSIFIED


def test_classify_overconfident_flag() -> None:
    f = classify_failure(
        opportunity_id="o1", trend_key="ai", prediction_id="p1",
        claimed_direction="rise", observed_direction="fall",
        prediction_confidence=0.85, baseline_value=40.0,
    )
    assert f.failure_category is FailureCategory.OVERCONFIDENT


# --------------------------------------------------------------------------- #
# Backfill falsifiability invariant (Rule 12)                                 #
# --------------------------------------------------------------------------- #
async def test_backfill_correct_outcome_makes_no_failure() -> None:
    pool = _Pool()
    pool.signal_rows = [_settled_row(resolution_status="correct")]
    summary = await MemoryBackfill(pool).run()  # type: ignore[arg-type]
    assert summary.opportunities == 1
    assert summary.failures == 0
    assert pool.failure_inserts == []
    # The opportunity outcome must be 'realized'.
    args = pool.opportunity_inserts[0]
    assert OpportunityOutcome.REALIZED.value in args
    assert OpportunityType.INFO_ARB.value in args


async def test_backfill_incorrect_outcome_creates_linked_failure() -> None:
    pool = _Pool()
    pool.signal_rows = [
        _settled_row(
            resolution_status="incorrect",
            claimed_direction="rise",
            observed_direction="fall",
        )
    ]
    summary = await MemoryBackfill(pool).run()  # type: ignore[arg-type]
    assert summary.opportunities == 1
    assert summary.failures == 1
    # Opportunity references the failure id; failure references the opportunity.
    opp_args = pool.opportunity_inserts[0]
    fail_args = pool.failure_inserts[0]
    failure_id_in_opp = opp_args[16]  # failure_id positional in INSERT
    failure_id_in_failure = fail_args[0]
    opp_id_in_opp = opp_args[0]
    opp_id_in_failure = fail_args[1]
    assert failure_id_in_opp == failure_id_in_failure
    assert opp_id_in_failure == opp_id_in_opp
    assert OpportunityOutcome.FAILED.value in opp_args


async def test_backfill_handles_empty_history() -> None:
    pool = _Pool()
    summary = await MemoryBackfill(pool).run()  # type: ignore[arg-type]
    assert summary.examined == 0
    assert summary.opportunities == 0


# --------------------------------------------------------------------------- #
# Query post-processing                                                       #
# --------------------------------------------------------------------------- #
async def test_patterns_realized_rate_math() -> None:
    pool = _Pool()
    pool.pattern_rows = [
        {
            "opportunity_type": "info_arb",
            "category": "general",
            "total": 10,
            "realized": 6,
            "failed": 4,
            "avg_confidence": 0.7,
        }
    ]
    rows = await OpportunityMemory(pool).patterns()  # type: ignore[arg-type]
    assert rows[0]["realized_rate"] == pytest.approx(0.6)
    assert rows[0]["avg_confidence"] == pytest.approx(0.7)


async def test_failure_category_counts() -> None:
    pool = _Pool()
    pool.failure_count_rows = [
        {"failure_category": "false_breakout", "n": 5},
        {"failure_category": "evidence_thin", "n": 3},
    ]
    counts = await FailureMemory(pool).category_counts()  # type: ignore[arg-type]
    assert counts == {"false_breakout": 5, "evidence_thin": 3}


async def test_opportunity_count() -> None:
    pool = _Pool()
    pool.signal_rows = [_settled_row()]
    await MemoryBackfill(pool).run()  # type: ignore[arg-type]
    assert await OpportunityMemory(pool).count() == 1  # type: ignore[arg-type]
