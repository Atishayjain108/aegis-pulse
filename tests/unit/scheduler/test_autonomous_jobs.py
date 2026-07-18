"""ORPH-4 — autonomous scheduler datalake refresh job.

The job is the direct (Prefect-less) fallback for the daily lake refresh.
Contract: delegates to `daily_lake_refresh`, never raises — scheduler jobs
log failures instead of crashing the loop.
"""

from __future__ import annotations

from typing import Any

import pytest

from aegis.scheduler import autonomous


async def test_job_datalake_refresh_invokes_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _fake_flow(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"date": "2026-06-11", "bronze": {"skipped": True}}

    from aegis.datalake.orchestration import flows

    monkeypatch.setattr(flows, "daily_lake_refresh", _fake_flow)
    await autonomous.job_datalake_refresh()
    assert len(calls) == 1


async def test_job_datalake_refresh_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("lake on fire")

    from aegis.datalake.orchestration import flows

    monkeypatch.setattr(flows, "daily_lake_refresh", _boom)
    await autonomous.job_datalake_refresh()  # logged, not raised


def test_datalake_job_function_exists() -> None:
    # Registration guard: the scheduler module exposes the job for cron wiring.
    assert callable(autonomous.job_datalake_refresh)


# ---------------------------------------------------------------------------
# PASS3-3C — shadow evaluation job
# ---------------------------------------------------------------------------


def test_shadow_job_function_exists() -> None:
    # Registration guard: the scheduler module exposes the job for cron wiring.
    assert callable(autonomous.job_shadow_evaluate)


async def test_job_shadow_evaluate_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncpg

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("pg down")

    monkeypatch.setattr(asyncpg, "create_pool", _boom)
    await autonomous.job_shadow_evaluate()  # logged, not raised


async def test_job_shadow_evaluate_delegates_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    import asyncpg

    from aegis.evolve import retrain as retrain_mod

    fake_pool = MagicMock()
    fake_pool.close = AsyncMock()

    async def _fake_create_pool(*_a: Any, **_k: Any) -> Any:
        return fake_pool

    evaluate = AsyncMock(return_value=[{"candidate_id": "c1", "promoted": True}])
    monkeypatch.setattr(asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(retrain_mod.RetrainingPipeline, "evaluate_shadows", evaluate)

    await autonomous.job_shadow_evaluate()

    evaluate.assert_awaited_once()
    fake_pool.close.assert_awaited_once()
