"""FastAPI app factory.

`build_app()` is the only public symbol. It assembles everything Phase 4
needs in one place: bus, repo, killswitch, routers, middleware, static
dashboard mount, and (optionally) the Prometheus default registry.

The app does NOT start the drainer. That is handled by a separate
`run_drain_worker()` invocation — typically a sidecar process or another
asyncio task spawned by the CLI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aegis.execute import __version__ as PHASE4_VERSION  # noqa: N812
from aegis.execute.api.middleware import RequestIdMiddleware
from aegis.execute.api.routes import alerts as alerts_routes
from aegis.execute.api.routes import capital as capital_routes
from aegis.execute.api.routes import dashboard as dashboard_routes
from aegis.execute.api.routes import health as health_routes
from aegis.execute.api.routes import killswitch as killswitch_routes
from aegis.execute.api.routes import stream as stream_routes
from aegis.execute.approval import ApprovalBroker
from aegis.execute.bus import EventBus
from aegis.execute.config import ExecuteSettings, get_execute_settings
from aegis.execute.dashboard import STATIC_DIR as _STATIC_DIR
from aegis.execute.engine import ExecutionEngine
from aegis.execute.killswitch.switch import KillSwitch
from aegis.execute.store.repository import AlertRepository

_log = structlog.get_logger(__name__)


def build_app(
    *,
    settings: ExecuteSettings | None = None,
    pool: Any | None = None,
    redis_client: Any | None = None,
    bus: EventBus | None = None,
    killswitch: KillSwitch | None = None,
    repo: AlertRepository | None = None,
    capital_engine: ExecutionEngine | None = None,
    approval_broker: ApprovalBroker | None = None,
) -> FastAPI:
    """Build a fully wired FastAPI app.

    All collaborators are injectable so tests can pass stubs.
    """
    settings = settings or get_execute_settings()
    _bus = bus or EventBus()
    _repo = repo if repo is not None else AlertRepository(pool=pool)
    _killswitch = killswitch or KillSwitch(
        redis_client=redis_client,
        key=settings.killswitch_key,
        fail_closed=redis_client is not None,
    )
    _engine = capital_engine or ExecutionEngine(settings)
    _broker = approval_broker or ApprovalBroker(
        bot_token=settings.telegram_token,
        chat_id=settings.telegram_chat_id,
        timeout_s=settings.approval_timeout_s,
    )

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        _log.info(
            "execute.api.lifespan_startup",
            version=PHASE4_VERSION,
            mode=settings.mode,
        )
        # The lifespan re-asserts state (idempotent). State is also set
        # synchronously below so test transports without a lifespan
        # (e.g. httpx.ASGITransport) work seamlessly.
        app.state.settings = settings
        app.state.bus = _bus
        app.state.repo = _repo
        app.state.killswitch = _killswitch
        app.state.audit_pool = pool
        app.state.capital_engine = _engine
        app.state.approval_broker = _broker
        app.state.plan_store = {}
        try:
            yield
        finally:
            _log.info("execute.api.lifespan_shutdown")

    app = FastAPI(
        title="AEGIS Pulse — Execute",
        description="Phase 4 Execution & Alert System",
        version=PHASE4_VERSION,
        lifespan=_lifespan,
        default_response_class=JSONResponse,
    )

    # Populate state synchronously too: covers transports that don't run
    # the ASGI lifespan (notably httpx.ASGITransport in tests).
    app.state.settings = settings
    app.state.bus = _bus
    app.state.repo = _repo
    app.state.killswitch = _killswitch
    app.state.audit_pool = pool
    app.state.capital_engine = _engine
    app.state.approval_broker = _broker
    app.state.plan_store = {}

    # Middleware
    app.add_middleware(RequestIdMiddleware)

    # Routers
    app.include_router(health_routes.router)
    app.include_router(alerts_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(stream_routes.router)
    app.include_router(killswitch_routes.router)
    app.include_router(capital_routes.router)

    # Root info
    @app.get("/", include_in_schema=False)
    async def _root() -> dict[str, Any]:
        return {
            "service": "aegis-execute",
            "version": PHASE4_VERSION,
            "mode": settings.mode,
            "dashboard": "/dashboard/",
            "docs": "/docs",
        }

    # Static dashboard mount (only if directory present)
    if _STATIC_DIR.is_dir():
        app.mount(
            "/dashboard",
            StaticFiles(directory=str(_STATIC_DIR), html=True),
            name="dashboard-static",
        )

        @app.get("/dashboard", include_in_schema=False)
        async def _dashboard_index() -> HTMLResponse:
            index = _STATIC_DIR / "index.html"
            if index.is_file():
                return HTMLResponse(index.read_text(encoding="utf-8"))
            return HTMLResponse("<h1>AEGIS Pulse — Execute</h1>")

    return app


__all__ = ["build_app"]
