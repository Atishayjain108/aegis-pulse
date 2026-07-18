"""Optional HTTP API for the compliance engine."""

from __future__ import annotations

from aegis.comply.api.router import build_router, router_available

__all__ = ["build_router", "router_available"]
