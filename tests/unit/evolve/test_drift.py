"""Unit tests for DriftDetector."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from aegis.evolve.config import EvolveSettings
from aegis.evolve.drift import DriftDetector
from aegis.evolve.schemas import DriftSnapshot


def _make_cfg(**overrides) -> EvolveSettings:
    return EvolveSettings(**overrides)


class TestDriftDetectorNoPool:
    """Tests with no DB pool — purely in-memory."""

    @pytest.mark.asyncio
    async def test_detect_drift_no_drift(self) -> None:
        detector = DriftDetector(db_pool=None)
        # Features near zero (same as baseline) → low drift
        X = np.zeros((50, 20))
        snap = await detector.detect_drift(X)
        assert snap.drift_score == pytest.approx(0.0, abs=1e-6)
        assert snap.is_drifted is False

    @pytest.mark.asyncio
    async def test_detect_drift_significant_drift(self) -> None:
        cfg = _make_cfg(drift_threshold=0.15)
        detector = DriftDetector(db_pool=None, settings=cfg)
        # Mean shifted by 2σ → high drift
        X = np.ones((50, 20)) * 2.0
        snap = await detector.detect_drift(X)
        assert snap.drift_score > 0.5
        assert snap.is_drifted is True

    @pytest.mark.asyncio
    async def test_detect_drift_empty_features(self) -> None:
        detector = DriftDetector(db_pool=None)
        X = np.zeros((0, 20))
        snap = await detector.detect_drift(X)
        assert snap.drift_score == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_detect_performance_drop_no_pool(self) -> None:
        detector = DriftDetector(db_pool=None)
        snap = await detector.detect_performance_drop()
        assert snap.drift_score == pytest.approx(0.0, abs=1e-6)
        assert snap.should_rollback is False

    @pytest.mark.asyncio
    async def test_persist_snapshot_no_pool(self) -> None:
        detector = DriftDetector(db_pool=None)
        snap = DriftSnapshot(drift_score=0.1, is_drifted=False)
        result = await detector.persist_snapshot(snap)
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_latest_snapshot_no_pool(self) -> None:
        detector = DriftDetector(db_pool=None)
        result = await detector.fetch_latest_snapshot()
        assert result is None

    @pytest.mark.asyncio
    async def test_run_all_checks_merges_snapshots(self) -> None:
        detector = DriftDetector(db_pool=None)
        X = np.zeros((20, 20))
        merged = await detector.run_all_checks(X)
        assert isinstance(merged, DriftSnapshot)
        assert merged.drift_score >= 0

    @pytest.mark.asyncio
    async def test_baseline_initialised_after_first_call(self) -> None:
        detector = DriftDetector(db_pool=None)
        assert detector._baseline_mean is None
        X = np.random.default_rng(1).standard_normal((10, 20))
        await detector.detect_drift(X)
        assert detector._baseline_mean is not None
        assert detector._baseline_mean.shape == (20,)


class TestDriftDetectorWithPool:
    def _make_pool_with_rows(self, recent, old):
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[recent, old])
        conn.fetchrow = AsyncMock(return_value=None)
        conn.execute = AsyncMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        return pool

    @pytest.mark.asyncio
    async def test_performance_drop_no_data_defaults_to_half(self) -> None:
        pool = self._make_pool_with_rows([], [])
        detector = DriftDetector(db_pool=pool)
        snap = await detector.detect_performance_drop()
        # Both precisions default to 0.5 → no drop
        assert snap.should_rollback is False

    @pytest.mark.asyncio
    async def test_performance_drop_triggers_rollback(self) -> None:
        def make_row(status: str):
            r = MagicMock()
            r.__getitem__ = lambda self, k: status if k == "resolution_status" else 0.8
            return r

        recent = [make_row("full_refund")] * 10   # 0% success
        old = [make_row("successful")] * 10         # 100% success

        pool = self._make_pool_with_rows(recent, old)
        cfg = _make_cfg(performance_drop_threshold=0.05)
        detector = DriftDetector(db_pool=pool, settings=cfg)
        snap = await detector.detect_performance_drop()
        assert snap.should_rollback is True
        assert snap.precision_drop > 0

    @pytest.mark.asyncio
    async def test_performance_drop_db_failure_returns_safe_default(self) -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=RuntimeError("db dead"))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        detector = DriftDetector(db_pool=pool)
        snap = await detector.detect_performance_drop()
        assert snap.should_rollback is False

    @pytest.mark.asyncio
    async def test_persist_snapshot(self) -> None:
        conn = AsyncMock()
        conn.execute = AsyncMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        detector = DriftDetector(db_pool=pool)
        snap = DriftSnapshot(drift_score=0.2, is_drifted=True)
        ok = await detector.persist_snapshot(snap)
        assert ok is True
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_snapshot_failure(self) -> None:
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=RuntimeError("write error"))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        detector = DriftDetector(db_pool=pool)
        snap = DriftSnapshot(drift_score=0.2, is_drifted=True)
        ok = await detector.persist_snapshot(snap)
        assert ok is False


class TestKSDistance:
    def test_zero_distance_identical(self) -> None:
        detector = DriftDetector(db_pool=None)
        detector._baseline_mean = np.zeros(20)
        detector._baseline_std = np.ones(20)
        X = np.zeros((30, 20))
        dist = detector._compute_ks_distance(X)
        assert dist == pytest.approx(0.0, abs=1e-6)

    def test_large_distance_shifted(self) -> None:
        detector = DriftDetector(db_pool=None)
        detector._baseline_mean = np.zeros(20)
        detector._baseline_std = np.ones(20)
        X = np.ones((30, 20)) * 5.0
        dist = detector._compute_ks_distance(X)
        assert dist == pytest.approx(1.0)  # clamped to 1.0

    def test_empty_array(self) -> None:
        detector = DriftDetector(db_pool=None)
        detector._baseline_mean = np.zeros(20)
        dist = detector._compute_ks_distance(np.zeros((0, 20)))
        assert dist == 0.0


class TestComputePrecision:
    def test_all_successful(self) -> None:
        rows = [{"resolution_status": "successful"}] * 10
        p = DriftDetector._compute_precision(rows)
        assert p == pytest.approx(1.0)

    def test_all_failed(self) -> None:
        rows = [{"resolution_status": "full_refund"}] * 10
        p = DriftDetector._compute_precision(rows)
        assert p == pytest.approx(0.0)

    def test_mixed(self) -> None:
        rows = [{"resolution_status": "successful"}] * 3 + [{"resolution_status": "dispute"}] * 7
        p = DriftDetector._compute_precision(rows)
        assert p == pytest.approx(0.3)

    def test_empty_defaults_to_half(self) -> None:
        p = DriftDetector._compute_precision([])
        assert p == pytest.approx(0.5)
