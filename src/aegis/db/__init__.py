"""Database layer — asyncpg pool + query helpers."""

from __future__ import annotations

from aegis.db.pool import (
    DEFAULT_TENANT_ID,
    HealthResult,
    PgConfig,
    PgPool,
    get_shared_pool,
    set_shared_pool,
)

__all__ = [
    "DEFAULT_TENANT_ID",
    "HealthResult",
    "PgConfig",
    "PgPool",
    "get_shared_pool",
    "set_shared_pool",
]
