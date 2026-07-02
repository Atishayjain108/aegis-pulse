"""Drainer end-to-end with the fake repo + recording notifiers.

Covers:
  * Successful delivery (single tick)
  * Retry-on-failure → success
  * Terminal failure after max attempts
  * Killswitch tripped → no delivery
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from aegis.execute.bridge.types import ComposerInput
from aegis.execute.killswitch.switch import KillSwitch
from aegis.execute.outbox.drainer import Drainer
from aegis.execute.pipeline import Pipeline
from tests.integration.execute.conftest import RecordingNotifier


def _make_drainer(*, repo, notifiers, killswitch, tenant_id, max_attempts=3, batch=10, interval=0.05):
    return Drainer(
        repository=repo,
        notifiers=notifiers,
        killswitch=killswitch,
        tenant_id=tenant_id,
        drain_interval_s=interval,
        drain_batch=batch,
        max_attempts=max_attempts,
    )


async def _seed_one_alert(repo, tenant_id):
    pipe = Pipeline(repository=repo)
    ci = ComposerInput(
        tenant_id=tenant_id,
        trend_id="t-d",
        phase2_verdict="HOLD",
        phase2_score=0.5,
        phase2_confidence=0.6,
        phase2_priority=2,
    )
    out = await pipe.submit(ci)
    assert out.persisted is True
    return out.alert


async def test_drainer_delivers_pending_alert(fake_repo):
    tid = uuid4()
    alert = await _seed_one_alert(fake_repo, tid)

    ks = KillSwitch(redis_client=None, fail_closed=False)
    recorder = RecordingNotifier(name="rec")
    drainer = _make_drainer(
        repo=fake_repo, notifiers=[recorder], killswitch=ks, tenant_id=str(tid)
    )

    # Run one tick manually
    await drainer._tick()
    # Allow async logging
    await asyncio.sleep(0)
    assert recorder.calls == 1
    assert fake_repo.outbox[alert.alert_id]["status"] == "delivered"
    assert any(d.alert_id == alert.alert_id for d in fake_repo.deliveries)


async def test_drainer_retries_then_succeeds(fake_repo):
    tid = uuid4()
    alert = await _seed_one_alert(fake_repo, tid)
    ks = KillSwitch(redis_client=None, fail_closed=False)
    recorder = RecordingNotifier(name="rec", fail_first_n=1)
    drainer = _make_drainer(
        repo=fake_repo, notifiers=[recorder], killswitch=ks, tenant_id=str(tid), max_attempts=5
    )

    # Tick 1 → fails, mark_retry pushes next_retry_at into the future.
    await drainer._tick()
    row = fake_repo.outbox[alert.alert_id]
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    # Force retry-due
    row["next_retry_at"] = None
    # Tick 2 → succeeds
    await drainer._tick()
    assert fake_repo.outbox[alert.alert_id]["status"] == "delivered"
    assert recorder.calls == 2


async def test_drainer_terminal_after_max_attempts(fake_repo):
    tid = uuid4()
    alert = await _seed_one_alert(fake_repo, tid)
    ks = KillSwitch(redis_client=None, fail_closed=False)
    recorder = RecordingNotifier(name="rec", fail_first_n=99)  # always fails
    drainer = _make_drainer(
        repo=fake_repo, notifiers=[recorder], killswitch=ks, tenant_id=str(tid), max_attempts=2
    )

    # Tick 1
    await drainer._tick()
    fake_repo.outbox[alert.alert_id]["next_retry_at"] = None  # force retry-due
    # Tick 2 — should mark terminal=True (attempts=2 reaches max)
    await drainer._tick()
    assert fake_repo.outbox[alert.alert_id]["status"] == "failed"


async def test_drainer_skips_when_killswitch_tripped(fake_repo):
    tid = uuid4()
    await _seed_one_alert(fake_repo, tid)

    class _AlwaysTrippedKS:
        async def is_tripped(self):
            return True

    ks: KillSwitch = _AlwaysTrippedKS()  # type: ignore[assignment]
    recorder = RecordingNotifier(name="rec")
    drainer = _make_drainer(
        repo=fake_repo, notifiers=[recorder], killswitch=ks, tenant_id=str(tid)
    )
    await drainer._tick()
    assert recorder.calls == 0


async def test_drainer_start_stop_lifecycle(fake_repo):
    tid = uuid4()
    await _seed_one_alert(fake_repo, tid)
    ks = KillSwitch(redis_client=None, fail_closed=False)
    recorder = RecordingNotifier(name="rec")
    drainer = _make_drainer(
        repo=fake_repo,
        notifiers=[recorder],
        killswitch=ks,
        tenant_id=str(tid),
        interval=0.02,
    )
    await drainer.start()
    # Give it a few ticks
    await asyncio.sleep(0.10)
    await drainer.stop()
    assert recorder.calls >= 1
