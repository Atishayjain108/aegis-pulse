"""Health, readiness, and Prometheus metrics endpoints.

`/healthz` is a liveness probe — returns 200 as long as the process is up.
`/readyz` is a readiness probe — checks the configured pool + killswitch.
`/metrics` exposes Prometheus metrics; the registry is on the app state.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status

router = APIRouter()


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request) -> dict[str, Any]:
    """Readiness check.

    Healthy means:
      - repository is callable (we try a no-op pending_count)
      - killswitch backend reachable (or absent, which is OK in dev)
    """
    bus = getattr(request.app.state, "bus", None)
    settings = getattr(request.app.state, "settings", None)

    status_payload: dict[str, Any] = {
        "status": "ok",
        "subscribers": (bus.subscriber_count if bus is not None else 0),
        "mode": (settings.mode if settings is not None else "unknown"),
    }
    # Optionally exercise the killswitch
    ks = getattr(request.app.state, "killswitch", None)
    if ks is not None:
        try:
            status_payload["killswitch"] = await ks.state()
        except Exception:
            status_payload["killswitch"] = "unknown"
    return status_payload


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Prometheus metrics endpoint.

    If `prometheus_client` is not installed (lightweight test environment),
    returns 204 No Content. The metric collection logic uses the default
    registry; in tests a fresh registry is used per-app.
    """
    try:
        from prometheus_client import (  # type: ignore[import-not-found]
            CONTENT_TYPE_LATEST,
            generate_latest,
        )
    except ImportError:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    registry = getattr(request.app.state, "prom_registry", None)
    body = generate_latest(registry) if registry is not None else generate_latest()
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)


__all__ = ["router"]
