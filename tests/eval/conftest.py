"""
Eval-suite fixtures for tests/eval/.

Provides:
  - real_db_stats   : session-scoped dict with actual signal aggregates from
                       the dev TimescaleDB (localhost:5433). Falls back to the
                       constants below if the DB is unreachable so CI never
                       fails due to infrastructure absence.
  - trend_factory   : factory that builds TrendCandidate instances using the
                       real stats as defaults, with per-test overrides.
  - reset_singletons: autouse fixture that clears LLM-router + graph cache
                       before every test (mirrors tests/unit/agents/conftest.py).

Architecture: eval fixtures are intentionally kept separate from unit fixtures
so the eval suite can be run independently (pytest tests/eval/) without pulling
in the full unit test infrastructure.

Invariants enforced indirectly: every TrendCandidate built via trend_factory
uses real observed velocity ranges, making sentinel/compliance invariant failures
reflect genuine routing bugs rather than fixture artefacts.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fallback constants derived from Step-0d DB query (2026-06-04)
# Used when the live DB is unreachable (CI, offline dev).
# ---------------------------------------------------------------------------
_FALLBACK_STATS: dict[str, Any] = {
    "avg_conf": 0.870,
    "total_24h": 198,
    "vel_1h": 106.0,
    "vel_6h": 106.0,
    "platforms": 5,
}

_DEV_DSN = os.getenv(
    "AEGIS_EVAL_PG_DSN",
    "postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis",
)
_TENANT_ID = "00000000-0000-0000-0000-000000000001"


async def _fetch_real_stats() -> dict[str, Any]:
    """Query the dev DB for signal aggregates over the last 24 hours."""
    try:
        import asyncpg  # type: ignore[import-untyped]
    except ImportError:
        return dict(_FALLBACK_STATS)

    try:
        conn = await asyncio.wait_for(asyncpg.connect(_DEV_DSN), timeout=5.0)
    except Exception:  # network unreachable, wrong creds, timeout
        return dict(_FALLBACK_STATS)

    try:
        await conn.execute(
            f"SET LOCAL app.current_tenant = '{_TENANT_ID}'"
        )
        row = await conn.fetchrow(
            """
            SELECT
                ROUND(AVG(source_confidence)::numeric, 3)           AS avg_conf,
                COUNT(*)                                             AS total_24h,
                SUM(CASE WHEN created_at > NOW()-INTERVAL '1h'
                         THEN 1 ELSE 0 END)::float                  AS vel_1h,
                SUM(CASE WHEN created_at > NOW()-INTERVAL '6h'
                         THEN 1 ELSE 0 END)::float                  AS vel_6h,
                COUNT(DISTINCT platform)                            AS platforms
            FROM signals
            WHERE created_at > NOW() - INTERVAL '24 hours'
            """
        )
    except Exception:
        return dict(_FALLBACK_STATS)
    finally:
        await conn.close()

    if row is None or row["total_24h"] == 0:
        return dict(_FALLBACK_STATS)

    return {
        "avg_conf": float(row["avg_conf"] or _FALLBACK_STATS["avg_conf"]),
        "total_24h": int(row["total_24h"]),
        "vel_1h": float(row["vel_1h"] or _FALLBACK_STATS["vel_1h"]),
        "vel_6h": float(row["vel_6h"] or _FALLBACK_STATS["vel_6h"]),
        "platforms": int(row["platforms"]),
    }


@pytest.fixture(scope="session")
def real_db_stats() -> dict[str, Any]:
    """Session-scoped: one DB query for the entire eval run."""
    return asyncio.run(_fetch_real_stats())


@pytest.fixture(autouse=True)
def _reset_singletons() -> pytest.FixtureResult[None]:
    """Clear LLM-router and compiled-graph cache before/after every eval test."""
    from aegis.agents import runner as runner_module
    from aegis.agents.llm import router as router_module

    asyncio.run(router_module.reset_default_router())
    asyncio.run(runner_module.reset_graph_cache())
    yield
    asyncio.run(router_module.reset_default_router())
    asyncio.run(runner_module.reset_graph_cache())


@pytest.fixture
def trend_factory(real_db_stats: dict[str, Any]):
    """
    Factory that builds TrendCandidate with real DB stat defaults.

    Usage:
        tc = trend_factory()                     # uses real DB values
        tc = trend_factory(velocity_1h=999.0)    # override specific field
        tc = trend_factory(coordination_risk=0.95, _id="edge-001")
    """
    from aegis.agents.schemas import TrendCandidate

    vel_1h = real_db_stats["vel_1h"]
    vel_6h = real_db_stats["vel_6h"]
    avg_conf = real_db_stats["avg_conf"]
    total_24h = real_db_stats["total_24h"]

    call_counter = {"n": 0}

    def _build(_id: str | None = None, **overrides: Any) -> TrendCandidate:
        call_counter["n"] += 1
        idx = call_counter["n"]
        trend_id = _id or f"eval-trend-{idx:04d}"
        defaults: dict[str, Any] = {
            "trend_id": trend_id,
            "title": f"Real-data eval trend {idx}",
            "signal_count": total_24h,
            "unique_authors": max(1, total_24h // 5),
            "platforms": ["reddit", "hacker_news", "google_news"],
            "velocity_1h": vel_1h,
            "velocity_6h": vel_6h,
            "velocity_24h": vel_6h * 3.5,
            "sentiment": avg_conf - 0.5,       # map [0,1] conf → [-0.5, 0.5]
            "commercial_intent": 0.6,
            "novelty": 0.5,
            "coordination_risk": 0.08,
        }
        defaults.update(overrides)
        return TrendCandidate(**defaults)

    return _build
