"""FastAPI route modules for Phase 4.

Each submodule exports a single ``router: APIRouter`` that the
``aegis.execute.api.app.build_app`` factory mounts. Splitting routers
by domain keeps each file small and lets tests target endpoints
individually without booting the full app.

Modules:

* ``health``     — ``/healthz``, ``/readyz``, ``/metrics``
* ``alerts``     — ``/alerts``, ``/alerts/{id}``, ``/alerts/{id}/ack``,
                   ``/alerts/{id}/deliveries``
* ``dashboard``  — ``/snapshot``
* ``stream``     — ``/stream`` (Server-Sent Events)
* ``killswitch`` — ``/killswitch``, ``/killswitch/trip``,
                   ``/killswitch/arm``
"""

from __future__ import annotations

from aegis.execute.api.routes import (
    alerts,
    dashboard,
    health,
    killswitch,
    stream,
)

__all__ = ["alerts", "dashboard", "health", "killswitch", "stream"]
