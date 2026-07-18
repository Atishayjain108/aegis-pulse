"""
Tool: signal_query.

Wraps the Phase-1 `aegis.db.signals` module to give agents a clean
async interface for fetching signal slices. We delegate to the
existing `fetch_recent_signals` to inherit its filtering + pagination
behavior; this tool just adapts the shape and error handling.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

from typing import Any

import structlog

from .base import ToolResult, tool_call

_log = structlog.get_logger("aegis.agents.tools.signal_query")


@tool_call("signal_query")
async def fetch_recent(
    *,
    tenant_id: str,
    limit: int = 50,
    platform: str | None = None,
    since_minutes: int | None = None,
) -> ToolResult:
    """Return up to `limit` recent signals as plain dicts."""
    try:
        # Import lazily so the tools package is importable even when
        # the DB module is being mocked or the DB is offline.
        from uuid import UUID

        from aegis.db import signals as db_signals
        from aegis.db.pool import get_shared_pool
    except ImportError:
        return ToolResult.failure(
            code="AEGIS-TOOL-SIGNAL-DEP",
            message="aegis.db not importable",
        )

    try:
        pool = get_shared_pool()
    except RuntimeError:
        return ToolResult.failure(
            code="AEGIS-TOOL-SIGNAL-NO-POOL",
            message="no shared PgPool configured; call set_shared_pool() at startup",
        )

    try:
        tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        rows = await db_signals.fetch_recent_signals(
            pool,
            tenant_id=tenant_uuid,
            limit=int(limit),
            platform=platform,
        )
    except Exception as exc:
        return ToolResult.failure(
            code="AEGIS-TOOL-SIGNAL-DB",
            message=f"db query failed: {exc}",
        )

    # Coerce to a JSON-safe list of dicts whether rows are records,
    # dicts, or pydantic models.
    out: list[dict[str, Any]] = []
    for r in rows or []:
        if hasattr(r, "model_dump"):
            out.append(r.model_dump(mode="json"))
        elif isinstance(r, dict):
            out.append(r)
        else:
            try:
                out.append(dict(r))
            except Exception:
                out.append({"_raw": str(r)})
    return ToolResult.success(out, count=len(out))
