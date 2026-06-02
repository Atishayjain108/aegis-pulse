"""Tests for the cross-phase bridges (Phase 2 agents + Phase 4 escalation)."""

from __future__ import annotations

import json

import pytest

from aegis.comply.bridge.agents_bridge import evaluate_for_compliance_node
from aegis.comply.bridge.phase4_bridge import maybe_escalate, to_escalation_fields
from aegis.comply.constants import COMPLIANCE_ESCALATION_STREAM
from aegis.comply.engine import ComplianceEngine
from aegis.comply.schemas import ComplianceRequest, ComplianceVerdict


# ---- Phase 2 agents bridge -------------------------------------------------
def test_agents_bridge_clean_maps_to_proceed():
    out = evaluate_for_compliance_node(
        {"trend_id": "a", "title": "plain reusable bottle", "price": 20.0}
    )
    assert out["agent"] == "compliance"
    assert out["verdict"] == "proceed"
    assert 0.0 <= out["confidence"] <= 1.0


def test_agents_bridge_health_claim_maps_to_block():
    out = evaluate_for_compliance_node(
        {"trend_id": "b", "title": "gummies", "claims": ["cures diabetes"], "category": "supplements"}
    )
    assert out["verdict"] == "block"
    assert out["compliance"]["verdict"] == "block"


def test_agents_bridge_trademark_maps_to_escalate():
    out = evaluate_for_compliance_node(
        {"trend_id": "c", "title": "Gucci bag", "brand_mentions": ["Gucci"], "price": 300.0}
    )
    assert out["verdict"] == "escalate"


def test_agents_bridge_fails_closed_on_error():
    class _Boom(ComplianceEngine):
        def evaluate(self, request):  # type: ignore[override]
            raise RuntimeError("kaboom")

    out = evaluate_for_compliance_node({"trend_id": "d", "title": "x"}, engine=_Boom())
    assert out["verdict"] == "escalate"
    assert out["confidence"] == 0.30


def test_agents_bridge_accepts_id_alias_and_missing_fields():
    out = evaluate_for_compliance_node({"id": "via-alias"})
    assert out["agent"] == "compliance"
    assert out["verdict"] in {"proceed", "escalate", "block"}


# ---- Phase 4 escalation bridge ---------------------------------------------
class _FakePublisher:
    def __init__(self):
        self.calls = []

    async def xadd(self, stream, fields, *, maxlen=None, approximate=True):
        self.calls.append((stream, fields, maxlen))
        return "1-0"


class _FakeKillSwitch:
    def __init__(self):
        self.tripped = []

    async def trip(self, reason):
        self.tripped.append(reason)


def _block_result():
    eng = ComplianceEngine()
    res = eng.evaluate(
        ComplianceRequest(
            trend_id="esc",
            title="gummies",
            claims=("cures diabetes",),
            category="supplements",
        )
    )
    assert res.verdict is ComplianceVerdict.BLOCK
    return res


@pytest.mark.asyncio
async def test_escalation_published_for_block():
    pub = _FakePublisher()
    published = await maybe_escalate(_block_result(), tenant_id="tenant-1", publisher=pub)
    assert published is True
    stream, fields, maxlen = pub.calls[0]
    assert stream == COMPLIANCE_ESCALATION_STREAM
    assert "body" in fields
    body = json.loads(fields["body"])
    assert body["verdict"] == "block"


@pytest.mark.asyncio
async def test_no_escalation_for_clear_verdict():
    eng = ComplianceEngine()
    clear = eng.evaluate(ComplianceRequest(trend_id="ok", title="plain mug", price=10.0))
    pub = _FakePublisher()
    published = await maybe_escalate(clear, tenant_id="t", publisher=pub)
    assert published is False
    assert pub.calls == []


@pytest.mark.asyncio
async def test_killswitch_not_tripped_in_advisory_mode():
    ks = _FakeKillSwitch()
    await maybe_escalate(_block_result(), tenant_id="t", killswitch=ks, mode="advisory")
    assert ks.tripped == []


@pytest.mark.asyncio
async def test_escalation_never_raises_on_publisher_error():
    class _BadPublisher:
        async def xadd(self, *a, **k):
            raise ConnectionError("redis down")

    # Should swallow the error and report not-published rather than raising.
    published = await maybe_escalate(_block_result(), tenant_id="t", publisher=_BadPublisher())
    assert published is False


def test_to_escalation_fields_shape():
    fields = to_escalation_fields(_block_result(), tenant_id="tenant-9")
    assert set(fields) == {"body"}
    body = json.loads(fields["body"])
    assert body["tenant_id"] == "tenant-9"
    assert "checked_at" in body
