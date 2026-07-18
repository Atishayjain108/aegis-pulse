"""Dashboard snapshot endpoint.

Returns a `DashboardSnapshot` — a single JSON payload the static UI can
render without SSE. The UI typically calls this once on load, then
subscribes to `/stream` for live updates.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from aegis.execute.api.auth import require_bearer
from aegis.execute.api.routes.alerts import _tenant_id  # reuse header dep
from aegis.execute.schemas.dashboard import DashboardSnapshot, MetricCard
from aegis.execute.store.repository import AlertRepository

router = APIRouter(tags=["dashboard"])


def _repo(request: Request) -> AlertRepository:
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="repo not initialised",
        )
    return repo


@router.get("/snapshot", response_model=DashboardSnapshot)
async def snapshot(
    _principal: Annotated[str, Depends(require_bearer)],
    tenant_id: Annotated[str, Depends(_tenant_id)],
    request: Request,
) -> DashboardSnapshot:
    repo = _repo(request)
    ks = getattr(request.app.state, "killswitch", None)

    recent = await repo.list_recent_alerts(tenant_id=tenant_id, limit=25)
    pending = await repo.pending_count(tenant_id=tenant_id)
    ks_state = await ks.state() if ks is not None else "UNKNOWN"

    by_verdict: dict[str, int] = {}
    for a in recent:
        by_verdict[a.verdict] = by_verdict.get(a.verdict, 0) + 1
    by_priority: dict[str, int] = {}
    for a in recent:
        by_priority[f"P{a.priority}"] = by_priority.get(f"P{a.priority}", 0) + 1

    cards = [
        MetricCard(label="Pending outbox", value=str(pending), hint="alerts awaiting delivery"),
        MetricCard(label="Killswitch", value=ks_state, hint="global dispatch halt"),
        MetricCard(label="Recent (25)", value=str(len(recent)), hint="latest alerts shown below"),
        MetricCard(label="ENTER", value=str(by_verdict.get("ENTER", 0)), hint="recent enters"),
        MetricCard(label="EXIT", value=str(by_verdict.get("EXIT", 0)), hint="recent exits"),
        MetricCard(label="BLOCK", value=str(by_verdict.get("BLOCK", 0)), hint="recent blocks"),
        MetricCard(label="P0", value=str(by_priority.get("P0", 0)), hint="critical priority"),
        MetricCard(label="P1", value=str(by_priority.get("P1", 0)), hint="high priority"),
    ]
    return DashboardSnapshot(
        killswitch_state=ks_state,
        pending_outbox=pending,
        recent_alerts=recent,
        cards=cards,
    )


__all__ = ["router"]
