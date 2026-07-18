"""
aegis.dr.api
============
FastAPI router for Phase 15 — Disaster Recovery.

Mounts under the main Dashboard FastAPI app at ``/dr/*``.
Follows the same pattern as Phase 10 ``aegis.datalake.api``:
a standalone ``APIRouter`` that the dashboard's ``app.py`` includes.

Endpoints
---------
GET  /dr/health          → SlaSnapshot (current RPO/RTO status)
GET  /dr/status          → structured status dict for dashboard widgets
GET  /dr/backups         → list recent backup manifests per target
GET  /dr/backups/{target}/latest → latest manifest for a target
POST /dr/drill/trigger   → kick off an on-demand restore drill
GET  /dr/runbooks        → list available runbook failure modes
POST /dr/runbooks/{mode}/run → execute a runbook (dry_run=true by default)

Security
--------
Drill trigger and runbook execution require the ``X-Aegis-Ops-Key`` header
matching ``AEGIS_DR_OPS_KEY`` env var (default blocks if unset).
Never exposed publicly — dashboard ops console only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from aegis.dr.config import DisasterRecoverySettings, get_dr_settings
from aegis.dr.health import DrHealthChecker
from aegis.dr.runbooks import FAILURE_MODES, FailureModeCategory

_log = structlog.get_logger("aegis.dr.api")

router = APIRouter(prefix="/dr", tags=["disaster-recovery"])


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_settings(request: Request) -> DisasterRecoverySettings:
    """Pull settings from app.state if available, else use singleton."""
    return getattr(request.app.state, "dr_settings", get_dr_settings())


def _get_minio(request: Request) -> Any:
    return getattr(request.app.state, "minio_client", None)


def _get_redis(request: Request) -> Any:
    return getattr(request.app.state, "redis_client", None)


async def _require_ops_key(
    x_aegis_ops_key: str | None = Header(default=None),
    settings: DisasterRecoverySettings = Depends(_get_settings),
) -> None:
    """Guard sensitive ops endpoints with a pre-shared key."""
    import os

    expected = os.getenv("AEGIS_DR_OPS_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="AEGIS_DR_OPS_KEY not configured — ops endpoints disabled",
        )
    if x_aegis_ops_key != expected:
        raise HTTPException(status_code=403, detail="Invalid ops key")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health", response_model=None)
async def get_health(
    request: Request,
    settings: DisasterRecoverySettings = Depends(_get_settings),
) -> JSONResponse:
    """
    Return current SLA snapshot (RPO/RTO status, backup ages, last drill).

    This endpoint is polled by the Dashboard every 60s to populate the DR widget.
    """
    minio = _get_minio(request)
    redis = _get_redis(request)

    if minio is None:
        return JSONResponse(
            status_code=503,
            content={"error": "MinIO client not initialised"},
        )

    checker = DrHealthChecker(
        settings=settings,
        minio_client=minio,
        redis_client=redis,
    )

    try:
        snapshot = await checker.check()
        return JSONResponse(content=snapshot.model_dump(mode="json"))
    except Exception as exc:
        _log.error("api.dr.health.error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/status")
async def get_status(
    request: Request,
    settings: DisasterRecoverySettings = Depends(_get_settings),
) -> dict[str, Any]:
    """
    Simplified status dict for dashboard summary tiles.

    Returns a flat dict suitable for direct rendering in the ops console.
    """
    minio = _get_minio(request)
    if minio is None:
        return {"status": "unavailable", "reason": "MinIO not connected"}

    checker = DrHealthChecker(
        settings=settings,
        minio_client=minio,
    )

    try:
        snapshot = await checker.check()
        return {
            "overall_status": snapshot.overall_status.value,
            "rpo_target_s": settings.rpo_target_s,
            "rto_target_s": settings.rto_target_s,
            "last_backup_ages_s": snapshot.last_backup_ages_s,
            "last_drill_outcome": (
                snapshot.last_drill_outcome.value
                if snapshot.last_drill_outcome
                else None
            ),
            "last_drill_at": (
                snapshot.last_drill_at.isoformat()
                if snapshot.last_drill_at
                else None
            ),
            "alerts": snapshot.active_alerts,
            "captured_at": snapshot.captured_at.isoformat(),
        }
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


@router.get("/backups")
async def list_backups(
    request: Request,
    target: str | None = None,
    limit: int = 10,
    settings: DisasterRecoverySettings = Depends(_get_settings),
) -> list[dict[str, Any]]:
    """
    List recent backup manifests, optionally filtered by target.

    Returns up to ``limit`` manifests, newest first.
    """
    minio = _get_minio(request)
    if minio is None:
        raise HTTPException(status_code=503, detail="MinIO not connected")

    prefix = f"{target}/" if target else ""
    try:
        objects = sorted(
            await asyncio.to_thread(
                lambda: list(
                    minio.list_objects(
                        settings.dr_bucket,
                        prefix=prefix,
                        recursive=True,
                    )
                )
            ),
            key=lambda o: o.last_modified or datetime.min,
            reverse=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    manifests = []
    for obj in objects:
        if not obj.object_name.endswith("_manifest.json"):
            continue
        if len(manifests) >= limit:
            break
        try:
            response = await asyncio.to_thread(
                minio.get_object, settings.dr_bucket, obj.object_name
            )
            from aegis.dr.schemas import BackupManifest

            m = BackupManifest.model_validate_json(response.read())
            manifests.append(m.model_dump(mode="json"))
        except Exception as exc:
            _log.warning("api.dr.list_backups.read_error", obj=obj.object_name, error=str(exc))
            continue

    return manifests


@router.post(
    "/drill/trigger",
    dependencies=[Depends(_require_ops_key)],
)
async def trigger_drill(
    request: Request,
    background_tasks: BackgroundTasks,
    dry_run: bool = True,
    settings: DisasterRecoverySettings = Depends(_get_settings),
) -> dict[str, str]:
    """
    Trigger an on-demand restore drill.

    Runs in the background so the HTTP response returns immediately.
    ``dry_run=true`` (default) — safe to call from the dashboard ops console.
    ``dry_run=false`` — performs a real restore into the drill DB.

    Requires ``X-Aegis-Ops-Key`` header.
    """
    from aegis.dr.drill import RestoreDrill

    minio = _get_minio(request)
    redis = _get_redis(request)

    if minio is None:
        raise HTTPException(status_code=503, detail="MinIO not connected")

    drill = RestoreDrill(
        settings=settings,
        minio_client=minio,
        redis_client=redis,
    )

    background_tasks.add_task(drill.run, dry_run=dry_run)

    return {
        "status": "accepted",
        "dry_run": str(dry_run),
        "message": "Drill started in background — check /dr/health for results",
    }


@router.get("/runbooks")
async def list_runbooks() -> list[dict[str, Any]]:
    """List all available runbook failure modes with metadata."""
    return [
        {
            "category": mode.category.value,
            "title": mode.title,
            "severity": mode.severity,
            "expected_rto_s": mode.expected_rto_s,
            "runbook_path": mode.runbook_path,
            "automated_steps": mode.automated_steps,
            "manual_steps": mode.manual_steps,
        }
        for mode in FAILURE_MODES.values()
    ]


@router.post(
    "/runbooks/{mode}/run",
    dependencies=[Depends(_require_ops_key)],
)
async def run_runbook(
    mode: str,
    request: Request,
    dry_run: bool = True,
    settings: DisasterRecoverySettings = Depends(_get_settings),
) -> dict[str, Any]:
    """
    Execute a runbook for the given failure mode.

    Always defaults to ``dry_run=true`` — pass ``?dry_run=false`` only when
    you intend real remediation.
    """
    try:
        category = FailureModeCategory(mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown failure mode '{mode}'. "
            f"Valid: {[m.value for m in FailureModeCategory]}",
        ) from exc

    minio = _get_minio(request)
    redis = _get_redis(request)

    from aegis.dr import runbooks

    try:
        if category == FailureModeCategory.PG_CORRUPTION:
            plan = await runbooks.run_pg_corruption_recovery(
                settings=settings, minio_client=minio, dry_run=dry_run
            )
        elif category == FailureModeCategory.REDIS_OOM:
            plan = await runbooks.run_redis_oom_recovery(
                redis_client=redis, dry_run=dry_run
            )
        elif category == FailureModeCategory.DISK_FULL:
            plan = await runbooks.run_disk_full_recovery(dry_run=dry_run)
        elif category == FailureModeCategory.DOCKER_DEAD:
            plan = await runbooks.run_docker_dead_recovery(dry_run=dry_run)
        else:
            raise HTTPException(
                status_code=501, detail=f"Runbook for '{mode}' not yet automated"
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return plan.model_dump(mode="json")
