"""
Unit tests for the PROJECT OMEGA Phase A self-supervised settlement loop.

Includes the MANDATORY look-ahead leakage test: the settler must never count
signals from outside the window it claims to measure. The fake pool below is
backed by a real in-memory list of (trend_key, ts) signal events, so the COUNT
queries are executed against ground truth with the exact bounds the settler
passes — any look-ahead bug shows up as a wrong count.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from aegis.evolve.schemas import SignalOutcome
from aegis.evolve.settlement_loop import SignalOutcomeSettler, _classify

# ---------------------------------------------------------------------------
# Fake pool backed by an in-memory signal store
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, signals: list[tuple[str, datetime]], anchors: list[dict]) -> None:
        self._signals = signals  # (trend_key, ts)
        self._anchors = anchors
        self.updates: list[tuple] = []
        self.inserts: list[tuple] = []

    async def execute(self, sql: str, *args):
        if "set_config" in sql:
            return
        if sql.strip().startswith("UPDATE signal_outcomes"):
            self.updates.append(args)
            return
        if "INSERT INTO signal_outcomes" in sql:
            self.inserts.append(args)
            return
        return

    async def fetchrow(self, sql: str, *args):
        if "COUNT(*) AS n" in sql and "FROM signals" in sql:
            trend_key, lo, hi = args
            n = sum(
                1 for tk, ts in self._signals if tk == trend_key and lo <= ts < hi
            )
            return {"n": n}
        return None

    async def fetch(self, sql: str, *args):
        if "FROM signal_outcomes" in sql and "resolution_status = 'pending'" in sql:
            return self._pending_rows
        if "GROUP BY tag" in sql:
            return self._anchors
        return []


def _make_pool(signals=None, anchors=None, pending=None):
    conn = _FakeConn(signals or [], anchors or [])
    conn._pending_rows = pending or []
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------

class TestClassify:
    def test_rise_fall_flat(self) -> None:
        assert _classify(10, 20) == "rise"
        assert _classify(10, 4) == "fall"
        assert _classify(10, 10.5) == "flat"

    def test_zero_baseline(self) -> None:
        assert _classify(0, 5) == "rise"
        assert _classify(0, 0) == "flat"


# ---------------------------------------------------------------------------
# MANDATORY: no look-ahead leakage
# ---------------------------------------------------------------------------

class TestNoLookAhead:
    @pytest.mark.asyncio
    async def test_signal_count_window_is_half_open(self) -> None:
        """_signal_count counts [lo, hi): includes lo, excludes hi."""
        t0 = datetime(2026, 6, 1, tzinfo=UTC)
        signals = [
            ("ai", t0),                       # at lo  -> included
            ("ai", t0 + timedelta(hours=1)),  # inside -> included
            ("ai", t0 + timedelta(hours=2)),  # at hi  -> EXCLUDED
            ("ai", t0 - timedelta(hours=1)),  # before -> excluded
            ("ml", t0 + timedelta(hours=1)),  # other key -> excluded
        ]
        pool, _ = _make_pool(signals=signals)
        settler = SignalOutcomeSettler(pool)
        n = await settler._signal_count("ai", t0, t0 + timedelta(hours=2))
        assert n == 2  # only the two strictly inside [lo, hi)

    @pytest.mark.asyncio
    async def test_backfill_observation_excludes_future_beyond_horizon(self) -> None:
        """
        A signal that lands AFTER the observation window must not inflate the
        observed value. This is the core leakage guard.
        """
        claim_ts = datetime(2026, 6, 1, tzinfo=UTC)
        horizon = 72
        obs_end = claim_ts + timedelta(hours=horizon)
        signals = [
            # baseline window [claim_ts-72h, claim_ts): 3 signals
            ("ai", claim_ts - timedelta(hours=5)),
            ("ai", claim_ts - timedelta(hours=10)),
            ("ai", claim_ts - timedelta(hours=20)),
            # observation window [claim_ts, obs_end): 5 signals -> 'rise'
            *[("ai", claim_ts + timedelta(hours=h)) for h in (1, 2, 3, 50, 70)],
            # AFTER the window — must be ignored
            ("ai", obs_end + timedelta(hours=1)),
            ("ai", obs_end + timedelta(hours=100)),
        ]
        anchors = [{"trend_key": "ai", "day": claim_ts, "claim_ts": claim_ts}]
        pool, conn = _make_pool(signals=signals, anchors=anchors)
        settler = SignalOutcomeSettler(pool)
        summary = await settler.backfill_from_history(
            horizon_hours=horizon, lookback_days=400
        )
        assert summary.settled == 1
        # observed_value recorded on the insert = 5 (the post-window signals
        # were excluded). Insert positional arg order matches record_signal_outcome.
        assert len(conn.inserts) == 1
        # observed_value is the 12th positional arg (after sql) -> index 11.
        observed_value = conn.inserts[0][11]
        assert observed_value == 5.0
        # baseline 3 -> observed 5 -> +66% -> 'rise' -> claim 'rise' -> correct.
        assert summary.correct == 1


# ---------------------------------------------------------------------------
# settle_pending
# ---------------------------------------------------------------------------

class TestSettlePending:
    @pytest.mark.asyncio
    async def test_settles_correct_claim(self) -> None:
        claim_ts = datetime(2026, 6, 1, tzinfo=UTC)
        settle_after = claim_ts + timedelta(hours=72)
        pending = [{
            "outcome_id": "oid-1",
            "trend_key": "ai",
            "metric": "signal_count",
            "claimed_direction": "rise",
            "baseline_value": 2.0,
            "claim_ts": claim_ts,
            "settle_after": settle_after,
        }]
        signals = [("ai", claim_ts + timedelta(hours=h)) for h in (1, 2, 3, 4, 5)]
        pool, conn = _make_pool(signals=signals, pending=pending)
        settler = SignalOutcomeSettler(pool)
        summary = await settler.settle_pending()
        assert summary.settled == 1
        assert summary.correct == 1  # 2 -> 5 = rise == claimed rise
        # the UPDATE marked it 'correct'
        assert conn.updates[0][3] == "correct"

    @pytest.mark.asyncio
    async def test_incorrect_claim_marked_incorrect(self) -> None:
        claim_ts = datetime(2026, 6, 1, tzinfo=UTC)
        pending = [{
            "outcome_id": "oid-2",
            "trend_key": "ai",
            "metric": "signal_count",
            "claimed_direction": "rise",
            "baseline_value": 10.0,
            "claim_ts": claim_ts,
            "settle_after": claim_ts + timedelta(hours=72),
        }]
        # only 1 signal in the window -> fall, not rise
        signals = [("ai", claim_ts + timedelta(hours=1))]
        pool, conn = _make_pool(signals=signals, pending=pending)
        settler = SignalOutcomeSettler(pool)
        summary = await settler.settle_pending()
        assert summary.settled == 1
        assert summary.correct == 0
        assert conn.updates[0][3] == "incorrect"


# ---------------------------------------------------------------------------
# SignalOutcome schema
# ---------------------------------------------------------------------------

class TestSignalOutcomeSchema:
    def test_defaults_and_is_correct(self) -> None:
        o = SignalOutcome(
            prediction_id="p",
            trend_key="ai",
            claimed_direction="rise",
            prediction_score=0.6,
            prediction_confidence=0.5,
            settle_after=datetime.now(UTC),
        )
        assert o.resolution_status == "pending"
        assert o.is_correct is False
        assert o.metric == "signal_count"
