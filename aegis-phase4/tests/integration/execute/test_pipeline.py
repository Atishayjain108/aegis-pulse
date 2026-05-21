"""End-to-end pipeline integration tests.

Exercises: Pipeline.submit → AlertRepository → OutboxWriter →
EventBus.publish → ExecutionIntent insert.
"""

from __future__ import annotations

from uuid import uuid4

from aegis.execute.bridge.types import ComposerInput
from aegis.execute.bus import EventBus
from aegis.execute.killswitch.switch import KillSwitch
from aegis.execute.pipeline import Pipeline


async def test_submit_persists_alert_and_outbox(fake_repo):
    pipe = Pipeline(repository=fake_repo)
    ci = ComposerInput(
        tenant_id=uuid4(),
        trend_id="t-int-1",
        phase2_verdict="ENTER",
        phase2_score=0.80,
        phase2_confidence=0.70,
        phase2_priority=1,
        phase3_p_breakout_24h=0.90,
        phase3_p_decline_6h=0.05,
        phase3_confidence=0.80,
        phase3_policy_action="enter",
        phase3_expected_margin_usd=3.50,
        phase3_loss_probability=0.15,
    )
    outcome = await pipe.submit(ci, unit_cost_usd=1.50)
    assert outcome.persisted is True
    assert outcome.alert is not None
    assert outcome.alert.verdict == "ENTER"
    assert outcome.intent is not None
    assert outcome.intent.kind == "enter_position"
    assert outcome.sizing is not None and outcome.sizing.units >= 0
    # Repo state
    assert len(fake_repo.alerts) == 1
    assert len(fake_repo.outbox) == 1
    assert fake_repo.outbox[outcome.alert.alert_id]["status"] == "pending"
    assert len(fake_repo.intents) == 1


async def test_submit_dedupes_repeat(fake_repo):
    pipe = Pipeline(repository=fake_repo)
    ci = ComposerInput(
        tenant_id=uuid4(),
        trend_id="t-int-2",
        phase2_verdict="HOLD",
        phase2_score=0.4,
        phase2_confidence=0.55,
        phase2_priority=2,
    )
    first = await pipe.submit(ci)
    second = await pipe.submit(ci)
    assert first.persisted is True
    assert second.persisted is False
    assert second.deduplicated is True
    assert len(fake_repo.alerts) == 1


async def test_submit_respects_killswitch(fake_repo):
    class _StubRedis:
        async def get(self, name):
            return b"TRIPPED"
        async def set(self, name, value):
            return True
        async def delete(self, *names):
            return 0
    ks = KillSwitch(redis_client=_StubRedis())
    pipe = Pipeline(repository=fake_repo, killswitch=ks)
    ci = ComposerInput(
        tenant_id=uuid4(),
        trend_id="t-killed",
        phase2_verdict="ENTER",
        phase2_score=0.8,
        phase2_confidence=0.7,
        phase2_priority=1,
    )
    outcome = await pipe.submit(ci)
    assert outcome.killswitch_tripped is True
    assert outcome.persisted is False
    assert len(fake_repo.alerts) == 0


async def test_submit_block_does_not_create_intent(fake_repo):
    pipe = Pipeline(repository=fake_repo)
    ci = ComposerInput(
        tenant_id=uuid4(),
        trend_id="t-blocked",
        phase2_verdict="BLOCK",
        phase2_score=0.9,
        phase2_confidence=0.9,
        phase2_halt_reason="compliance_block",
        phase2_blocked_by=("COMPLIANCE",),
    )
    outcome = await pipe.submit(ci)
    assert outcome.persisted is True
    assert outcome.alert is not None
    assert outcome.alert.verdict == "BLOCK"
    assert outcome.intent is None
    assert len(fake_repo.intents) == 0


async def test_submit_publishes_to_bus(fake_repo):
    bus = EventBus()
    sub = await bus.subscribe()
    pipe = Pipeline(repository=fake_repo, bus=bus)
    ci = ComposerInput(
        tenant_id=uuid4(),
        trend_id="t-bus",
        phase2_verdict="ENTER",
        phase2_score=0.8,
        phase2_confidence=0.7,
        phase2_priority=1,
        phase3_p_breakout_24h=0.85,
        phase3_confidence=0.7,
        phase3_policy_action="enter",
    )
    outcome = await pipe.submit(ci, unit_cost_usd=1.0)
    assert outcome.persisted is True
    # Pull an event off the bus
    import asyncio
    event = await asyncio.wait_for(sub.iter_events().__anext__(), timeout=0.5)
    assert event.event == "alert.created"
    assert event.data["trend_id"] == "t-bus"


async def test_submit_risk_gate_downgrades_to_block(fake_repo):
    # Low margin → ENTER gets blocked by margin gate.
    pipe = Pipeline(repository=fake_repo)
    ci = ComposerInput(
        tenant_id=uuid4(),
        trend_id="t-low-margin",
        phase2_verdict="ENTER",
        phase2_score=0.85,
        phase2_confidence=0.80,
        phase2_priority=1,
        phase3_p_breakout_24h=0.90,
        phase3_confidence=0.80,
        phase3_policy_action="enter",
        phase3_expected_margin_usd=0.10,  # below floor!
        phase3_loss_probability=0.10,
    )
    outcome = await pipe.submit(ci)
    assert outcome.alert is not None
    assert outcome.alert.verdict == "BLOCK"
    assert outcome.alert.halt_reason == "AEGIS-EXEC-0010"
    # No intent for a blocked alert.
    assert outcome.intent is None
