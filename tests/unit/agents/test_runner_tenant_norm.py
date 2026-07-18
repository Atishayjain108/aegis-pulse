"""Regression for audit P6-1: run_trend must resolve the placeholder tenant
"default" to the canonical UUID so calibration/skill/RLS lookups key correctly.

Before the fix, dashboard/CLI analyze passed the literal string "default", which
never matched the UUID the trust maps are stored under, so calibration silently
never applied and every verdict shipped raw UNVERIFIED confidence.
"""

from __future__ import annotations

from aegis.agents import runner
from aegis.constants import DEFAULT_TENANT_UUID


async def test_publish_forwards_tenant_to_calibration(monkeypatch):
    """_publish_phase2_result hands the tenant it is given to the calibration
    lookup — so run_trend must give it the UUID, not the 'default' placeholder."""
    seen: dict[str, str] = {}

    async def _capture(raw_confidence: float, tenant_id: str):
        seen["tenant"] = tenant_id
        return raw_confidence, False

    async def _no_skill(_tenant: str):
        return None

    monkeypatch.setattr(runner, "_calibrate_confidence", _capture)
    monkeypatch.setattr(runner, "_recent_model_skill", _no_skill)

    from datetime import UTC, datetime

    from aegis.agents.schemas import AgentVerdict, GraphResult

    gr = GraphResult(
        trend_id="t-1",
        correlation_id="c-1",
        final_verdict=AgentVerdict.HOLD,
        final_score=0.5,
        final_confidence=0.6,
        final_priority=3,
        halt_reason="completed",
        decisions=[],
        blocked_by=[],
        started_at=datetime.now(tz=UTC),
        finished_at=datetime.now(tz=UTC),
        duration_ms=1.0,
    )

    class _Redis:
        async def xadd(self, *a, **k):
            return b"1-0"

    await runner._publish_phase2_result(_Redis(), gr, DEFAULT_TENANT_UUID)
    assert seen["tenant"] == DEFAULT_TENANT_UUID


def test_run_trend_normalizes_default_placeholder():
    """Source-level guard: the run_trend body must translate the 'default'
    placeholder to the configured tenant UUID so the branch can't be dropped."""
    import inspect

    src = inspect.getsource(runner.run_trend)
    assert 'tenant_id in ("default"' in src
    assert "default_tenant_id" in src
