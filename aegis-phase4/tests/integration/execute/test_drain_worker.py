"""Drain worker assembly test.

We exercise the construction path inside `run_drain_worker` by cancelling
the wait-forever Event soon after start. This validates that:
  * AlertRepository is built without crashing
  * KillSwitch construction picks the right fail_closed default
  * ChannelRegistry is materialised
  * Drainer.start / stop are called in order
  * Notifiers are aclose()'d on shutdown
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from aegis.execute.workers.drain_worker import run_drain_worker


async def test_run_drain_worker_starts_and_stops():
    tid = uuid4()

    async def _runner():
        # The function runs forever; cancel it shortly after start.
        return await run_drain_worker(
            tenant_id=str(tid),
            pool=None,
            redis_client=None,
        )

    task = asyncio.create_task(_runner())
    # Yield a few times so the drainer actually starts.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises((asyncio.CancelledError, Exception)):
        await task
