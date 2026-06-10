"""Unified AEGIS REST API (ORPH-1).

Geo, Compliance and Evolve were each shipped with a fully-formed
``APIRouter`` but no served application ever called ``include_router`` on
them — they were reachable only via their CLIs. This module composes a
single FastAPI app that mounts every available phase router behind its own
prefix, so the whole intelligence surface is HTTP-reachable.

Each mount is wrapped in ``try/except`` so a missing optional dependency for
one phase never prevents the others from loading. ``mounted_routers()``
reports exactly which phases came up — used by the health route and tests.

Run as a service:

    uv run aegis api serve            # → http://localhost:8400
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("aegis.api")

# (prefix-tag, import path of the module exposing `router`)
_ROUTER_MODULES: tuple[tuple[str, str], ...] = (
    ("geo", "aegis.geo.api"),
    ("compliance", "aegis.compliance.api"),
    ("evolve", "aegis.evolve.api"),
)

_MOUNTED: list[str] = []


def mounted_routers() -> list[str]:
    """Return the list of phase prefixes that mounted successfully."""
    return list(_MOUNTED)


def create_app() -> Any:
    """Build the unified FastAPI application.

    Imports FastAPI lazily so importing this module never hard-requires the
    web stack (keeps unit-test import cost and optional-dep surface low).
    """
    from fastapi import FastAPI

    app = FastAPI(
        title="AEGIS Unified API",
        version="1.0.0",
        description="Geo / Compliance / Evolve / Data Lake intelligence over HTTP.",
    )
    _MOUNTED.clear()

    for tag, module_path in _ROUTER_MODULES:
        try:
            module = __import__(module_path, fromlist=["router"])
            app.include_router(module.router)
            _MOUNTED.append(tag)
        except Exception as exc:  # one phase failing must not sink the app
            _log.warning("api.router_mount_failed", phase=tag, error=str(exc)[:300])

    # Data Lake exposes a router *factory* rather than a module-level router.
    try:
        from aegis.datalake.api.router import build_router as _build_datalake_router
        from aegis.datalake.settings import DataLakeSettings

        app.include_router(_build_datalake_router(settings=DataLakeSettings()))
        _MOUNTED.append("datalake")
    except Exception as exc:
        _log.warning("api.router_mount_failed", phase="datalake", error=str(exc)[:300])

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:  # pragma: no cover - trivial
        return {"status": "ok", "mounted": mounted_routers()}

    return app


# Module-level app for `uvicorn aegis.api.main:app`.
app = create_app()
