"""AEGIS unified REST API.

Mounts every phase router that was previously built but never served
(geo, compliance, evolve, datalake) behind one FastAPI app so the
intelligence layers are reachable over HTTP, not CLI-only.

See :func:`aegis.api.main.create_app`.
"""

from aegis.api.main import create_app

__all__ = ["create_app"]
