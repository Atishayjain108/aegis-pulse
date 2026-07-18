"""Bearer-token auth for the API.

Auth is OPTIONAL. If `AEGIS_EXECUTE_API_BEARER_TOKEN` is empty, every
request is accepted — appropriate for laptop / homelab use. Production
deployments should set the token (and run behind TLS).

The dependency is a FastAPI dependency that returns the authenticated
principal name (or "anonymous") for logging. It never returns None — it
either succeeds or raises 401.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from aegis.execute.config import ExecuteSettings, get_execute_settings


def _settings() -> ExecuteSettings:
    return get_execute_settings()


async def require_bearer(
    settings: Annotated[ExecuteSettings, Depends(_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Return the principal name. Raises 401 on failure."""
    expected = settings.api_bearer_token
    if not expected:
        # No token configured → permissive mode.
        return "anonymous"
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="expected Bearer scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )
    presented = authorization[len(prefix):].strip()
    # Constant-time compare to prevent timing oracles.
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return "bearer"


__all__ = ["require_bearer"]
