"""Drain worker entry point.

Assembles AlertRepository + KillSwitch + ChannelRegistry + Drainer and
runs them until cancelled. Used by `aegis-execute drain` CLI command and
by the FastAPI lifespan when `mode=drain` is selected.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from aegis.execute.config import ExecuteSettings, get_execute_settings
from aegis.execute.killswitch.switch import KillSwitch
from aegis.execute.notifiers.base import ChannelRegistry
from aegis.execute.outbox.drainer import Drainer
from aegis.execute.store.repository import AlertRepository

_log = structlog.get_logger(__name__)


async def run_drain_worker(
    *,
    tenant_id: str,
    pool: Any | None = None,
    redis_client: Any | None = None,
    settings: ExecuteSettings | None = None,
) -> None:
    """Run the drainer until cancelled."""
    settings = settings or get_execute_settings()
    repo = AlertRepository(pool=pool)
    killswitch = KillSwitch(
        redis_client=redis_client,
        key=settings.killswitch_key,
        # Without Redis, default to ARMED in dev: a missing Redis is not a
        # crash signal here — operators can re-trip via the API once they
        # bring the backend back. (Production deployments always provide
        # one.)
        fail_closed=redis_client is not None,
    )
    registry = ChannelRegistry.from_settings(settings)
    drainer = Drainer(
        repository=repo,
        notifiers=registry.enabled_notifiers(),
        killswitch=killswitch,
        tenant_id=tenant_id,
        drain_interval_s=settings.drain_interval_s,
        drain_batch=settings.drain_batch,
        max_attempts=settings.notify_max_attempts,
    )
    _log.info(
        "execute.drain_worker.starting",
        tenant_id=tenant_id,
        notifiers=[n.name for n in registry.enabled_notifiers()],
    )
    await drainer.start()
    try:
        # Run until cancelled.
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        _log.info("execute.drain_worker.cancelled")
        raise
    finally:
        await drainer.stop()
        await registry.aclose_all()


__all__ = ["run_drain_worker"]
