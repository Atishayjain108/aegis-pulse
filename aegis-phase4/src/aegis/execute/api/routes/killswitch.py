"""Killswitch API.

GET  /killswitch        — current state (TRIPPED / ARMED)
POST /killswitch/trip   — trip the switch (requires reason)
POST /killswitch/arm    — re-arm the switch (requires reason)

Both POST endpoints write an audit row to `killswitch_audit`. The audit
table is NOT RLS-gated by design (ops need cross-tenant visibility).
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from aegis.execute.api.auth import require_bearer
from aegis.execute.killswitch.switch import KillSwitch

_log = structlog.get_logger("aegis.execute.api.killswitch")

router = APIRouter(prefix="/killswitch", tags=["killswitch"])


class ToggleRequest(BaseModel):
    """Body for trip/arm. Reason is required for the audit log."""

    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


def _switch(request: Request) -> KillSwitch:
    ks = getattr(request.app.state, "killswitch", None)
    if ks is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="kill switch not initialised",
        )
    return ks


@router.get("", response_model=dict[str, Any])
async def get_state(
    _principal: Annotated[str, Depends(require_bearer)],
    request: Request,
) -> dict[str, Any]:
    ks = _switch(request)
    state = await ks.state()
    return {"state": state}


@router.post("/trip", response_model=dict[str, Any])
async def trip(
    body: ToggleRequest,
    principal: Annotated[str, Depends(require_bearer)],
    request: Request,
) -> dict[str, Any]:
    ks = _switch(request)
    await ks.trip(reason=body.reason)
    # Best-effort audit write
    pool = getattr(request.app.state, "audit_pool", None)
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO killswitch_audit (actor, action, reason) VALUES ($1, 'trip', $2)",
                    principal,
                    body.reason,
                )
        except Exception as exc:
            _log.warning("execute.killswitch.audit_write_failed", action="trip", error=str(exc))
    return {"state": "TRIPPED", "reason": body.reason}


@router.post("/arm", response_model=dict[str, Any])
async def arm(
    body: ToggleRequest,
    principal: Annotated[str, Depends(require_bearer)],
    request: Request,
) -> dict[str, Any]:
    ks = _switch(request)
    await ks.arm(reason=body.reason)
    pool = getattr(request.app.state, "audit_pool", None)
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO killswitch_audit (actor, action, reason) VALUES ($1, 'arm', $2)",
                    principal,
                    body.reason,
                )
        except Exception as exc:
            _log.warning("execute.killswitch.audit_write_failed", action="arm", error=str(exc))
    return {"state": "ARMED", "reason": body.reason}


__all__ = ["router"]
