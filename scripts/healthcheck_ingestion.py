#!/usr/bin/env python
"""Container healthcheck for the autonomous scheduler: healthy == fresh data.

LAYER (c) of the recovery protocol. The previous healthcheck grepped
/proc/*/cmdline for the string "autonomous" — it verified the process
EXISTED, not that it WORKED, and reported "healthy" through all 13 days of
the 2026-07-03..16 ingestion blackout. A process that scrapes nothing is not
healthy.

Healthy  (exit 0): at least one signals row with scraped_at inside the
                   freshness window (AEGIS_INGEST_HEALTH_MAX_AGE_H, default 2h).
Unhealthy (exit 1): no fresh row, or the check itself cannot run — an
                   unverifiable system is not a healthy one.
"""

from __future__ import annotations

import asyncio
import os
import sys

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


async def check() -> int:
    import asyncpg

    dsn = os.environ.get("AEGIS_PG_DSN", "")
    tenant = os.environ.get("AEGIS_DEFAULT_TENANT_ID", _DEFAULT_TENANT)
    max_age_h = float(os.environ.get("AEGIS_INGEST_HEALTH_MAX_AGE_H", "2"))

    conn = await asyncpg.connect(dsn, timeout=8)
    try:
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, false)", tenant
        )
        row = await conn.fetchrow(
            "SELECT 1 FROM signals"
            " WHERE scraped_at > now() - ($1::float8 * interval '1 hour')"
            " LIMIT 1",
            max_age_h,
        )
        if row:
            return 0
        print(f"UNHEALTHY: no signals row scraped within {max_age_h}h", file=sys.stderr)
        return 1
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(check()))
    except Exception as exc:  # unverifiable == unhealthy, never "assume fine"
        print(f"UNHEALTHY: healthcheck failed to run: {exc}", file=sys.stderr)
        sys.exit(1)
