"""Tests for the compliance audit repository using a fake asyncpg pool."""

from __future__ import annotations

import pytest

from aegis.comply.engine import ComplianceEngine
from aegis.comply.errors import AuditWriteError
from aegis.comply.schemas import ComplianceRequest
from aegis.comply.store.audit_repo import ComplianceAuditRepository


class _FakeConn:
    def __init__(self, fail_on_insert=False):
        self.executed = []
        self._fail = fail_on_insert

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if self._fail and "INSERT" in query:
            raise RuntimeError("db exploded")
        return "INSERT 0 1"


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


def _result():
    eng = ComplianceEngine()
    return eng.evaluate(ComplianceRequest(trend_id="audit-1", title="plain mug", price=10.0))


@pytest.mark.asyncio
async def test_record_sets_tenant_and_inserts():
    conn = _FakeConn()
    repo = ComplianceAuditRepository(_FakePool(conn), tenant_id="00000000-0000-0000-0000-000000000001")
    ok = await repo.record(_result())
    assert ok is True
    # First statement sets the RLS tenant GUC; second is the insert.
    assert "app.current_tenant" in conn.executed[0][0]
    assert "INSERT INTO compliance_audit" in conn.executed[1][0]


@pytest.mark.asyncio
async def test_record_passes_content_id_as_pk_arg():
    conn = _FakeConn()
    repo = ComplianceAuditRepository(_FakePool(conn), tenant_id="t-uuid")
    result = _result()
    await repo.record(result)
    insert_args = conn.executed[1][1]
    assert insert_args[0] == result.content_id  # $1 == content_id


@pytest.mark.asyncio
async def test_record_raises_typed_error_on_db_failure():
    conn = _FakeConn(fail_on_insert=True)
    repo = ComplianceAuditRepository(_FakePool(conn), tenant_id="t")
    with pytest.raises(AuditWriteError):
        await repo.record(_result())
