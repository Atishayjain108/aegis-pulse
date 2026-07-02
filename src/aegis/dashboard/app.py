"""
AEGIS Pulse — Command Center Dashboard API
==========================================
FastAPI backend served on :8300.
Aggregates data from Postgres, Redis, Docker, Phase 3 (:8100), Phase 4 (:8200)
and exposes them as clean JSON APIs plus an SSE live feed for the SPA.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import uuid as _uuid_mod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager as _asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from redis.exceptions import TimeoutError as _RedisTimeoutError

from aegis.config import settings

_sse_log = structlog.get_logger("aegis.dashboard.sse")

# ---------------------------------------------------------------------------
# DASH-4: pool-acquire timeout counter. Uses the collision-safe observability
# factory (returns a no-op stub when prometheus_client is absent), so this
# import can never fail or double-register.
# ---------------------------------------------------------------------------
try:
    from aegis.observability.metrics import _counter as _obs_counter

    _pool_acquire_timeout_total = _obs_counter(
        "aegis_obs_dashboard_pool_acquire_timeout_total",
        "Dashboard PG pool acquire timeouts",
    )
except Exception:  # observability package unavailable — degrade to no-op

    class _NoOpCounter:
        def inc(self, *_a: Any, **_k: Any) -> None:
            return None

    _pool_acquire_timeout_total = _NoOpCounter()

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
_STATIC = Path(__file__).parent / "static"
_REPO_ROOT = Path(__file__).resolve().parents[4]   # …/aegis-pulse/


@_asynccontextmanager
async def _pg_conn(dsn: str, timeout: float = 3.0) -> AsyncIterator[Any]:
    """Guaranteed-close asyncpg connection context manager.

    Prevents connection leaks when queries throw between connect and close.
    """
    conn = await asyncpg.connect(dsn, timeout=timeout)
    try:
        yield conn
    finally:
        await conn.close()


# Module-level Redis pool: created once on first use, reused for all REST
# requests. Eliminates the O(requests/sec) connection-creation overhead that
# was present when each handler called aioredis.from_url() independently.
_redis_pool: aioredis.Redis | None = None  # type: ignore[type-arg]
_pg_pool: asyncpg.Pool | None = None  # type: ignore[type-arg]

# Research job store: job_id → {status, events, _started_dt, result?}
# Capped at MAX_RESEARCH_JOBS entries; evicted oldest-first by _cleanup_research_jobs()
# regardless of status (so stuck `running` jobs cannot pin the store and leak tasks).
_research_jobs: dict[str, dict[str, Any]] = {}
# Hard wall-clock ceiling for a single research job. The agent runner already has its
# own 120s timeout; this is the outer guard covering scrape + dedup + pipeline so a
# wedged job is always reaped instead of holding a task + DB/Redis connections forever.
RESEARCH_JOB_TIMEOUT_S = 300.0
MAX_RESEARCH_JOBS = 20
_research_pool: Any = None  # aegis.db.pool.PgPool (different from asyncpg pool)

# Watchlist store: list of {topic, added_at, last_verdict, last_score, last_run_at}
# Persisted to Redis; in-memory fallback when Redis is unavailable.
_watchlist_mem: list[dict[str, Any]] = []
_WATCHLIST_KEY = "aegis:dashboard:watchlist"
_VERDICT_TO_P4 = {"proceed": "ENTER", "hold": "HOLD", "block": "BLOCK", "escalate": "HOLD"}


async def _load_watchlist() -> list[dict[str, Any]]:
    try:
        raw = await _get_redis().get(_WATCHLIST_KEY)
        if raw:
            return json.loads(raw)  # type: ignore[arg-type]
    except Exception:
        pass
    return list(_watchlist_mem)


async def _save_watchlist(wl: list[dict[str, Any]]) -> None:
    global _watchlist_mem  # noqa: PLW0603
    _watchlist_mem = list(wl)
    with contextlib.suppress(Exception):
        await _get_redis().set(_WATCHLIST_KEY, json.dumps(wl))


def _get_redis() -> aioredis.Redis:  # type: ignore[type-arg]
    """Return (or lazily create) the process-singleton Redis connection pool."""
    global _redis_pool  # noqa: PLW0603
    if _redis_pool is None:
        cfg = settings()
        _redis_pool = aioredis.from_url(
            cfg.redis_url_str,
            decode_responses=True,
            # socket_timeout must exceed the SSE XREAD block window (1500 ms)
            # with headroom, or the blocking read races the socket deadline
            # and surfaces a spurious "Timeout reading from ..." every tick.
            socket_timeout=5.0,
            max_connections=20,
        )
    return _redis_pool


@_asynccontextmanager
async def _acquire_pg(dsn: str, timeout: float = 3.0) -> AsyncIterator[Any]:
    """Acquire a connection from the shared pool, falling back to a raw connect.

    Uses the process-singleton asyncpg.Pool when available (warm startup).
    Falls back to asyncpg.connect() during startup or if pool creation failed.
    """
    if _pg_pool is not None:
        # DASH-4: a saturated pool must surface as 504 (gateway timeout), not
        # hang the request or bubble a bare TimeoutError into a 500.
        try:
            async with _pg_pool.acquire(timeout=timeout) as conn:
                await _set_tenant(conn)
                yield conn
        except TimeoutError as exc:
            _pool_acquire_timeout_total.inc()
            _app_log.warning(
                "dashboard.pg_pool_acquire_timeout",
                timeout_s=timeout,
                pool_max=getattr(_pg_pool, "_maxsize", None),
            )
            raise HTTPException(
                status_code=504, detail="Database connection pool acquire timed out"
            ) from exc
    else:
        conn = await asyncpg.connect(dsn, timeout=timeout)
        try:
            await _set_tenant(conn)
            yield conn
        finally:
            await conn.close()


async def _set_tenant(conn: Any) -> None:
    """Always set the RLS tenant on a freshly-acquired connection.

    Every tenant-scoped table uses Row-Level Security; without
    ``SET app.current_tenant`` a query silently returns 0 rows, which the
    dashboard renders as "empty" rather than an error. Setting it on every
    acquire (pooled connections are reused, so the GUC can be stale) closes
    that trap. ``set_config(..., true)`` scopes it to the transaction/session.
    """
    try:
        await conn.execute(
            "SELECT set_config('app.current_tenant', $1, false)",
            _cfg.default_tenant_id,
        )
    except Exception as exc:  # never let tenant-set failure break a read path
        _app_log.debug("dashboard.set_tenant_failed", error=str(exc)[:200])


async def _docker_ps() -> list[dict[str, Any]]:
    """Query the Docker Engine API via Unix socket — no docker CLI needed.

    Returns an empty list (not an error sentinel) when Docker is unreachable so
    the frontend pill shows "Docker 0/0" instead of the misleading "Docker 0/1"
    that appeared when the error dict was counted as a container entry.
    """
    try:
        # INFRA-2: prefer a TCP Docker endpoint when DOCKER_HOST is set —
        # containers without the socket mount (the hardened default) can
        # still show status via a remote/proxied Docker API. Fall back to
        # the local Unix socket (host-mode dashboard), then degrade to [].
        docker_host = os.environ.get("DOCKER_HOST", "")
        if docker_host.startswith("tcp://"):
            transport = httpx.AsyncHTTPTransport()
            base_url = "http://" + docker_host.removeprefix("tcp://").rstrip("/")
        else:
            transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
            base_url = "http://localhost"
        async with httpx.AsyncClient(transport=transport, base_url=base_url, timeout=5.0) as client:
            resp = await client.get("/containers/json", params={"all": "true"})
            resp.raise_for_status()
            raw: list[dict[str, Any]] = resp.json()
            return [
                {
                    "Name": c.get("Names", ["?"])[0].lstrip("/"),
                    "Image": c.get("Image", "?"),
                    "State": c.get("State", "unknown"),
                    "Status": c.get("Status", "?"),
                }
                for c in raw
            ]
    except Exception as exc:
        _app_log.debug("docker_ps.unavailable", error=str(exc)[:200])
        return []

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
_cfg = settings()
_app_log = structlog.get_logger("aegis.dashboard.app")


def _require_ops_token_in_prod() -> None:
    """ENV-2: refuse to start when the ops console would be unauthenticated RCE.

    The ops console (POST /api/ops/run) executes shell commands. In prod and
    staging an empty AEGIS_DASHBOARD_OPS_TOKEN means anyone who can reach
    :8300 can run commands — so startup hard-fails. Dev/test keep the
    existing WARNING (host-mode dev workflow stays friction-free) and the
    request-time 503 in _verify_ops_token remains as defense-in-depth.
    """
    if _cfg.env in ("prod", "staging") and not _cfg.dashboard_ops_token:
        raise RuntimeError(
            "AEGIS_DASHBOARD_OPS_TOKEN must be set when AEGIS_ENV is prod or "
            "staging. The ops console executes shell commands — an empty "
            "token is unauthenticated remote code execution."
        )


_llm_probe_task: asyncio.Task[None] | None = None


async def _probe_llm_backends() -> None:
    """ENV-3: non-blocking startup probe of the LLM fallback chain.

    Warns loudly when every backend is unreachable — the system still works
    (heuristic-only doctrine) but verdict reasoning will be template text,
    and the operator should know that *at startup*, not after a day of
    wondering why reasoning looks canned. Never blocks or fails startup.
    """
    try:
        from aegis.agents.llm import get_gateway

        if get_gateway is None:  # Phase 11 not installed — shim exports None
            _app_log.warning("startup.llm_probe.no_gateway", heuristic_only_mode=True)
            return
        gw = await get_gateway()
        if gw is None:
            _app_log.warning("startup.llm_probe.no_gateway", heuristic_only_mode=True)
            return
        health = await asyncio.wait_for(gw.health(), timeout=5.0)
        reachable = [p for p, ok in health.items() if ok]
        if not reachable:
            _app_log.warning(
                "startup.llm_probe.all_unreachable",
                heuristic_only_mode=True,
                checked=list(health.keys()),
                consequence="every agent will use heuristic fallback",
            )
        else:
            _app_log.info("startup.llm_probe.ok", reachable=reachable)
    except Exception as exc:
        _app_log.warning(
            "startup.llm_probe.failed", error=str(exc)[:200], heuristic_only_mode=True
        )


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Create shared connection pools on startup; close them on shutdown."""
    global _pg_pool, _redis_pool, _research_pool, _llm_probe_task  # noqa: PLW0603
    _require_ops_token_in_prod()  # ENV-2: fail fast, before any pool exists
    # audit P8-2: actually initialise error tracking at startup. init_sentry is a
    # no-op when sentry-sdk is absent or AEGIS_SENTRY_DSN is empty, so this is
    # safe in dev and turns real error tracking on the moment a DSN is set.
    try:
        from aegis.observability import init_sentry

        init_sentry()
    except Exception as exc:  # never let observability wiring block startup
        _app_log.debug("dashboard.sentry_init_skipped", error=str(exc))
    try:
        # DASH-4: a bigger default ceiling (concurrent tabs + SSE catch-up +
        # /api/stats aggregation + research all share this pool) and a server-side
        # command_timeout so a slow query can't pin a connection forever. Both are
        # env-overridable. The acquire-side timeout lives in _acquire_pg (3s).
        _max = int(os.getenv("AEGIS_DASHBOARD_PG_POOL_MAX", "20"))
        _cmd_to = float(os.getenv("AEGIS_DASHBOARD_PG_COMMAND_TIMEOUT", "10"))
        _pg_pool = await asyncpg.create_pool(
            _cfg.pg_dsn_str, min_size=2, max_size=_max, command_timeout=_cmd_to
        )
    except Exception as exc:
        _app_log.warning("dashboard.pg_pool_unavailable", error=str(exc))
    try:
        from aegis.db.pool import PgPool as _PgPool
        _research_pool = _PgPool(dsn=_cfg.pg_dsn_str)
        await _research_pool.start()
    except Exception as exc:
        _app_log.warning("dashboard.research_pool_unavailable", error=str(exc))
    # ENV-3: fire-and-forget LLM reachability probe. Reference kept so the
    # task isn't garbage-collected mid-flight; cancelled on shutdown.
    _llm_probe_task = asyncio.create_task(_probe_llm_backends())
    try:
        yield
    finally:
        if _llm_probe_task is not None and not _llm_probe_task.done():
            _llm_probe_task.cancel()
        if _pg_pool is not None:
            await _pg_pool.close()
            _pg_pool = None
        if _research_pool is not None:
            await _research_pool.close()
            _research_pool = None
        if _redis_pool is not None:
            await _redis_pool.aclose()
            _redis_pool = None


app = FastAPI(title="AEGIS Command Center", version="1.0.0", docs_url="/api/docs", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cfg.dashboard_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Ops-Token"],
)
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.middleware("http")
async def _no_cache_api(request: Request, call_next: Any) -> Any:
    """Prevent browser/proxy caching on all /api/ endpoints.

    Without this, browsers may serve a previous response from disk cache
    between polling ticks, causing the dashboard to show stale data even
    after the backend has updated.  The SSE stream is exempt (it streams
    indefinitely and already sets Cache-Control: no-cache).
    """
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

if _cfg.dashboard_ops_token is None and not _cfg.is_prod:
    _app_log.warning(
        "dashboard.ops_token_unset",
        note="POST /api/ops/run is unauthenticated. Set AEGIS_DASHBOARD_OPS_TOKEN to secure it.",
    )


# ---------------------------------------------------------------------------
# Root → SPA
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    p = _STATIC / "index.html"
    if not p.exists():
        return HTMLResponse("<h1>Static files missing — run build first.</h1>", status_code=500)
    html = p.read_text()
    # Inject the ops token into the same-origin SPA so the operations console
    # can authenticate against POST /api/ops/run (which requires X-Ops-Token
    # when AEGIS_DASHBOARD_OPS_TOKEN is set). The token never leaves this
    # localhost origin; CORS is already locked to localhost.
    tok = settings().dashboard_ops_token
    tok_val = tok.get_secret_value() if tok is not None else ""
    inject = f'<script>window.AEGIS_OPS_TOKEN={json.dumps(tok_val)};</script>'
    html = html.replace("</head>", f"{inject}</head>", 1) if "</head>" in html else inject + html
    return HTMLResponse(html)


@app.get("/healthz")
@app.get("/api/health")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "aegis-dashboard"}


# ---------------------------------------------------------------------------
# /api/system — full cross-phase health snapshot
# ---------------------------------------------------------------------------
@app.get("/api/system")
async def system_health() -> dict[str, Any]:
    cfg = settings()
    out: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "version": cfg.service_version,
        "env": cfg.env,
    }

    # ── Postgres ──────────────────────────────────────────────────────────
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM signals")
            by_plat = await conn.fetch(
                "SELECT platform, COUNT(*) AS n FROM signals GROUP BY platform ORDER BY n DESC LIMIT 10"
            )
            last_sig = await conn.fetchrow(
                "SELECT MAX(scraped_at) AS ts FROM signals"
            )
            try:
                pred_count = await conn.fetchval("SELECT COUNT(*) FROM predictions")
            except Exception:
                pred_count = 0
        out["postgres"] = {
            "status": "healthy",
            "signal_count": total,
            "prediction_count": pred_count,
            "by_platform": {r["platform"]: r["n"] for r in by_plat},
            "last_scraped": last_sig["ts"].isoformat() if last_sig and last_sig["ts"] else None,
        }
    except Exception as exc:
        out["postgres"] = {"status": "error", "error": str(exc)[:300]}

    # ── Redis ─────────────────────────────────────────────────────────────
    try:
        r = _get_redis()
        await r.ping()
        info = await r.info()
        stream_len = 0
        with contextlib.suppress(Exception):
            stream_len = await r.xlen("aegis:phase2:graph_results")
        out["redis"] = {
            "status": "healthy",
            "used_memory_human": info.get("used_memory_human", "?"),
            "connected_clients": info.get("connected_clients", 0),
            "keyspace_hits": info.get("keyspace_hits", 0),
            "keyspace_misses": info.get("keyspace_misses", 0),
            "graph_results_stream_len": stream_len,
        }
    except Exception as exc:
        out["redis"] = {"status": "error", "error": str(exc)[:300]}

    # ── Phase 3 — predict ─────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{cfg.predict_api_url}/healthz")
            out["predict"] = {"status": "healthy", "http_status": resp.status_code, "data": resp.json()}
    except Exception as exc:
        out["predict"] = {"status": "error", "error": str(exc)[:200]}

    # ── Phase 4 — execute ─────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{cfg.execute_api_url}/healthz")
            out["execute"] = {"status": "healthy", "http_status": resp.status_code, "data": resp.json()}
    except Exception as exc:
        out["execute"] = {"status": "error", "error": str(exc)[:200]}

    # ── Docker containers ─────────────────────────────────────────────────
    try:
        containers = await _docker_ps()
        out["docker"] = {"status": "ok", "containers": containers}
    except Exception as exc:
        out["docker"] = {"status": "error", "error": str(exc)[:200], "containers": []}

    return out


# ---------------------------------------------------------------------------
# /api/signals/stats
# ---------------------------------------------------------------------------
@app.get("/api/signals/stats")
async def signal_stats() -> dict[str, Any]:
    cfg = settings()
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            now = datetime.now(UTC)

            total = await conn.fetchval("SELECT COUNT(*) FROM signals")
            last24h = await conn.fetchval(
                "SELECT COUNT(*) FROM signals WHERE scraped_at >= $1", now - timedelta(hours=24)
            )
            last1h = await conn.fetchval(
                "SELECT COUNT(*) FROM signals WHERE scraped_at >= $1", now - timedelta(hours=1)
            )
            by_platform = await conn.fetch(
                "SELECT platform, COUNT(*) AS n FROM signals GROUP BY platform ORDER BY n DESC"
            )
            hourly = await conn.fetch("""
                SELECT DATE_TRUNC('hour', scraped_at) AS hr, COUNT(*) AS n
                FROM signals
                WHERE scraped_at >= $1
                GROUP BY hr ORDER BY hr
            """, now - timedelta(hours=24))

            # avg quality metrics by platform (mapped from real schema columns)
            sentiment_avg = await conn.fetch("""
                SELECT platform,
                       AVG(source_confidence) AS avg_sentiment,
                       AVG(CASE WHEN price_amount IS NOT NULL THEN 0.9::real
                                WHEN intent = 'purchase' THEN 0.8::real
                                WHEN intent = 'save' THEN 0.6::real
                                ELSE 0.1::real
                           END) AS avg_commercial_intent,
                       AVG(completeness) AS avg_novelty
                FROM signals
                WHERE scraped_at >= $1
                GROUP BY platform
            """, now - timedelta(hours=24))

        return {
            "total": total,
            "last_24h": last24h,
            "last_1h": last1h,
            "by_platform": {r["platform"]: r["n"] for r in by_platform},
            "hourly_24h": [
                {"hour": r["hr"].isoformat(), "count": r["n"]}
                for r in hourly
            ],
            "platform_metrics": {
                r["platform"]: {
                    "sentiment": round(float(r["avg_sentiment"] or 0), 3),
                    "commercial_intent": round(float(r["avg_commercial_intent"] or 0), 3),
                    "novelty": round(float(r["avg_novelty"] or 0), 3),
                }
                for r in sentiment_avg
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# /api/signals/recent
# ---------------------------------------------------------------------------
@app.get("/api/signals/recent")
async def signals_recent(
    limit: int = 60,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    cfg = settings()
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            _signal_cols = """
                    signal_id, platform, title, url, scraped_at,
                    source_confidence AS sentiment,
                    CASE WHEN price_amount IS NOT NULL THEN 0.9::real
                         WHEN intent = 'purchase' THEN 0.8::real
                         WHEN intent = 'save' THEN 0.6::real
                         ELSE 0.1::real
                    END AS commercial_intent,
                    completeness AS novelty,
                    views, likes, comments, shares
            """
            if platform and platform != "all":
                rows = await conn.fetch(
                    f"SELECT {_signal_cols} FROM signals WHERE platform = $1"  # noqa: S608
                    " ORDER BY scraped_at DESC LIMIT $2",
                    platform, limit,
                )
            else:
                rows = await conn.fetch(
                    f"SELECT {_signal_cols} FROM signals ORDER BY scraped_at DESC LIMIT $1",  # noqa: S608
                    limit,
                )
        return [
            {
                "signal_id": str(r["signal_id"]),
                "platform": r["platform"],
                "title": (r["title"] or "")[:120],
                "url": str(r["url"]) if r["url"] else None,
                "scraped_at": r["scraped_at"].isoformat() if r["scraped_at"] else None,
                "sentiment": round(float(r["sentiment"] or 0), 3),
                "commercial_intent": round(float(r["commercial_intent"] or 0), 3),
                "novelty": round(float(r["novelty"] or 0), 3),
                "engagement": (r["likes"] or 0) + (r["comments"] or 0) + (r["shares"] or 0),
            }
            for r in rows
        ]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# /api/products/recent  — product intelligence cards (image + price + source)
# Reads image_url/price out of the platform_specific JSONB captured by the
# e-commerce adapters (flipkart, meesho, nykaa, myntra, amazon_in, snapdeal).
# ---------------------------------------------------------------------------
@app.get("/api/products/recent")
async def products_recent(
    limit: int = 48,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    cfg = settings()
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            where = "WHERE platform_specific->>'image_url' IS NOT NULL"
            params: list[Any] = []
            if platform and platform != "all":
                params.append(platform)
                where += f" AND platform = ${len(params)}"
            params.append(limit)
            rows = await conn.fetch(
                f"""
                SELECT signal_id, platform, title, url, scraped_at, price_amount,
                       platform_specific
                FROM signals
                {where}
                ORDER BY scraped_at DESC
                LIMIT ${len(params)}
                """,  # noqa: S608 - identifiers are static, values parameterised
                *params,
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            ps = r["platform_specific"] or {}
            if isinstance(ps, str):
                try:
                    ps = json.loads(ps)
                except Exception:
                    ps = {}
            price = r["price_amount"]
            if price is None:
                price = ps.get("disc_price") or ps.get("price_inr")
            out.append({
                "signal_id": str(r["signal_id"]),
                "platform": r["platform"],
                "title": (r["title"] or "")[:140],
                "url": str(r["url"]) if r["url"] else None,
                "image_url": ps.get("image_url"),
                "price": float(price) if price is not None else None,
                "currency": ps.get("currency", "INR"),
                "discount_pct": ps.get("discount_pct"),
                "rating": ps.get("rating"),
                "review_count": ps.get("review_count"),
                "scraped_at": r["scraped_at"].isoformat() if r["scraped_at"] else None,
            })
        return out
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# /api/agents/recent  — reads the phase2 graph_results Redis stream
# ---------------------------------------------------------------------------
@app.get("/api/agents/recent")
async def agents_recent(limit: int = 15) -> list[dict[str, Any]]:
    try:
        entries = await _get_redis().xrevrange("aegis:phase2:graph_results", count=limit)
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for _entry_id, entry_data in entries:
        try:
            raw = entry_data.get("body") or entry_data.get("payload") or "{}"
            payload = json.loads(raw)
            decisions = payload.get("decisions", [])
            # Summarise decisions for the UI
            dec_summary = [
                {
                    "agent": d.get("agent", "?"),
                    "verdict": d.get("verdict", "?"),
                    "score": round(float(d.get("score", 0)), 3),
                    "confidence": round(float(d.get("confidence", 0)), 3),
                    "reasoning": (d.get("reasoning") or "")[:200],
                    "used_llm": d.get("used_llm", False),
                    "duration_ms": round(float(d.get("duration_ms", 0)), 1),
                }
                for d in decisions
            ]
            results.append(
                {
                    "stream_id": _entry_id,
                    "trend_id": payload.get("trend_id", "unknown"),
                    "final_verdict": payload.get("final_verdict", "unknown"),
                    "final_score": round(float(payload.get("final_score", 0)), 3),
                    "final_confidence": round(float(payload.get("final_confidence", 0)), 3),
                    "final_priority": payload.get("final_priority", 2),
                    "halt_reason": payload.get("halt_reason", "unknown"),
                    "duration_ms": round(float(payload.get("duration_ms", 0)), 1),
                    "started_at": payload.get("started_at", ""),
                    "finished_at": payload.get("finished_at", ""),
                    "blocked_by": payload.get("blocked_by", []),
                    "decisions": dec_summary,
                    # Provenance (HALLU-1): heuristic-only vs real LLM reasoning.
                    "llm_used": bool(payload.get("llm_used", False)),
                    "reasoning_source": payload.get("reasoning_source", "heuristic"),
                }
            )
        except Exception as _parse_exc:
            _sse_log.warning("intelligence.parse_error", entry_id=_entry_id, exc=str(_parse_exc))
    return results


# ---------------------------------------------------------------------------
# CONN-1 / DASH-2 — phase 7/8/9 event-bus consumers (geo / compliance / evolve)
# ---------------------------------------------------------------------------
async def _read_phase_stream(stream: str, limit: int) -> list[dict[str, Any]]:
    """Read recent ``{"body": json}`` entries from a phase event-bus stream."""
    try:
        entries = await _get_redis().xrevrange(stream, count=min(limit, 100))
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for entry_id, entry_data in entries:
        try:
            raw = entry_data.get("body") or "{}"
            payload = json.loads(raw)
            payload["stream_id"] = entry_id
            out.append(payload)
        except Exception as exc:
            _sse_log.warning("phase_stream.parse_error", stream=stream, entry_id=entry_id, exc=str(exc))
    return out


@app.get("/api/geo/recent")
async def geo_recent(limit: int = 15) -> list[dict[str, Any]]:
    """Recent cross-market arbitrage reports (Phase 7)."""
    return await _read_phase_stream("aegis:phase7:geo_opportunities", limit)


@app.get("/api/compliance/recent")
async def compliance_recent(limit: int = 15) -> list[dict[str, Any]]:
    """Recent compliance risk assessments (Phase 8)."""
    return await _read_phase_stream("aegis:phase8:compliance_assessments", limit)


@app.get("/api/evolve/recent")
async def evolve_recent(limit: int = 15) -> list[dict[str, Any]]:
    """Recent self-evolution events — retrains, drift, promotions (Phase 9)."""
    return await _read_phase_stream("aegis:phase9:evolve_events", limit)


@app.get("/api/swarm/recent")
async def swarm_recent(limit: int = 10) -> list[dict[str, Any]]:
    """DASH-2: recent swarm runs straight from the Redis results stream."""
    return await _read_phase_stream("aegis:swarm:results", limit)


@app.get("/api/sentinel/recent")
async def sentinel_recent(limit: int = 15) -> list[dict[str, Any]]:
    """Autonomous Market Sentinel discoveries (self-launched market reports)."""
    return await _read_phase_stream("aegis:sentinel:reports", limit)


@app.post("/api/sentinel/scan")
async def sentinel_scan() -> dict[str, Any]:
    """Trigger a Sentinel scan on demand (sweep radar → breakouts → reports).

    Runs the same loop the 45-min scheduler job runs, so the user can force a
    fresh autonomous discovery cycle from the dashboard.
    """
    try:
        import asyncpg

        from aegis.config import settings as _settings
        from aegis.scheduler.sentinel import MarketSentinel

        cfg = _settings()
        pool = await asyncpg.create_pool(cfg.pg_dsn_str, min_size=1, max_size=2)
        try:
            scan = await MarketSentinel(pool=pool, redis=_get_redis()).scan()
            return {"ok": True, **scan.to_dict()}
        finally:
            await pool.close()
    except Exception as exc:
        _sse_log.warning("sentinel.scan.error", exc=str(exc))
        return {"ok": False, "error": str(exc)[:200]}


@app.get("/api/anomalies/recent")
async def anomalies_recent(limit: int = 20) -> list[dict[str, Any]]:
    """DASH-2: recent scrape anomalies.

    The ``aegis:scrape:anomalies`` stream is populated by the online anomaly
    scorer (Pass 9 wiring); until a publisher runs, this returns [] and the
    panel shows its empty state.
    """
    return await _read_phase_stream("aegis:scrape:anomalies", limit)


@app.get("/api/search/semantic")
async def semantic_search(q: str, top_k: int = 10) -> dict[str, Any]:
    """Pass 9B: semantic signal search backed by the FAISS index.

    Returns ``{"available": bool, "query": str, "results": [...]}``. When
    faiss-cpu / an embedding gateway are absent, the index returns no results
    and ``available`` is False — the panel shows its empty state.
    """
    if not q or not q.strip():
        return {"available": False, "query": q, "results": []}
    try:
        from aegis.scrape.semantic_index import get_signal_index

        results = await get_signal_index().search(q.strip(), top_k=max(1, min(top_k, 50)))
        return {
            "available": bool(results),
            "query": q,
            "results": [
                {"signal_id": r.signal_id, "similarity": r.similarity, "metadata": r.metadata}
                for r in results
            ],
        }
    except Exception:
        return {"available": False, "query": q, "results": []}


@app.get("/api/capital/recent")
async def capital_recent(limit: int = 20) -> dict[str, Any]:
    """DASH-2: recent Phase 6 execution plans (advisory/staging/live)."""
    cfg = settings()
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            rows = await conn.fetch("""
                SELECT plan_id, trend_id, execution_mode, quantity,
                       fulfillment_method, total_capital_usd,
                       estimated_profit_usd, kelly_fraction_used,
                       risk_score, requires_approval, status, created_at
                FROM execution_plans
                ORDER BY created_at DESC
                LIMIT $1
            """, min(limit, 100))
        return {
            "status": "ok",
            "plans": [
                {
                    "plan_id": str(r["plan_id"]),
                    "trend_id": r["trend_id"],
                    "execution_mode": r["execution_mode"],
                    "quantity": r["quantity"],
                    "fulfillment_method": r["fulfillment_method"],
                    "total_capital_usd": float(r["total_capital_usd"] or 0),
                    "estimated_profit_usd": float(r["estimated_profit_usd"] or 0),
                    "kelly_fraction_used": float(r["kelly_fraction_used"] or 0),
                    "risk_score": float(r["risk_score"] or 0),
                    "requires_approval": r["requires_approval"],
                    "plan_status": r["status"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ],
        }
    except Exception as exc:
        # Table absent (migration 0007 not applied) or DB down — degrade, never 500.
        return {"status": "error", "error": str(exc)[:300], "plans": []}


@app.get("/api/dr/status")
async def dr_status() -> dict[str, Any]:
    """DASH-2: Phase 15 DR SLA snapshot — graceful when the module is absent.

    ``aegis-phase15`` is a standalone module with its own venv; in the main
    process it is usually not importable, so "unavailable" is the expected
    dev-mode answer, not an error.
    """
    try:
        from aegis.dr.health import DrHealthChecker  # type: ignore[import-not-found]
    except Exception:
        return {
            "status": "unavailable",
            "message": "aegis-phase15 DR module not installed in this process",
        }
    try:
        snapshot = await DrHealthChecker().check()
        data = snapshot.model_dump(mode="json") if hasattr(snapshot, "model_dump") else dict(snapshot)
        return {"status": "ok", "snapshot": data}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300]}


# Canonical event-bus streams surfaced by /api/health/streams. Keep in sync
# with aegis.core.event_bus and the Phase 0/2 publishers.
_CANONICAL_STREAMS = (
    "aegis:phase0:raw_signals",
    "aegis:phase2:graph_results",
    "aegis:phase7:geo_opportunities",
    "aegis:phase8:compliance_assessments",
    "aegis:phase9:evolve_events",
    "aegis:scrape:anomalies",
    "aegis:swarm:results",
)


@app.get("/api/health/streams")
async def stream_health() -> dict[str, Any]:
    """DASH-2: traffic-light health for every canonical AEGIS Redis stream."""
    result: dict[str, Any] = {}
    now_ms = datetime.now(UTC).timestamp() * 1000
    for stream in _CANONICAL_STREAMS:
        try:
            info = await _get_redis().xinfo_stream(stream)
            last_id = str(info.get("last-generated-id", "0-0"))
            last_ms = int(last_id.split("-")[0]) if last_id != "0-0" else 0
            age_s = (now_ms - last_ms) / 1000 if last_ms else None
            result[stream] = {
                "length": info.get("length", 0),
                "last_entry_age_seconds": round(age_s, 1) if age_s is not None else None,
                "status": "healthy" if age_s is not None and age_s < 3600 else "stale",
            }
        except Exception as exc:
            # Missing stream → "no key" error; show as empty, not broken.
            msg = str(exc)
            if "no such key" in msg.lower():
                result[stream] = {"length": 0, "last_entry_age_seconds": None, "status": "empty"}
            else:
                result[stream] = {"status": "error", "error": msg[:200]}
    return result


# ---------------------------------------------------------------------------
# /api/swarm/latest  — latest SwarmResult from Redis
# ---------------------------------------------------------------------------
@app.get("/api/swarm/latest")
async def swarm_latest() -> dict[str, Any]:
    try:
        raw = await _get_redis().get("aegis:swarm:latest")
        if not raw:
            return {"status": "no_data", "message": "No swarm run recorded yet"}
        return {"status": "ok", "data": json.loads(raw)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300]}


# ---------------------------------------------------------------------------
# /api/swarm/history  — last 24h swarm runs from swarm_results table
# ---------------------------------------------------------------------------
@app.get("/api/swarm/history")
async def swarm_history(limit: int = 10) -> dict[str, Any]:
    cfg = settings()
    try:
        now = datetime.now(UTC)
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            rows = await conn.fetch("""
                SELECT run_id, started_at, finished_at, total_signals, unique_signals,
                       dedup_removed, market_pulse, batch_confidence, conclusion
                FROM swarm_results
                WHERE started_at >= $1
                ORDER BY started_at DESC
                LIMIT $2
            """, now - timedelta(hours=24), limit)
        return {
            "status": "ok",
            "runs": [
                {
                    "run_id": str(r["run_id"]),
                    "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                    "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                    "total_signals": r["total_signals"],
                    "unique_signals": r["unique_signals"],
                    "dedup_removed": r["dedup_removed"],
                    "market_pulse": r["market_pulse"],
                    "batch_confidence": round(float(r["batch_confidence"] or 0), 3),
                    "conclusion": (r["conclusion"] or "")[:200],
                }
                for r in rows
            ],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300], "runs": []}


# ---------------------------------------------------------------------------
# /api/swarm/agents  — agent health from Redis hash
# ---------------------------------------------------------------------------
@app.get("/api/swarm/agents")
async def swarm_agents() -> dict[str, Any]:
    try:
        raw = await _get_redis().hgetall("aegis:swarm:agent_health")
        if not raw:
            return {"status": "no_data", "agents": {}}
        agents: dict[str, Any] = {}
        for name, val in raw.items():
            try:
                agents[name] = json.loads(val)
            except Exception:
                agents[name] = {"raw": val}
        return {"status": "ok", "agents": agents}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300], "agents": {}}


# ---------------------------------------------------------------------------
# DASH-3: DELETED /api/platforms/stats — superseded by /api/signals/platforms
# (wired in the SPA) which carries the same per-platform counts/confidence.
# DASH-3: DELETED /api/platforms/trends — superseded by /api/signals/velocity
# (wired) for time-series; 7-day hourly rollups were unused. [2026-06-11]
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/docker/status
# ---------------------------------------------------------------------------
@app.get("/api/docker/status")
async def docker_status() -> list[dict[str, Any]]:
    return await _docker_ps()


# ---------------------------------------------------------------------------
# /api/ops/run  — stream a CLI command back as text/plain
# ---------------------------------------------------------------------------
_ALLOWED_ROOTS = {"aegis", "uv", "python", "docker"}


class RunOp(BaseModel):
    cmd: list[str]


_ops_log = structlog.get_logger("aegis.dashboard.ops")


def _verify_ops_token(x_ops_token: str | None) -> None:
    """Raise 401 if ops token auth is required but token is missing/wrong.

    When AEGIS_DASHBOARD_OPS_TOKEN is unset in dev the endpoint is open
    (with a startup warning). In prod the token is always required.
    """
    cfg = settings()
    required_token = cfg.dashboard_ops_token
    if required_token is None:
        if cfg.is_prod:
            raise HTTPException(
                status_code=503,
                detail="AEGIS_DASHBOARD_OPS_TOKEN must be set in prod.",
            )
        return  # dev: allow without token
    provided = (x_ops_token or "").strip()
    import hmac as _hmac
    if not provided or not _hmac.compare_digest(
        provided, required_token.get_secret_value()
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Ops-Token.")


@app.post("/api/ops/run")
async def run_op(
    op: RunOp,
    x_ops_token: str | None = Header(default=None),
) -> StreamingResponse:
    _verify_ops_token(x_ops_token)
    if not op.cmd or op.cmd[0] not in _ALLOWED_ROOTS:
        raise HTTPException(status_code=400, detail=f"Root command must be one of: {_ALLOWED_ROOTS}")

    cmd = op.cmd
    if cmd[0] == "aegis":
        # Invoke via the current interpreter so the venv is guaranteed
        cmd = [sys.executable, "-m", "aegis.cli.main"] + cmd[1:]

    async def _stream() -> AsyncIterator[bytes]:
        yield f"$ {' '.join(op.cmd)}\n".encode()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(_REPO_ROOT),
            )
            if proc.stdout is None:
                yield b"\n[Error: subprocess stdout pipe not available]\n"
                return
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield line
            await proc.wait()
            _ops_log.info("ops.run.complete", cmd=op.cmd, returncode=proc.returncode)
            yield f"\n[Process exited with code {proc.returncode}]\n".encode()
        except Exception as exc:
            _ops_log.error("ops.run.error", cmd=op.cmd, exc=str(exc))
            yield f"\n[Error launching process: {exc}]\n".encode()

    return StreamingResponse(_stream(), media_type="text/plain")


# ---------------------------------------------------------------------------
# /api/swarm/run  — fire a full swarm harvest (all working adapters) in the
# background. Returns immediately; progress is observable via the SSE
# `swarm_complete` event and /api/swarm/latest. Guarded by the same ops token.
# ---------------------------------------------------------------------------
# Mutable holder (dict avoids `global` statements + keeps a task ref alive).
_swarm_state: dict[str, Any] = {"proc": None, "task": None}


@app.post("/api/swarm/run")
async def run_swarm(
    x_ops_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _verify_ops_token(x_ops_token)
    proc = _swarm_state["proc"]
    if proc is not None and proc.returncode is None:
        return {"status": "already_running", "message": "A swarm run is already in progress."}

    cmd = [sys.executable, "-m", "aegis.cli.main", "swarm", "run"]

    async def _launch() -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(_REPO_ROOT),
            )
            _swarm_state["proc"] = proc
            await proc.wait()
            _ops_log.info("swarm.run.complete", returncode=proc.returncode)
        except Exception as exc:
            _ops_log.error("swarm.run.error", exc=str(exc))

    _swarm_state["task"] = asyncio.create_task(_launch())
    return {
        "status": "started",
        "message": "Swarm harvest launched. Watch the live feed for a swarm_complete event.",
    }


@app.get("/api/swarm/run/status")
async def swarm_run_status() -> dict[str, Any]:
    """Whether a dashboard-triggered swarm run is currently in flight."""
    proc = _swarm_state["proc"]
    task = _swarm_state["task"]
    running = (proc is not None and proc.returncode is None) or (
        proc is None and task is not None and not task.done()
    )
    return {"running": running}


# ---------------------------------------------------------------------------
# /stream/events  — SSE feed (new signals, new verdicts, system ticks)
# ---------------------------------------------------------------------------
@app.get("/stream/events")
async def sse_events(request: Request) -> StreamingResponse:
    cfg = settings()

    async def _generate() -> AsyncIterator[bytes]:
        tick = 0
        _r = _get_redis()
        try:
            # ── Catch-up: emit the last 3 entries from each stream so the
            # client has immediate context on connect rather than waiting for
            # the next real-time event to arrive.
            last_phase2_id = "$"
            last_swarm_id = "$"
            try:
                catch_p2 = await _r.xrevrange("aegis:phase2:graph_results", count=3)
                if catch_p2:
                    last_phase2_id = catch_p2[0][0]  # most-recent ID → XREAD picks up from here
                    for _mid, data in reversed(catch_p2):
                        try:
                            raw = data.get("body") or data.get("payload") or "{}"
                            p = json.loads(raw)
                            evt = json.dumps({
                                "type": "analysis_complete",
                                "trend_id": p.get("trend_id"),
                                "verdict": p.get("final_verdict"),
                                "score": p.get("final_score"),
                                "confidence": p.get("final_confidence"),
                                "priority": p.get("final_priority"),
                                "duration_ms": p.get("duration_ms"),
                                "halt_reason": p.get("halt_reason"),
                                "agent_count": len(p.get("decisions", [])),
                                "ts": p.get("finished_at") or datetime.now(UTC).isoformat(),
                                "catchup": True,
                            })
                            yield f"data: {evt}\n\n".encode()
                        except Exception:
                            pass
            except Exception:
                last_phase2_id = "$"

            try:
                catch_sw = await _r.xrevrange("aegis:swarm:results", count=1)
                if catch_sw:
                    last_swarm_id = catch_sw[0][0]
                    for _mid, data in reversed(catch_sw):
                        try:
                            raw = data.get("body") or data.get("result") or "{}"
                            p = json.loads(raw)
                            evt = json.dumps({
                                "type": "swarm_complete",
                                "total_signals": p.get("total_signals", 0),
                                "unique_signals": p.get("unique_signals", 0),
                                "market_pulse": p.get("market_pulse", "neutral"),
                                "batch_confidence": p.get("batch_confidence", 0),
                                "platforms": len(p.get("by_platform", {})),
                                "ts": p.get("finished_at") or datetime.now(UTC).isoformat(),
                                "catchup": True,
                            })
                            yield f"data: {evt}\n\n".encode()
                        except Exception:
                            pass
            except Exception:
                last_swarm_id = "$"

            while not await request.is_disconnected():
                tick += 1

                # System health tick every 10 iterations (~15 s at 1500 ms block).
                # Reduced from 20 (30 s) so the signal count / last-scraped timestamp
                # stays fresh without hammering the DB.
                if tick % 10 == 1:
                    try:
                        async with _acquire_pg(cfg.pg_dsn_str, timeout=2.0) as conn:
                            cnt = await conn.fetchval("SELECT COUNT(*) FROM signals")
                            last_sig = await conn.fetchval("SELECT MAX(scraped_at) FROM signals")
                        ks_raw = await _r.get("aegis:execute:killswitch")
                        ks_tripped = (ks_raw or "").strip().upper() == "TRIPPED"
                        stream_len = 0
                        with contextlib.suppress(Exception):
                            stream_len = await _r.xlen("aegis:phase2:graph_results")
                        payload = json.dumps({
                            "type": "tick",
                            "signal_count": cnt,
                            "last_scraped": last_sig.isoformat() if last_sig else None,
                            "killswitch_tripped": ks_tripped,
                            "graph_stream_len": stream_len,
                            "ts": datetime.now(UTC).isoformat(),
                        })
                        yield f"data: {payload}\n\n".encode()
                    except Exception as exc:
                        _sse_log.warning("sse.health_tick_failed", error=str(exc))

                # XREAD both phase2 analysis results and swarm results simultaneously.
                # Blocks 1500 ms then yields to the event loop — no asyncio.sleep needed.
                try:
                    entries = await _r.xread(
                        {
                            "aegis:phase2:graph_results": last_phase2_id,
                            "aegis:swarm:results": last_swarm_id,
                        },
                        count=5,
                        block=1500,
                    )
                    if entries:
                        for stream_name, msgs in entries:
                            for mid, data in msgs:
                                if stream_name == "aegis:phase2:graph_results":
                                    last_phase2_id = mid
                                    try:
                                        raw = data.get("body") or data.get("payload") or "{}"
                                        p = json.loads(raw)
                                        evt = json.dumps({
                                            "type": "analysis_complete",
                                            "trend_id": p.get("trend_id"),
                                            "verdict": p.get("final_verdict"),
                                            "score": p.get("final_score"),
                                            "confidence": p.get("final_confidence"),
                                            "priority": p.get("final_priority"),
                                            "duration_ms": p.get("duration_ms"),
                                            "halt_reason": p.get("halt_reason"),
                                            "agent_count": len(p.get("decisions", [])),
                                            "ts": datetime.now(UTC).isoformat(),
                                        })
                                        yield f"data: {evt}\n\n".encode()
                                    except (json.JSONDecodeError, KeyError, TypeError) as exc:
                                        _sse_log.warning("sse.phase2_decode_failed", entry_id=mid, error=str(exc))
                                elif stream_name == "aegis:swarm:results":
                                    last_swarm_id = mid
                                    try:
                                        raw = data.get("body") or data.get("result") or "{}"
                                        p = json.loads(raw)
                                        evt = json.dumps({
                                            "type": "swarm_complete",
                                            "total_signals": p.get("total_signals", 0),
                                            "unique_signals": p.get("unique_signals", 0),
                                            "market_pulse": p.get("market_pulse", "neutral"),
                                            "batch_confidence": p.get("batch_confidence", 0),
                                            "platforms": len(p.get("by_platform", {})),
                                            "ts": datetime.now(UTC).isoformat(),
                                        })
                                        yield f"data: {evt}\n\n".encode()
                                    except (json.JSONDecodeError, KeyError, TypeError) as exc:
                                        _sse_log.warning("sse.swarm_decode_failed", entry_id=mid, error=str(exc))
                except (TimeoutError, _RedisTimeoutError) as exc:
                    # No new stream entries within the block window — expected
                    # when the system is idle. Not an error; keep streaming.
                    _sse_log.debug("sse.xread_idle", error=str(exc))
                except Exception as exc:
                    _sse_log.warning("sse.xread_failed", error=str(exc))
                    await asyncio.sleep(1.0)  # back off on real errors
        finally:
            pass  # _r is the shared pool — must not close it here

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# /api/search  — full-text search across signal titles
# ---------------------------------------------------------------------------
@app.get("/api/search")
async def search_signals(
    q: str = "",
    limit: int = 50,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    if not q.strip():
        return []
    cfg = settings()
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            _cols = """
                signal_id, platform, title, url, scraped_at,
                source_confidence AS sentiment,
                CASE WHEN price_amount IS NOT NULL THEN 0.9::real
                     WHEN intent = 'purchase' THEN 0.8::real
                     WHEN intent = 'save' THEN 0.6::real
                     ELSE 0.1::real
                END AS commercial_intent,
                completeness AS novelty,
                views, likes, comments, shares
            """
            if platform and platform != "all":
                rows = await conn.fetch(
                    f"SELECT {_cols} FROM signals WHERE title ILIKE $1 AND platform = $2"  # noqa: S608
                    " ORDER BY scraped_at DESC LIMIT $3",
                    f"%{q.strip()}%", platform, min(limit, 200),
                )
            else:
                rows = await conn.fetch(
                    f"SELECT {_cols} FROM signals WHERE title ILIKE $1"  # noqa: S608
                    " ORDER BY scraped_at DESC LIMIT $2",
                    f"%{q.strip()}%", min(limit, 200),
                )
        return [
            {
                "signal_id": str(r["signal_id"]),
                "platform": r["platform"],
                "title": (r["title"] or "")[:200],
                "url": str(r["url"]) if r["url"] else None,
                "scraped_at": r["scraped_at"].isoformat() if r["scraped_at"] else None,
                "sentiment": round(float(r["sentiment"] or 0), 3),
                "commercial_intent": round(float(r["commercial_intent"] or 0), 3),
                "novelty": round(float(r["novelty"] or 0), 3),
                "engagement": (r["likes"] or 0) + (r["comments"] or 0) + (r["shares"] or 0),
            }
            for r in rows
        ]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# /api/predictions/recent  — Phase 3 inference results
# ---------------------------------------------------------------------------
@app.get("/api/predictions/recent")
async def predictions_recent(limit: int = 20) -> list[dict[str, Any]]:
    cfg = settings()
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            rows = await conn.fetch("""
                SELECT prediction_id, trend_id, horizon_h,
                       p_breakout, p_decline, p_hold,
                       kelly_fraction, confidence, finished_at
                FROM predictions
                ORDER BY finished_at DESC
                LIMIT $1
            """, min(limit, 100))
        return [
            {
                "prediction_id": str(r["prediction_id"]),
                "trend_id": r["trend_id"],
                "horizon_h": r["horizon_h"],
                "p_breakout": round(float(r["p_breakout"] or 0), 3),
                "p_decline": round(float(r["p_decline"] or 0), 3),
                "p_hold": round(float(r["p_hold"] or 0), 3),
                "kelly_fraction": round(float(r["kelly_fraction"] or 0), 3),
                "confidence": round(float(r["confidence"] or 0), 3),
                "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# /api/execute/killswitch  — Phase 4 kill-switch (read + write)
# ---------------------------------------------------------------------------
@app.get("/api/execute/killswitch")
async def killswitch_state() -> dict[str, Any]:
    try:
        r = _get_redis()
        raw = await r.get("aegis:execute:killswitch")
        is_tripped = (raw or "").strip().upper() == "TRIPPED"
        return {
            "tripped": is_tripped,
            "state": "TRIPPED" if is_tripped else "ARMED",
        }
    except Exception as exc:
        return {"tripped": False, "state": "UNKNOWN", "error": str(exc)[:200]}


class _KillswitchAction(BaseModel):
    reason: str = "manual via dashboard"


@app.post("/api/execute/killswitch/trip")
async def killswitch_trip(
    action: _KillswitchAction,
    x_ops_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _verify_ops_token(x_ops_token)
    try:
        r = _get_redis()
        await r.set("aegis:execute:killswitch", "TRIPPED")
        ts = datetime.now(UTC).isoformat()
        _app_log.warning("dashboard.killswitch.tripped", reason=action.reason)
        return {"ok": True, "state": "TRIPPED", "ts": ts}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/execute/killswitch/arm")
async def killswitch_arm(
    action: _KillswitchAction,
    x_ops_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _verify_ops_token(x_ops_token)
    try:
        r = _get_redis()
        await r.set("aegis:execute:killswitch", "ARMED")
        ts = datetime.now(UTC).isoformat()
        _app_log.info("dashboard.killswitch.armed", reason=action.reason)
        return {"ok": True, "state": "ARMED", "ts": ts}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# DASH-3: DELETED /api/execute/alerts — duplicate of /api/alerts/recent (the
# wired, dashboard-shaped variant of the same alerts-table query). [2026-06-11]
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/llm/health  — Phase 11 LLM gateway provider health
# ---------------------------------------------------------------------------
@app.get("/api/llm/health")
async def llm_health_check() -> dict[str, Any]:
    try:
        from aegis.llm.bridge.agents_bridge import get_gateway  # type: ignore[import]
        gw = await get_gateway()
        if gw is None:
            return {"status": "unavailable", "providers": []}
        health = await gw.health()
        return {"status": "ok", "providers": health}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300], "providers": []}


# ---------------------------------------------------------------------------
# /api/agents/trend-history  — enriched list of recent trend analyses
# ---------------------------------------------------------------------------
@app.get("/api/agents/trend-history")
async def agents_trend_history(limit: int = 50) -> list[dict[str, Any]]:
    try:
        entries = await _get_redis().xrevrange("aegis:phase2:graph_results", count=min(limit, 200))
    except Exception:
        return []
    results: list[dict[str, Any]] = []
    for _eid, entry_data in entries:
        try:
            raw = entry_data.get("body") or entry_data.get("payload") or "{}"
            p = json.loads(raw)
            results.append({
                "stream_id": _eid,
                "trend_id": p.get("trend_id", ""),
                "final_verdict": p.get("final_verdict", "unknown"),
                "final_score": round(float(p.get("final_score", 0)), 3),
                "final_confidence": round(float(p.get("final_confidence", 0)), 3),
                "final_priority": p.get("final_priority", 2),
                "halt_reason": p.get("halt_reason", ""),
                "duration_ms": round(float(p.get("duration_ms", 0)), 1),
                "started_at": p.get("started_at", ""),
                "finished_at": p.get("finished_at", ""),
                "agent_count": len(p.get("decisions", [])),
                "data_confidence": round(float(p.get("data_confidence", 1.0)), 3),
                "explanation": p.get("explanation", ""),
                "counterfactual": p.get("counterfactual", ""),
                "primary_drivers": p.get("primary_drivers", []),
            })
        except Exception:
            pass
    return results


# ---------------------------------------------------------------------------
# /api/predictions/stats  — Phase 3 aggregate statistics
# ---------------------------------------------------------------------------
@app.get("/api/predictions/stats")
async def predictions_stats() -> dict[str, Any]:
    cfg = settings()
    try:
        now = datetime.now(UTC)
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM predictions")
            last_24h = await conn.fetchval(
                "SELECT COUNT(*) FROM predictions WHERE finished_at >= $1",
                now - timedelta(hours=24),
            )
            avg_conf = await conn.fetchval("""
                SELECT AVG((p->>'confidence')::float)
                FROM predictions, jsonb_array_elements(bundle_json->'predictions') AS p
                WHERE finished_at >= $1
            """, now - timedelta(hours=24))
            by_horizon = await conn.fetch("""
                SELECT
                  (p->>'horizon_hours')::int     AS horizon_h,
                  COUNT(*)                        AS n,
                  AVG((p->>'p_breakout')::float)  AS avg_breakout,
                  AVG((p->>'p_decline')::float)   AS avg_decline,
                  AVG((p->>'p_peak')::float)      AS avg_hold,
                  AVG((p->>'confidence')::float)  AS avg_kelly
                FROM predictions, jsonb_array_elements(bundle_json->'predictions') AS p
                WHERE finished_at >= $1
                GROUP BY 1 ORDER BY 1
            """, now - timedelta(hours=24))
            dist = await conn.fetchrow("""
                SELECT
                  COUNT(*) FILTER (WHERE (p->>'p_breakout')::float > (p->>'p_decline')::float
                    AND (p->>'p_breakout')::float > (p->>'p_peak')::float)    AS bullish,
                  COUNT(*) FILTER (WHERE (p->>'p_decline')::float > (p->>'p_breakout')::float
                    AND (p->>'p_decline')::float > (p->>'p_peak')::float)     AS bearish,
                  COUNT(*) FILTER (WHERE (p->>'p_peak')::float >= GREATEST(
                    (p->>'p_breakout')::float, (p->>'p_decline')::float))     AS neutral
                FROM predictions, jsonb_array_elements(bundle_json->'predictions') AS p
                WHERE finished_at >= $1
            """, now - timedelta(hours=24))
        return {
            "total": total,
            "last_24h": last_24h,
            "avg_confidence": round(float(avg_conf or 0), 3),
            "by_horizon": [
                {
                    "horizon_h": r["horizon_h"],
                    "count": r["n"],
                    "avg_breakout": round(float(r["avg_breakout"] or 0), 3),
                    "avg_decline":  round(float(r["avg_decline"]  or 0), 3),
                    "avg_hold":     round(float(r["avg_hold"]     or 0), 3),
                    "avg_kelly":    round(float(r["avg_kelly"]    or 0), 3),
                }
                for r in by_horizon
            ],
            "verdict_distribution": {
                "bullish": int(dist["bullish"] or 0) if dist else 0,
                "bearish": int(dist["bearish"] or 0) if dist else 0,
                "neutral": int(dist["neutral"] or 0) if dist else 0,
            },
        }
    except Exception as exc:
        return {"total": 0, "last_24h": 0, "avg_confidence": 0, "by_horizon": [], "error": str(exc)[:300]}


# ---------------------------------------------------------------------------
# /api/execute/alerts/stats  — Phase 4 alert aggregate statistics
# ---------------------------------------------------------------------------
@app.get("/api/execute/alerts/stats")
async def execute_alerts_stats() -> dict[str, Any]:
    cfg = settings()
    tenant = cfg.default_tenant_id or "00000000-0000-0000-0000-000000000001"
    try:
        now = datetime.now(UTC)
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            await conn.execute("SELECT set_config('app.current_tenant', $1, TRUE)", tenant)
            total = await conn.fetchval("SELECT COUNT(*) FROM alerts")
            last_24h = await conn.fetchval(
                "SELECT COUNT(*) FROM alerts WHERE created_at >= $1",
                now - timedelta(hours=24),
            )
            by_verdict = await conn.fetch("""
                SELECT verdict, COUNT(*) AS n
                FROM alerts WHERE created_at >= $1
                GROUP BY verdict ORDER BY n DESC
            """, now - timedelta(hours=24))
            avg_score = await conn.fetchval(
                "SELECT AVG(score) FROM alerts WHERE created_at >= $1",
                now - timedelta(hours=24),
            )
            hourly = await conn.fetch("""
                SELECT DATE_TRUNC('hour', created_at) AS hr, COUNT(*) AS n
                FROM alerts WHERE created_at >= $1
                GROUP BY hr ORDER BY hr
            """, now - timedelta(hours=24))
        r = _get_redis()
        ks_raw = await r.get("aegis:execute:killswitch")
        ks_tripped = (ks_raw or "").strip().upper() == "TRIPPED"
        return {
            "total": total,
            "last_24h": last_24h,
            "avg_score": round(float(avg_score or 0), 3),
            "by_verdict": {row["verdict"]: row["n"] for row in by_verdict},
            "hourly_24h": [{"hour": r["hr"].isoformat(), "count": r["n"]} for r in hourly],
            "killswitch": "TRIPPED" if ks_tripped else "ARMED",
        }
    except Exception as exc:
        return {"total": 0, "last_24h": 0, "avg_score": 0, "by_verdict": {}, "hourly_24h": [], "killswitch": "UNKNOWN", "error": str(exc)[:300]}


# ---------------------------------------------------------------------------
# /api/datalake/status  — Phase 10 data lake health snapshot
# ---------------------------------------------------------------------------
@app.get("/api/datalake/status")
async def datalake_status() -> dict[str, Any]:
    try:
        import importlib
        dl_mod = importlib.import_module("aegis.datalake")
        DataLake = getattr(dl_mod, "DataLake", None)
        if DataLake is None:
            return {"status": "unavailable", "message": "DataLake class not exported"}
        settings_mod = importlib.import_module("aegis.datalake.settings")
        DataLakeSettings = settings_mod.DataLakeSettings
        from pathlib import Path as _Path
        _dl_base = _Path("/tmp/aegis-datalake")  # noqa: S108
        dl_cfg = DataLakeSettings(
            use_local_filesystem=True,
            local_root=_dl_base / "local",
            catalog_db_path=_dl_base / "catalog.sqlite3",
            duckdb_temp_dir=_dl_base / "duckdb_tmp",
        )
        dl = DataLake.open(dl_cfg)
        health = dl.health()
        if asyncio.iscoroutine(health):
            health = await health
        dl.close()
        return {"status": "ok", "data": health}
    except ImportError:
        return {"status": "not_installed", "message": "aegis.datalake extra not installed"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300]}


# ---------------------------------------------------------------------------
# /api/signals/velocity  — per-platform hourly rate (last 48h)
# ---------------------------------------------------------------------------
class _ResearchRequest(BaseModel):
    topic: str
    limit: int = 20
    use_llm: bool = True


# ---------------------------------------------------------------------------
# Research helpers — job-based streaming (Phase 2)
# ---------------------------------------------------------------------------

def _cleanup_research_jobs() -> None:
    """Evict oldest jobs (any status) when the store exceeds ``MAX_RESEARCH_JOBS``.

    Eviction is age-ordered and status-agnostic: a stuck ``running`` job must never
    be able to pin the store. When an unfinished job is evicted its background task
    is cancelled so it cannot keep leaking DB/Redis connections.
    """
    if len(_research_jobs) <= MAX_RESEARCH_JOBS:
        return
    # Oldest first by start time; fall back to insertion order.
    ordered = sorted(
        _research_jobs.items(),
        key=lambda kv: kv[1].get("_started_dt") or datetime.min.replace(tzinfo=UTC),
    )
    overflow = len(_research_jobs) - MAX_RESEARCH_JOBS
    for k, v in ordered[:overflow]:
        task = v.get("_task")
        if task is not None and not task.done():
            task.cancel()
        del _research_jobs[k]


async def _run_research_job(job_id: str, topic: str, limit: int, use_llm: bool) -> None:
    """Background coroutine: runs scrape → dedup → agent pipeline, emitting SSE events."""
    job = _research_jobs[job_id]

    def _emit(evt: dict[str, Any]) -> None:
        job["events"].append(evt)

    try:
        from aegis.agents.runner import run_trend
        from aegis.agents.schemas import TrendCandidate
        from aegis.scrape.topic import expand_topic, scrape_topic

        _emit({"type": "progress", "step": "expanding",
               "msg": f'Expanding "{topic}" into search queries…'})
        expansion = expand_topic(topic)

        _emit({"type": "progress", "step": "scraping",
               "msg": f"Scraping across {len(expansion.search_terms)} queries from 7+ sources…"})

        global _research_pool  # noqa: PLW0603
        if _research_pool is None:
            from aegis.db.pool import PgPool as _PgPool
            _research_pool = _PgPool(dsn=_cfg.pg_dsn_str)
            await _research_pool.start()

        cfg = settings()
        scrape_result = await scrape_topic(
            topic,
            pool=_research_pool,
            tenant_id=_uuid_mod.UUID(cfg.default_tenant_id),
            limit_per_source=limit,
            dry_run=False,
            dedup_threshold=0.85,
        )

        unique = scrape_result.total_unique or 0
        sigs = scrape_result.signals
        first_sig = sigs[0] if sigs else None

        # ── Phase 3: compute real features from scraped signals ───────────────
        from aegis.scrape.sentiment import score_text as _score_text

        titles = [s.title for s in sigs if getattr(s, "title", None)]
        sent_scores = [_score_text(t) for t in titles] if titles else [0.0]
        avg_sent_raw = sum(sent_scores) / len(sent_scores)
        # TrendCandidate.sentiment is [0,1] (0.5=neutral); map from [-1,1]
        sentiment = round((avg_sent_raw + 1.0) / 2.0, 4)

        _COMMERCIAL_KW = frozenset({
            "buy", "price", "sale", "deal", "discount", "shop", "offer",
            "flipkart", "amazon", "product", "launch", "percent", "bestseller",
            "trending", "demand", "order", "delivery", "limited", "exclusive",
        })
        ci_hits = sum(1 for t in titles if any(k in t.lower() for k in _COMMERCIAL_KW))
        commercial_intent = round(ci_hits / max(len(titles), 1), 4)

        unique_author_set: set[str] = set()
        for _s in sigs:
            _auth = getattr(_s, "author", None)
            if _auth is not None:
                _handle = getattr(_auth, "handle", None) or str(_auth)
                if _handle:
                    unique_author_set.add(_handle)
        _author_diversity = len(unique_author_set) / max(len(sigs), 1)
        coordination_risk = round(max(0.0, 1.0 - min(_author_diversity * 3.0, 1.0)), 4)

        platform_set = {
            (_s.platform.value if hasattr(_s.platform, "value") else str(_s.platform))
            for _s in sigs if hasattr(_s, "platform")
        }
        novelty = round(min(1.0, len(platform_set) / 6.0), 4)
        platforms = list(platform_set)
        unique_authors_n = max(1, len(unique_author_set) or (unique // 3))
        # ── end feature computation ───────────────────────────────────────────

        _emit({"type": "progress", "step": "agents",
               "msg": f"Found {unique} unique signals across {len(platform_set)} platforms. Running 10-node agent pipeline…"})

        candidate = TrendCandidate(
            trend_id=f"research-{_uuid_mod.uuid4().hex[:8]}",
            title=topic,
            signal_count=unique,
            unique_authors=unique_authors_n,
            platforms=platforms,
            velocity_1h=float(unique),
            velocity_6h=float(unique) / 6.0,
            velocity_24h=float(unique) / 24.0,
            sentiment=sentiment,
            commercial_intent=commercial_intent,
            novelty=novelty,
            coordination_risk=coordination_risk,
            representative_text=(first_sig.title or "") if first_sig else "",
            representative_url=str(first_sig.url) if first_sig and first_sig.url else None,
        )

        # Flatten scraped ProductSignals into builder-compatible rows so
        # Phase 3 inference (SCOUT/SENTINEL) has real data to run on.
        # Without this the bridge gets signals=None and raises ValueError.
        def _sig_to_row(s: Any, sent: float) -> dict[str, Any]:
            eng = getattr(s, "engagement", None)
            plat = s.platform.value if hasattr(s.platform, "value") else str(s.platform)
            auth = getattr(s, "author", None)
            return {
                "id": str(getattr(s, "signal_id", "") or getattr(s, "content_hash", "") or ""),
                "platform": plat,
                "captured_at": getattr(s, "scraped_at", None),
                "title": getattr(s, "title", None),
                "body": getattr(s, "raw_text", None),
                "url": str(s.url) if getattr(s, "url", None) else None,
                "content_hash": getattr(s, "content_hash", None),
                "author_id": (getattr(auth, "handle", None) or getattr(auth, "platform_user_id", None)) if auth else None,
                "views": getattr(eng, "views", None) if eng else None,
                "likes": getattr(eng, "likes", None) if eng else None,
                "comments": getattr(eng, "comments", None) if eng else None,
                "shares": getattr(eng, "shares", None) if eng else None,
                "saves": getattr(eng, "saves", None) if eng else None,
                "sentiment": round((sent + 1.0) / 2.0, 4),
                "commercial_intent": commercial_intent,
                "novelty": novelty,
            }

        signal_rows = [
            _sig_to_row(s, sc)
            for s, sc in zip(sigs, sent_scores + [0.0] * len(sigs), strict=False)
            if getattr(s, "scraped_at", None) is not None
        ]

        graph_result = await run_trend(
            candidate=candidate,
            tenant_id=cfg.default_tenant_id,
            signals=signal_rows,
            use_llm=use_llm,
        )

        _emit({"type": "progress", "step": "done", "msg": "Pipeline complete. Preparing results…"})

        priority_val = (
            graph_result.final_priority.value
            if hasattr(graph_result.final_priority, "value")
            else str(graph_result.final_priority)
        )

        started_at: datetime = job["_started_dt"]
        duration_s = (datetime.now(UTC) - started_at).total_seconds()

        result: dict[str, Any] = {
            "topic": topic,
            "started_at": started_at.isoformat(),
            "duration_s": round(duration_s, 1),
            "scrape": {
                "total_fetched": scrape_result.total_fetched,
                "total_unique": scrape_result.total_unique,
                "duplicates_dropped": scrape_result.duplicates_dropped,
                "sources_hit": scrape_result.sources_hit,
                "errors": scrape_result.errors[:5],
            },
            "verdict": graph_result.final_verdict.value,
            "score": round(graph_result.final_score, 3),
            "confidence": round(graph_result.final_confidence, 3),
            "priority": priority_val,
            "halt_reason": graph_result.halt_reason,
            "decisions": [
                {
                    "agent": d.agent,
                    "verdict": d.verdict.value,
                    "score": round(d.score, 3),
                    "confidence": round(d.confidence, 3),
                    "reasoning": (d.reasoning or "")[:300],
                    "reasoning_full": (d.reasoning or "")[:2000],
                }
                for d in graph_result.decisions
            ],
            "features": {
                "sentiment": sentiment,
                "commercial_intent": commercial_intent,
                "novelty": novelty,
                "coordination_risk": coordination_risk,
                "platform_diversity": len(platform_set),
                "unique_authors": unique_authors_n,
                "market_pulse": (
                    "bullish" if avg_sent_raw > 0.15
                    else "bearish" if avg_sent_raw < -0.15
                    else "neutral"
                ),
            },
            "expansion": {
                "category": expansion.category,
                "related": expansion.related_entities[:5],
                "queries": expansion.search_terms[:5],
            },
            "patterns": [
                {
                    "label": p.label,
                    "signal_count": p.signal_count,
                    "is_high_priority": p.is_high_priority,
                    "velocity_slope": round(p.velocity_slope, 3),
                }
                for p in scrape_result.patterns[:5]
            ],
            "top_signals": [
                {
                    "title": (s.title or "")[:120],
                    "platform": s.platform.value if hasattr(s.platform, "value") else str(s.platform),
                    "url": str(s.url) if s.url else None,
                }
                for s in scrape_result.signals[:10]
            ],
        }

        job["result"] = result
        _emit({"type": "result", "data": result})
        job["status"] = "done"

    except Exception as exc:
        _app_log.warning("research.job.failed", job_id=job_id, error=str(exc))
        _emit({"type": "error", "detail": str(exc)[:500]})
        job["status"] = "error"


# ---------------------------------------------------------------------------
# /api/topic/research  — start a background job; stream progress via SSE
# ---------------------------------------------------------------------------

@app.post("/api/topic/research")
async def topic_research_start(body: _ResearchRequest) -> dict[str, Any]:
    """Start a background research job and return its *job_id* immediately.

    The caller should open ``GET /api/topic/research/{job_id}/stream`` to
    receive SSE progress events followed by the full structured result.
    """
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    limit = min(body.limit, 50)
    now = datetime.now(UTC)
    job_id = _uuid_mod.uuid4().hex[:12]
    _research_jobs[job_id] = {
        "status": "running",
        "events": [],
        "_started_dt": now,
        "started_at": now.isoformat(),
    }
    _cleanup_research_jobs()
    task = asyncio.create_task(
        _run_research_job_guarded(job_id, topic, limit, body.use_llm)
    )
    _research_jobs[job_id]["_task"] = task  # keep a ref so GC doesn't collect the task
    return {"job_id": job_id}


async def _run_research_job_guarded(
    job_id: str, topic: str, limit: int, use_llm: bool
) -> None:
    """Run a research job under a hard wall-clock timeout.

    On timeout or cancellation the job is marked ``error`` (not left ``running``)
    so it becomes evictable and the SSE stream terminates cleanly.
    """
    try:
        await asyncio.wait_for(
            _run_research_job(job_id, topic, limit, use_llm),
            timeout=RESEARCH_JOB_TIMEOUT_S,
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        job = _research_jobs.get(job_id)
        if job is not None and job.get("status") == "running":
            reason = "timed out" if isinstance(exc, TimeoutError) else "cancelled"
            job["events"].append({"type": "error", "detail": f"research job {reason}"})
            job["status"] = "error"


# ---------------------------------------------------------------------------
# PASS6-6B: /api/research/deep — multi-pass ResearchEngine report job
# ---------------------------------------------------------------------------


class _MarketRequest(BaseModel):
    query: str
    depth: str = "surface"  # "surface" | "standard" | "deep"
    max_products: int = 160
    use_llm: bool = True
    gate: bool = True


@app.post("/api/market/analyze")
async def market_analyze(body: _MarketRequest) -> dict[str, Any]:
    """Run the Product Intelligence Engine on a query and return a market report.

    Harvests live marketplace listings (dry-run, no DB write) and synthesizes a
    competitive view: price bands, competitors, top products, value picks and
    cross-platform arbitrage gaps. No default query — one is required.
    """
    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required — no default topic")
    depth = body.depth if body.depth in ("surface", "standard", "deep") else "surface"
    max_products = max(40, min(body.max_products, 400))

    from aegis.intelligence.product_intel import ProductIntelligenceEngine

    redis = None
    try:
        redis = await _get_redis()
    except Exception:
        redis = None
    engine = ProductIntelligenceEngine(pool=None, redis=redis)
    try:
        report = await asyncio.wait_for(
            engine.analyze(
                query,
                depth=depth,
                max_products=max_products,
                use_llm=body.use_llm,
                gate=body.gate,
            ),
            timeout=240,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="market analysis timed out — try a narrower query or surface depth",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return report.to_dict()


class _DeepResearchRequest(BaseModel):
    topic: str
    depth: str = "standard"  # "surface" | "standard" | "deep"
    max_signals: int = 200


async def _run_deep_research_job(
    job_id: str, topic: str, depth: str, max_signals: int
) -> None:
    """Background coroutine: run the 5-pass ResearchEngine and emit the report."""
    job = _research_jobs[job_id]
    try:
        from aegis.intelligence.research_engine import ResearchEngine

        job["events"].append({
            "type": "progress", "step": "research",
            "msg": f'Running {depth} multi-pass research on "{topic}"…',
        })
        engine = ResearchEngine(pool=_research_pool, redis=_get_redis())
        report = await engine.research(topic, depth=depth, max_signals=max_signals)
        result = report.to_dict()
        job["result"] = result
        job["events"].append({"type": "result", "data": result})
        job["status"] = "done"
    except Exception as exc:
        _app_log.warning("research.deep_job_failed", job_id=job_id, error=str(exc))
        job["events"].append({"type": "error", "detail": str(exc)[:500]})
        job["status"] = "error"


async def _run_deep_research_job_guarded(
    job_id: str, topic: str, depth: str, max_signals: int
) -> None:
    """Deep-research variant of the guarded research runner (same eviction rules)."""
    try:
        await asyncio.wait_for(
            _run_deep_research_job(job_id, topic, depth, max_signals),
            timeout=RESEARCH_JOB_TIMEOUT_S,
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        job = _research_jobs.get(job_id)
        if job is not None and job.get("status") == "running":
            reason = "timed out" if isinstance(exc, TimeoutError) else "cancelled"
            job["events"].append({"type": "error", "detail": f"research job {reason}"})
            job["status"] = "error"


@app.post("/api/research/deep")
async def deep_research_start(body: _DeepResearchRequest) -> dict[str, Any]:
    """Start a multi-pass ResearchEngine job; stream via the research SSE endpoint.

    Returns a *job_id*; the caller opens ``GET /api/topic/research/{job_id}/stream``
    (shared with topic research) to receive progress and the final report.
    """
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    depth = body.depth if body.depth in ("surface", "standard", "deep") else "standard"
    max_signals = max(10, min(body.max_signals, 500))
    now = datetime.now(UTC)
    job_id = _uuid_mod.uuid4().hex[:12]
    _research_jobs[job_id] = {
        "status": "running",
        "events": [],
        "_started_dt": now,
        "started_at": now.isoformat(),
    }
    _cleanup_research_jobs()
    task = asyncio.create_task(
        _run_deep_research_job_guarded(job_id, topic, depth, max_signals)
    )
    _research_jobs[job_id]["_task"] = task  # keep a ref so GC doesn't collect the task
    return {"job_id": job_id}


@app.get("/api/topic/research/{job_id}/stream")
async def research_job_stream(job_id: str) -> StreamingResponse:
    """SSE stream for a running research job.

    Emits ``progress`` events (``step``, ``msg``) until the pipeline finishes,
    then emits a single ``result`` event containing the full payload.
    Emits ``error`` on failure.
    """
    if job_id not in _research_jobs:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    async def _gen() -> AsyncIterator[str]:
        idx = 0
        while True:
            job = _research_jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'type': 'error', 'detail': 'Job expired'})}\n\n"
                return
            events: list[dict[str, Any]] = job["events"]
            while idx < len(events):
                yield f"data: {json.dumps(events[idx])}\n\n"
                idx += 1
            if job["status"] in ("done", "error"):
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# /api/watchlist  — persistent topic monitoring list
# ---------------------------------------------------------------------------

class _WatchlistTopicBody(BaseModel):
    topic: str


@app.get("/api/watchlist")
async def watchlist_list() -> dict[str, Any]:
    """Return the current watchlist topics with their last-run metadata."""
    return {"watchlist": await _load_watchlist()}


@app.post("/api/watchlist/add")
async def watchlist_add(body: _WatchlistTopicBody) -> dict[str, Any]:
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    wl = await _load_watchlist()
    if any(w["topic"].lower() == topic.lower() for w in wl):
        return {"ok": True, "message": "already in watchlist"}
    wl.append({"topic": topic, "added_at": datetime.now(UTC).isoformat(),
                "last_verdict": None, "last_score": None, "last_run_at": None})
    await _save_watchlist(wl)
    return {"ok": True, "watchlist": wl}


@app.post("/api/watchlist/remove")
async def watchlist_remove(body: _WatchlistTopicBody) -> dict[str, Any]:
    topic = body.topic.strip()
    wl = await _load_watchlist()
    wl = [w for w in wl if w["topic"].lower() != topic.lower()]
    await _save_watchlist(wl)
    return {"ok": True, "watchlist": wl}


@app.post("/api/topic/research/{job_id}/push-alert")
async def push_research_alert(job_id: str) -> dict[str, Any]:
    """Publish a completed research result to the Phase 4 alert intake stream.

    Maps the research verdict to Phase 4 vocabulary and XADDs to
    ``aegis:phase2:graph_results`` so the IntakeWorker picks it up.
    """
    job = _research_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job_id")
    result = job.get("result")
    if not result:
        raise HTTPException(status_code=409, detail="Job not yet complete")
    raw_verdict = result.get("verdict", "hold")
    p4_verdict = _VERDICT_TO_P4.get(raw_verdict, "HOLD")
    body = {
        "trend_id": f"research-{job_id}",
        "final_verdict": p4_verdict,
        "final_score": result.get("score", 0.0),
        "final_confidence": result.get("confidence", 0.0),
        "final_priority": "HIGH" if (result.get("score") or 0) >= 0.75 else "MEDIUM",
        "halt_reason": result.get("halt_reason"),
        "started_at": result.get("started_at"),
        "finished_at": datetime.now(UTC).isoformat(),
        "duration_ms": int((result.get("duration_s") or 0) * 1000),
        "data_confidence": 1.0,
        "raw_verdict": raw_verdict,
        "decisions": result.get("decisions", []),
        "source": "dashboard_research",
        "topic": result.get("topic", ""),
    }
    try:
        await _get_redis().xadd(
            "aegis:phase2:graph_results",
            {"body": json.dumps(body)},
            maxlen=10_000,
            approximate=True,
        )
        return {"ok": True, "p4_verdict": p4_verdict, "trend_id": body["trend_id"]}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}") from exc


@app.get("/api/topic/research/history")
async def research_history() -> dict[str, Any]:
    """Return metadata for recent completed research jobs (newest first)."""
    completed = [
        {
            "job_id": jid,
            "topic": (v.get("result") or {}).get("topic", ""),
            "verdict": (v.get("result") or {}).get("verdict"),
            "score": (v.get("result") or {}).get("score"),
            "started_at": v.get("started_at"),
            "status": v["status"],
        }
        for jid, v in _research_jobs.items()
        if v["status"] in ("done", "error")
    ]
    completed.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return {"history": completed[:10]}


@app.get("/api/signals/velocity")
async def signals_velocity() -> dict[str, Any]:
    cfg = settings()
    try:
        now = datetime.now(UTC)
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            rows = await conn.fetch("""
                SELECT platform,
                       DATE_TRUNC('hour', scraped_at) AS hr,
                       COUNT(*) AS n
                FROM signals
                WHERE scraped_at >= $1
                GROUP BY platform, hr
                ORDER BY hr ASC
            """, now - timedelta(hours=48))
        velocity: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            p = r["platform"]
            if p not in velocity:
                velocity[p] = []
            velocity[p].append({"hour": r["hr"].isoformat(), "count": r["n"]})
        current_rates = {p: pts[-1]["count"] for p, pts in velocity.items() if pts}
        return {"status": "ok", "velocity": velocity, "current_rates": current_rates}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300], "velocity": {}, "current_rates": {}}


# ---------------------------------------------------------------------------
# New convenience endpoints for the production dashboard
# ---------------------------------------------------------------------------

@app.get("/api/stats")
async def dashboard_stats() -> dict[str, Any]:
    """Aggregate system-wide stats for the dashboard overview."""
    cfg = settings()
    now = datetime.now(UTC)
    result: dict[str, Any] = {
        "total_signals": 0, "signals_24h": 0, "signals_1h": 0,
        "total_alerts": 0, "alerts_24h": 0, "working_adapters": 0,
        "prediction_outcomes": 0, "avg_signal_confidence": 0.0,
        "pipeline_latency_s": 52.5, "last_scrape_at": None,
        "last_alert_at": None, "verdict_distribution": {},
        "uptime_s": 0,
    }
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE scraped_at >= $1) AS last_24h,
                    COUNT(*) FILTER (WHERE scraped_at >= $2) AS last_1h,
                    ROUND(AVG(source_confidence)::numeric, 3) AS avg_conf,
                    MAX(scraped_at) AS last_scraped,
                    COUNT(DISTINCT platform) FILTER (WHERE scraped_at >= $1) AS active_platforms
                FROM signals
            """, now - timedelta(hours=24), now - timedelta(hours=1))
            if row:
                result["total_signals"] = row["total"] or 0
                result["signals_24h"] = row["last_24h"] or 0
                result["signals_1h"] = row["last_1h"] or 0
                result["avg_signal_confidence"] = float(row["avg_conf"] or 0)
                result["last_scrape_at"] = row["last_scraped"].isoformat() if row["last_scraped"] else None
                result["working_adapters"] = row["active_platforms"] or 0

            arow = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE created_at >= $1) AS last_24h,
                    MAX(created_at) AS last_at
                FROM alerts
            """, now - timedelta(hours=24))
            if arow:
                result["total_alerts"] = arow["total"] or 0
                result["alerts_24h"] = arow["last_24h"] or 0
                result["last_alert_at"] = arow["last_at"].isoformat() if arow["last_at"] else None

            vrows = await conn.fetch("""
                SELECT verdict AS v, COUNT(*) AS n
                FROM alerts
                GROUP BY verdict
            """)
            vd: dict[str, int] = {}
            for vr in vrows:
                key = str(vr["v"] or "").upper()
                vd[key] = int(vr["n"])
            result["verdict_distribution"] = vd

            orow = await conn.fetchrow("SELECT COUNT(*) AS n FROM prediction_outcomes")
            if orow:
                result["prediction_outcomes"] = orow["n"] or 0
    except Exception as exc:
        _app_log.warning("dashboard_stats.error", error=str(exc)[:200])

    # Real pipeline latency: median of recent stream run durations (ms→s).
    # Replaces the hardcoded 52.5s placeholder.
    try:
        entries = await _get_redis().xrevrange("aegis:phase2:graph_results", count=20)
        durs = []
        for _sid, fields in entries:
            try:
                d = json.loads(fields.get("body", "{}")).get("duration_ms")
                if d:
                    durs.append(float(d))
            except Exception:
                pass
        if durs:
            durs.sort()
            result["pipeline_latency_s"] = round(durs[len(durs) // 2] / 1000.0, 2)
    except Exception:
        pass
    return result


@app.get("/api/signals/platforms")
async def signals_platforms() -> list[dict[str, Any]]:
    """Per-platform signal counts, avg confidence, and latest signal time."""
    cfg = settings()
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            rows = await conn.fetch("""
                SELECT
                    platform,
                    COUNT(*) AS cnt,
                    ROUND(AVG(source_confidence)::numeric, 3) AS avg_conf,
                    MAX(scraped_at) AS latest
                FROM signals
                GROUP BY platform
                ORDER BY cnt DESC
            """)
        return [
            {
                "platform": r["platform"],
                "count": r["cnt"],
                "avg_confidence": float(r["avg_conf"] or 0),
                "latest": r["latest"].isoformat() if r["latest"] else None,
            }
            for r in rows
        ]
    except Exception as exc:
        _app_log.warning("signals_platforms.error", error=str(exc)[:200])
        return []


@app.get("/api/alerts/recent")
async def alerts_recent(limit: int = 20) -> list[dict[str, Any]]:
    """Recent alerts with full data — dashboard-friendly shape."""
    cfg = settings()
    tenant = cfg.default_tenant_id or "00000000-0000-0000-0000-000000000001"
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            await conn.execute("SELECT set_config('app.current_tenant', $1, TRUE)", tenant)
            rows = await conn.fetch("""
                SELECT
                    alert_id, trend_id, verdict, priority,
                    score, confidence, source,
                    summary_text, halt_reason, blocked_by,
                    p_breakout_24h, p_decline_6h, expected_margin_usd,
                    created_at
                FROM alerts
                ORDER BY created_at DESC
                LIMIT $1
            """, limit)
        return [
            {
                "alert_id": r["alert_id"],
                "trend_id": r["trend_id"],
                "verdict": (r["verdict"] or "").upper(),
                "priority": r["priority"],
                "score": round(float(r["score"] or 0), 3),
                "confidence": round(float(r["confidence"] or 0), 3),
                "explanation": (r["summary_text"] or "")[:500],
                "primary_drivers": [],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "p_breakout_24h": round(float(r["p_breakout_24h"]), 3) if r["p_breakout_24h"] is not None else None,
                "p_decline_6h": round(float(r["p_decline_6h"]), 3) if r["p_decline_6h"] is not None else None,
            }
            for r in rows
        ]
    except Exception as exc:
        _app_log.warning("alerts_recent.error", error=str(exc)[:200])
        return []


@app.get("/api/adapters/status")
async def adapters_status() -> list[dict[str, Any]]:
    """All known adapters with health status derived from DB signal recency."""
    cfg = settings()
    known_adapters = [
        "hacker_news", "reddit", "github_trending", "amazon",
        "google_news", "bing_news", "google_trends", "techcrunch", "wired",
        "bbc_news", "reuters", "ndtv_profit", "mint", "business_standard",
        "yahoo_finance", "investing_com", "medium", "devto", "github_public",
        "reddit_finance", "reddit_ecommerce", "youtube_rss", "google_trends_india",
        "producthunt", "npm_trends", "moneycontrol", "economic_times",
        "nse_bse", "screener_in", "amazon_in", "flipkart", "meesho",
        "myntra", "ajio", "nykaa", "snapdeal", "indiamart",
    ]
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            rows = await conn.fetch("""
                SELECT
                    platform,
                    COUNT(*) FILTER (WHERE scraped_at >= $1) AS cnt_24h,
                    MAX(scraped_at) AS latest
                FROM signals
                GROUP BY platform
            """, cutoff_24h)
        db: dict[str, dict[str, Any]] = {}
        for r in rows:
            db[r["platform"]] = {
                "signals_24h": r["cnt_24h"] or 0,
                "latest": r["latest"],
            }
    except Exception as exc:
        _app_log.warning("adapters_status.db_error", error=str(exc)[:200])
        db = {}

    results = []
    seen = set()
    for name in known_adapters:
        seen.add(name)
        info = db.get(name, {})
        latest = info.get("latest")
        cnt_24h = info.get("signals_24h", 0)
        if latest and latest > cutoff_24h:
            status = "working"
        elif latest and latest > cutoff_7d:
            status = "idle"
        else:
            status = "dead"
        results.append({
            "name": name,
            "status": status,
            "signals_24h": cnt_24h,
            "last_signal_at": latest.isoformat() if latest else None,
        })
    # Include any DB platforms not in known list
    for platform, info in db.items():
        if platform not in seen:
            latest = info.get("latest")
            results.append({
                "name": platform,
                "status": "working" if latest and latest > cutoff_24h else "idle",
                "signals_24h": info.get("signals_24h", 0),
                "last_signal_at": latest.isoformat() if latest else None,
            })
    results.sort(key=lambda x: (0 if x["status"] == "working" else 1 if x["status"] == "idle" else 2, x["name"]))
    return results


@app.get("/api/pipeline/live")
async def pipeline_live() -> dict[str, Any]:
    """Live pipeline state: stream length, killswitch, worker status."""
    cfg = settings()
    result: dict[str, Any] = {
        "stream_length": 0,
        "last_processed_at": None,
        "killswitch_armed": True,
        "drain_running": False,
        "intake_running": False,
        # Real values — replace the previously hardcoded frontend metrics.
        "db_signal_count": 0,
        "active_adapters": 0,
        "recent_pipeline_ms": None,
        "services": {},
    }
    try:
        redis = _get_redis()
        length = await redis.xlen("aegis:phase2:graph_results")
        result["stream_length"] = length or 0
        entries = await redis.xrevrange("aegis:phase2:graph_results", count=20)
        durations: list[float] = []
        for i, (_sid, fields) in enumerate(entries):
            try:
                body = json.loads(fields.get("body", "{}"))
                if i == 0:
                    ts = body.get("finished_at") or body.get("started_at")
                    if ts:
                        result["last_processed_at"] = ts
                d = body.get("duration_ms")
                if d:
                    durations.append(float(d))
            except Exception:
                pass
        if durations:
            durations.sort()
            # p95 of recent pipeline durations (real, not the hardcoded 52.5s).
            idx = max(0, int(len(durations) * 0.95) - 1)
            result["recent_pipeline_ms"] = round(durations[idx], 1)
        ks = await redis.get("aegis:execute:killswitch")
        result["killswitch_armed"] = not (ks and ks.lower() in ("1", "tripped", "true"))
        result["drain_running"] = bool(await redis.get("aegis:execute:drain:running"))
        result["intake_running"] = bool(await redis.get("aegis:execute:intake:running"))
    except Exception as exc:
        _app_log.warning("pipeline_live.error", error=str(exc)[:200])

    # Real DB signal count + active adapters (distinct platforms in last 24h).
    services: dict[str, bool] = {}
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            result["db_signal_count"] = await conn.fetchval("SELECT COUNT(*) FROM signals") or 0
            result["active_adapters"] = await conn.fetchval(
                "SELECT COUNT(DISTINCT platform) FROM signals WHERE scraped_at >= $1",
                datetime.now(UTC) - timedelta(hours=24),
            ) or 0
        services["postgres"] = True
    except Exception:
        services["postgres"] = False
    try:
        await _get_redis().ping()
        services["redis"] = True
    except Exception:
        services["redis"] = False

    # Probe sibling FastAPI services rather than claiming they're UP.
    async def _probe(url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                return (await c.get(url)).status_code < 500
        except Exception:
            return False

    predict_ok, execute_ok = await asyncio.gather(
        _probe("http://localhost:8100/healthz"),
        _probe("http://localhost:8200/healthz"),
    )
    services["predict"] = predict_ok
    services["execute_api"] = execute_ok
    services["dashboard"] = True  # if this handler runs, the dashboard is up
    result["services"] = services
    return result
