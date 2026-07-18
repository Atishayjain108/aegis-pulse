"""Drainer notifier-exception coverage.

Validates that even a misbehaving notifier (raises from `send`) is
caught — the drainer records a FAILURE delivery and proceeds to retry.
"""

from __future__ import annotations

from uuid import uuid4

from aegis.execute.bridge.types import ComposerInput
from aegis.execute.killswitch.switch import KillSwitch
from aegis.execute.outbox.drainer import Drainer
from aegis.execute.pipeline import Pipeline


class _RaisingNotifier:
    """Notifier whose send() raises — contracts say it shouldn't, but
    the drainer must survive misbehaving channels."""

    def __init__(self) -> None:
        self.name = "raiser"
        self.enabled = True
        self.timeout_s = 1.0
        self.calls = 0

    async def send(self, envelope):
        self.calls += 1
        raise RuntimeError("simulated notifier crash")

    async def aclose(self) -> None:
        return None


async def test_drainer_survives_notifier_exception(fake_repo):
    tid = uuid4()
    pipe = Pipeline(repository=fake_repo)
    await pipe.submit(
        ComposerInput(
            tenant_id=tid,
            trend_id="t-raise",
            phase2_verdict="HOLD",
            phase2_score=0.5,
            phase2_confidence=0.6,
            phase2_priority=2,
        )
    )
    ks = KillSwitch(redis_client=None, fail_closed=False)
    raiser = _RaisingNotifier()
    drainer = Drainer(
        repository=fake_repo,
        notifiers=[raiser],
        killswitch=ks,
        tenant_id=str(tid),
        drain_interval_s=0.05,
        drain_batch=10,
        max_attempts=2,
    )
    await drainer._tick()
    # The notifier raised; the drainer logged a FAILURE delivery row
    # and bumped attempts in the outbox.
    assert raiser.calls == 1
    assert len(fake_repo.deliveries) >= 1
    # Outbox stayed visible (not delivered); attempts incremented.
    alert_id = next(iter(fake_repo.outbox.keys()))
    assert fake_repo.outbox[alert_id]["status"] in {"pending", "failed"}
    assert fake_repo.outbox[alert_id]["attempts"] >= 1
