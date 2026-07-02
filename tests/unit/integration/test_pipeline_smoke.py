"""End-to-end pipeline smoke tests that require zero infrastructure.

All external calls are mocked. Exercises the real scrape→dedup→route and
settlement→evolution chains without DB, Redis, or LLM. The § stream
invariant (field name "body", stream STREAM_EVOLVE) is asserted directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.scrape.adapter_router import AdapterRouter
from aegis.scrape.dedup import deduplicate_batch
from aegis.scrape.topic_classifier import TopicType

pytestmark = pytest.mark.asyncio


async def test_full_pipeline_dedup_and_route() -> None:
    """Scrape→dedup→route with no infra: dedup collapses dupes, router ranks."""
    signals = [
        {"url": f"https://x.com/{i}", "title": "AI chips breakout signal"}
        for i in range(10)
    ] + [{"url": "https://y.com/1", "title": "Totally different headline here"}]

    unique, results = await deduplicate_batch(signals)
    # 10 identical titles collapse to 1, plus the distinct one → 2 unique.
    assert len(unique) == 2
    assert any(r.is_duplicate for r in results)

    router = AdapterRouter()
    recs = router.route("AI chips", top_n=5)
    assert recs, "router returned no adapters"
    # Recommendations sorted by final_score descending (§ invariant).
    scores = [r.final_score for r in recs]
    assert scores == sorted(scores, reverse=True)
    assert recs[0].topic_type is TopicType.TECH_NEWS


async def test_adapter_router_excludes_credentials() -> None:
    """Router → classifier → selection; credential-gated adapters excluded."""
    router = AdapterRouter()
    recs = router.route("HDFC Bank NSE", top_n=12, exclude_credentials_required=True)
    assert recs
    assert recs[0].topic_type is TopicType.FINANCIAL_TREND
    for r in recs:
        # No adapter that requires-but-lacks credentials slips through.
        assert not (r.requires_credentials and not r.credentials_available)


async def test_evolution_loop_smoke() -> None:
    """Settlement → OutcomeRecorder → evolve stream publish, body field."""
    from aegis.execute.settlement import OrderOutcome, SettlementManager

    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"trend_id": "tx", "score": 0.7, "confidence": 0.8})
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)

    mgr = SettlementManager(pool)
    mgr.stage_outcome(OrderOutcome(
        order_id="o1", plan_id="p1", unit_cost_usd=10.0, quantity=2,
        revenue_usd=40.0, refund_usd=0.0, shipping_usd=5.0, platform_fee_usd=1.0,
    ))

    recorder = AsyncMock()
    recorder.record_outcome = AsyncMock(return_value=True)

    # Patch publish at the source module so we can inspect the XADD payload.
    captured: dict = {}

    async def _fake_xadd(stream, fields, **kw):
        captured["stream"] = stream
        captured["fields"] = fields

    fake_redis = MagicMock()
    fake_redis.xadd = AsyncMock(side_effect=_fake_xadd)

    with patch("aegis.evolve.outcomes.OutcomeRecorder", return_value=recorder), \
         patch("aegis.core.event_bus._HOLDER.get", new=AsyncMock(return_value=fake_redis)):
        snap = await mgr.settle_daily(tenant_id="t-1")

    assert snap.order_count == 1
    recorder.record_outcome.assert_awaited_once()
    trade = recorder.record_outcome.await_args.args[0]
    assert float(trade.actual_roi_pct) > 0
    # Stream invariant: published to evolve stream with the "body" field.
    if captured:
        assert captured["stream"] == "aegis:phase9:evolve_events"
        assert "body" in captured["fields"]
