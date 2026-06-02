"""Long-lived asyncio workers for Phase 4.

Two workers, one direction each:

* ``IntakeWorker`` consumes Phase 2 + Phase 3 results from their
  respective Redis streams, merges them within a 30-second window, and
  submits each merged input to a ``Pipeline``. Tests can drive it
  directly via ``submit_phase2_dict`` / ``submit_phase3_dict`` — no
  Redis required.

* ``run_drain_worker`` is a coroutine that assembles an
  ``AlertRepository`` + ``KillSwitch`` + ``ChannelRegistry`` +
  ``Drainer`` and runs them until cancelled. Used by the
  ``aegis-execute drain`` CLI subcommand and by deployment wrappers.

Both workers are written to fail loudly to logs, recover silently, and
never crash on a single bad message.
"""

from __future__ import annotations

from aegis.execute.workers.drain_worker import run_drain_worker
from aegis.execute.workers.intake_worker import (
    CONSUMER_GROUP,
    MERGE_WINDOW_S,
    STREAM_PHASE2,
    STREAM_PHASE3,
    IntakeWorker,
)

__all__ = [
    "CONSUMER_GROUP",
    "MERGE_WINDOW_S",
    "STREAM_PHASE2",
    "STREAM_PHASE3",
    "IntakeWorker",
    "run_drain_worker",
]
