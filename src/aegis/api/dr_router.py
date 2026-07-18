"""Disaster-Recovery HTTP surface for the unified AEGIS API.

The full DR orchestrator lives in the standalone ``aegis-phase15`` module
(its own venv), so it is not importable from the main process. This router
exposes a lightweight, always-available ``/dr`` surface backed by the
in-namespace :mod:`aegis.backup` package (``BackupHealth``), and opportunistically
upgrades to the richer ``aegis.dr`` health checker when phase-15 happens to be
installed in the same environment. Every endpoint degrades gracefully — a
missing backup tool yields a structured ``unavailable`` status, never a 500.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter

_log = structlog.get_logger("aegis.api.dr")

router = APIRouter(prefix="/dr", tags=["disaster-recovery"])


@router.get("/status")
async def dr_status() -> dict[str, Any]:
    """RPO/RTO + backup staleness snapshot. Graceful when DR tooling absent."""
    # Prefer the standalone phase-15 health checker if co-installed.
    try:
        from aegis.dr.health import DrHealthChecker  # type: ignore[import-not-found]

        snapshot = await DrHealthChecker().get_sla_snapshot()
        return {"status": "ok", "source": "aegis.dr", "sla": snapshot}
    except Exception:
        pass

    # Fall back to the in-namespace backup health monitor.
    try:
        from aegis.backup.health import BackupHealth

        report = await BackupHealth().check()
        return {"status": "ok", "source": "aegis.backup", "health": report}
    except Exception as exc:
        _log.info("dr.status.unavailable", error=str(exc)[:200])
        return {
            "status": "unavailable",
            "message": "DR tooling not installed in this process",
            "rpo_target_s": 900,
            "rto_target_s": 3600,
        }


@router.get("/health")
async def dr_health() -> dict[str, Any]:
    """Liveness of the DR surface itself — always responds."""
    return {"status": "ok", "rpo_target_s": 900, "rto_target_s": 3600}
