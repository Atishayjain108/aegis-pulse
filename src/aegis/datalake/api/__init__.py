"""FastAPI surface for the Phase 10 Data Lake.

Mounted by the Phase 4 ``aegis-execute-api`` (or the Phase 1 Dashboard) under
the ``/datalake`` prefix. Provides read-only query + introspection endpoints.

Importing this module does *not* import FastAPI at file scope — that is done
lazily inside :func:`get_router` to keep Phase 10 usable in environments
without a web framework (CLI / Prefect workers / notebooks).
"""

from .router import build_router

__all__ = ["build_router"]
