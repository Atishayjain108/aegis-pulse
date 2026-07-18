"""Alerts API — list / get / ack.

All endpoints require the `X-Aegis-Tenant` header for the tenant UUID
(string). RLS in the database is what actually enforces isolation; this
header just lets the repository set `app.current_tenant` correctly.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from aegis.execute.api.auth import require_bearer
from aegis.execute.schemas.alert import Alert, DeliveryAttempt
from aegis.execute.store.repository import AlertRepository

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _repo(request: Request) -> AlertRepository:
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="alert repository not initialised",
        )
    return repo


def _tenant_id(
    x_aegis_tenant: Annotated[str | None, Header(alias="X-Aegis-Tenant")] = None,
) -> str:
    if not x_aegis_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing X-Aegis-Tenant header",
        )
    return x_aegis_tenant


@router.get("", response_model=list[Alert])
async def list_alerts(
    _principal: Annotated[str, Depends(require_bearer)],
    tenant_id: Annotated[str, Depends(_tenant_id)],
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[Alert]:
    repo = _repo(request)
    return await repo.list_recent_alerts(tenant_id=tenant_id, limit=limit)


@router.get("/{alert_id}", response_model=Alert)
async def get_alert(
    alert_id: str,
    _principal: Annotated[str, Depends(require_bearer)],
    tenant_id: Annotated[str, Depends(_tenant_id)],
    request: Request,
) -> Alert:
    repo = _repo(request)
    alert = await repo.get_alert(tenant_id=tenant_id, alert_id=alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alert not found")
    return alert


@router.get("/{alert_id}/deliveries", response_model=list[DeliveryAttempt])
async def get_deliveries(
    alert_id: str,
    _principal: Annotated[str, Depends(require_bearer)],
    tenant_id: Annotated[str, Depends(_tenant_id)],
    request: Request,
) -> list[DeliveryAttempt]:
    repo = _repo(request)
    return await repo.list_deliveries(tenant_id=tenant_id, alert_id=alert_id)


@router.post("/{alert_id}/ack", response_model=dict[str, Any])
async def ack_alert(
    alert_id: str,
    _principal: Annotated[str, Depends(require_bearer)],
    tenant_id: Annotated[str, Depends(_tenant_id)],
    request: Request,
) -> dict[str, Any]:
    """Mark an outbox row 'acked' (best-effort; idempotent).

    This is a soft state used by the operator UI to dismiss alerts; it
    does not affect retries.
    """
    repo = _repo(request)
    # mark_delivered semantically covers ack here (outbox is now finalised).
    await repo.mark_delivered(tenant_id=tenant_id, alert_id=alert_id)
    return {"alert_id": alert_id, "acked": True}


__all__ = ["router"]
