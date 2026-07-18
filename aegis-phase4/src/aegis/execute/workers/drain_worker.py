"""Drain worker entry point.

Assembles AlertRepository + KillSwitch + ChannelRegistry + Drainer and
runs them until cancelled. Used by `aegis-execute drain` CLI command and
by the FastAPI lifespan when `mode=drain` is selected.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import structlog

from aegis.execute.config import ExecuteSettings, get_execute_settings
from aegis.execute.killswitch.switch import KillSwitch
from aegis.execute.notifiers.base import ChannelRegistry
from aegis.execute.outbox.drainer import Drainer
from aegis.execute.store.repository import AlertRepository

_log = structlog.get_logger(__name__)

# Liveness heartbeat: refresh every INTERVAL, expire after TTL (> interval so a
# momentary stall doesn't flap the dashboard, short enough to self-clear fast).
HEARTBEAT_INTERVAL_S = 10.0
HEARTBEAT_TTL_S = 30


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
        # Run until cancelled, publishing a liveness heartbeat so the dashboard
        # (`/api/pipeline/live`) can report the worker as running. The key has a
        # short TTL so it self-clears within ~30s of the worker dying.
        while True:
            if redis_client is not None:
                try:
                    await redis_client.set(
                        "aegis:execute:drain:running", "1", ex=HEARTBEAT_TTL_S
                    )
                except Exception as exc:  # heartbeat is best-effort, never fatal
                    _log.debug("execute.drain_worker.heartbeat_failed", error=str(exc))
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
    except asyncio.CancelledError:
        _log.info("execute.drain_worker.cancelled")
        raise
    finally:
        await drainer.stop()
        await registry.aclose_all()
        if redis_client is not None:
            with suppress(Exception):
                await redis_client.delete("aegis:execute:drain:running")


__all__ = ["run_drain_worker"]
