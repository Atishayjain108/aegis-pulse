"""AlertRepository: no-pool error path coverage.

The real repo wraps asyncpg; tests use FakeRepository for behaviour. But
the production class must raise a typed `AegisExecuteError` rather than
crash on `pool=None`. These tests pin that contract.
"""

from __future__ import annotations

import pytest

from aegis.execute.errors import AegisExecuteError
from aegis.execute.store.repository import AlertRepository


async def test_insert_alert_without_pool_raises_typed_error():
    from uuid import uuid4

    from aegis.execute.schemas.alert import Alert, AlertSource

    repo = AlertRepository(pool=None)
    a = Alert(
        alert_id="a" * 32,
        tenant_id=uuid4(),
        trend_id="t",
        verdict="HOLD",
        priority=2,
        score=0.4,
        confidence=0.5,
        source=AlertSource.PHASE2_ONLY,
        title="t — HOLD",
    )
    with pytest.raises(AegisExecuteError) as ei:
        await repo.insert_alert(a)
    assert ei.value.spec.code == "AEGIS-EXEC-0026"


async def test_get_alert_without_pool_raises():
    repo = AlertRepository(pool=None)
    with pytest.raises(AegisExecuteError):
        await repo.get_alert(tenant_id="00000000-0000-0000-0000-000000000000", alert_id="x")


async def test_list_recent_alerts_without_pool_raises():
    repo = AlertRepository(pool=None)
    with pytest.raises(AegisExecuteError):
        await repo.list_recent_alerts(tenant_id="00000000-0000-0000-0000-000000000000")


async def test_upsert_outbox_without_pool_raises():
    from uuid import uuid4

    from aegis.execute.schemas.alert import Alert, AlertSource

    repo = AlertRepository(pool=None)
    a = Alert(
        alert_id="b" * 32,
        tenant_id=uuid4(),
        trend_id="t",
        verdict="HOLD",
        priority=2,
        score=0.4,
        confidence=0.5,
        source=AlertSource.PHASE2_ONLY,
        title="t — HOLD",
    )
    with pytest.raises(AegisExecuteError):
        await repo.upsert_outbox_pending(a)


async def test_pending_count_without_pool_raises():
    repo = AlertRepository(pool=None)
    with pytest.raises(AegisExecuteError):
        await repo.pending_count(tenant_id="00000000-0000-0000-0000-000000000000")


async def test_mark_delivered_without_pool_raises():
    repo = AlertRepository(pool=None)
    with pytest.raises(AegisExecuteError):
        await repo.mark_delivered(
            tenant_id="00000000-0000-0000-0000-000000000000",
            alert_id="x",
        )


async def test_claim_pending_without_pool_raises():
    repo = AlertRepository(pool=None)
    with pytest.raises(AegisExecuteError):
        await repo.claim_pending(
            tenant_id="00000000-0000-0000-0000-000000000000",
            limit=5,
        )


async def test_claim_pending_zero_limit_returns_empty():
    """`limit=0` is a fast-path — no pool needed."""
    repo = AlertRepository(pool=None)
    result = await repo.claim_pending(
        tenant_id="00000000-0000-0000-0000-000000000000",
        limit=0,
    )
    assert result == []


async def test_list_recent_alerts_zero_limit_returns_empty():
    repo = AlertRepository(pool=None)
    result = await repo.list_recent_alerts(
        tenant_id="00000000-0000-0000-0000-000000000000",
        limit=0,
    )
    assert result == []
