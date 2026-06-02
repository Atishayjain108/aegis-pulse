"""End-to-end engine tests: clear / flag / block paths and determinism."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.comply.engine import ComplianceEngine
from aegis.comply.schemas import ComplianceRequest, ComplianceVerdict


def test_clean_request_is_clear(engine, clean_request):
    result = engine.evaluate(clean_request)
    assert result.verdict is ComplianceVerdict.CLEAR
    assert result.risk_score == 0.0
    assert result.rule_hits == ()
    assert result.trademark_matches == ()
    assert result.blocking_reasons == ()


def test_health_claim_is_blocked(engine, health_claim_request):
    result = engine.evaluate(health_claim_request)
    assert result.verdict is ComplianceVerdict.BLOCK
    rule_ids = {h.rule_id for h in result.rule_hits}
    assert "FTC-HEALTH-001" in rule_ids
    assert result.blocking_reasons  # non-empty
    assert result.remediation  # remediation guidance present


def test_trademark_mention_is_flagged(engine):
    req = ComplianceRequest(
        trend_id="tm-1",
        title="Gucci handbag",
        brand_mentions=("Gucci",),
        category="fashion",
        price=300.0,
    )
    result = engine.evaluate(req)
    assert result.verdict is ComplianceVerdict.FLAG
    assert any(m.mark == "gucci" for m in result.trademark_matches)


def test_typosquat_plus_cheap_price_blocks(engine):
    req = ComplianceRequest(
        trend_id="cf-both",
        title="Addidas running shoes",
        brand_mentions=("Addidas",),
        category="footwear",
        price=3.0,
        price_baseline=(120.0, 20.0),
    )
    result = engine.evaluate(req)
    assert result.verdict is ComplianceVerdict.BLOCK
    assert any(s.risk >= 0.90 for s in result.counterfeit_signals)


def test_engine_is_deterministic(engine, health_claim_request):
    a = engine.evaluate(health_claim_request)
    b = engine.evaluate(health_claim_request)
    assert a.verdict == b.verdict
    assert a.risk_score == b.risk_score
    assert a.content_id == b.content_id


def test_clock_is_injectable():
    ts = datetime(2030, 6, 1, 0, 0, 0, tzinfo=UTC)
    eng = ComplianceEngine(clock=lambda: ts)
    result = eng.evaluate(ComplianceRequest(trend_id="x", title="plain item"))
    assert result.checked_at == ts


def test_engine_version_stamped(engine, clean_request):
    from aegis.comply import VERSION

    assert engine.evaluate(clean_request).engine_version == VERSION


@pytest.mark.asyncio
async def test_aevaluate_without_augmentor_matches_sync(engine, health_claim_request):
    sync = engine.evaluate(health_claim_request)
    a = await engine.aevaluate(health_claim_request, augmentor=None)
    assert a.verdict == sync.verdict
    assert a.augmented is False
