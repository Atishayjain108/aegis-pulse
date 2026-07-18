"""Request-id + structured access log middleware."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_log = structlog.get_logger("aegis.execute.http")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request-id header and emit one structured access log per request."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        start = time.monotonic()
        # Stash on state so route handlers can access it.
        request.state.request_id = rid
        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            _log.error(
                "http.request_failed",
                request_id=rid,
                method=request.method,
                path=request.url.path,
                elapsed_ms=round(elapsed_ms, 2),
                error=str(exc),
            )
            raise
        elapsed_ms = (time.monotonic() - start) * 1000.0
        response.headers["X-Request-Id"] = rid
        _log.info(
            "http.request",
            request_id=rid,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=round(elapsed_ms, 2),
        )
        return response


__all__ = ["RequestIdMiddleware"]
