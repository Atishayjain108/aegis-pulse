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

from aegis.config import settings

_sse_log = structlog.get_logger("aegis.dashboard.sse")

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
# Capped at ~20 entries; completed entries evicted by _cleanup_research_jobs().
_research_jobs: dict[str, dict[str, Any]] = {}
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
            socket_timeout=3.0,
            max_connections=10,
        )
    return _redis_pool


@_asynccontextmanager
async def _acquire_pg(dsn: str, timeout: float = 3.0) -> AsyncIterator[Any]:
    """Acquire a connection from the shared pool, falling back to a raw connect.

    Uses the process-singleton asyncpg.Pool when available (warm startup).
    Falls back to asyncpg.connect() during startup or if pool creation failed.
    """
    if _pg_pool is not None:
        async with _pg_pool.acquire(timeout=timeout) as conn:
            yield conn
    else:
        conn = await asyncpg.connect(dsn, timeout=timeout)
        try:
            yield conn
        finally:
            await conn.close()


async def _docker_ps() -> list[dict[str, Any]]:
    """Query the Docker Engine API via Unix socket — no docker CLI needed.

    Returns an empty list (not an error sentinel) when Docker is unreachable so
    the frontend pill shows "Docker 0/0" instead of the misleading "Docker 0/1"
    that appeared when the error dict was counted as a container entry.
    """
    try:
        transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost", timeout=5.0) as client:
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


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Create shared connection pools on startup; close them on shutdown."""
    global _pg_pool, _redis_pool, _research_pool  # noqa: PLW0603
    try:
        _pg_pool = await asyncpg.create_pool(_cfg.pg_dsn_str, min_size=2, max_size=10)
    except Exception as exc:
        _app_log.warning("dashboard.pg_pool_unavailable", error=str(exc))
    try:
        from aegis.db.pool import PgPool as _PgPool
        _research_pool = _PgPool(dsn=_cfg.pg_dsn_str)
        await _research_pool.start()
    except Exception as exc:
        _app_log.warning("dashboard.research_pool_unavailable", error=str(exc))
    try:
        yield
    finally:
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
    return HTMLResponse(p.read_text())


@app.get("/healthz")
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
                }
            )
        except Exception as _parse_exc:
            _sse_log.warning("intelligence.parse_error", entry_id=_entry_id, exc=str(_parse_exc))
    return results


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
# /api/platforms/stats  — signal counts by platform (last 24h)
# ---------------------------------------------------------------------------
@app.get("/api/platforms/stats")
async def platform_stats() -> dict[str, Any]:
    cfg = settings()
    try:
        now = datetime.now(UTC)
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            rows = await conn.fetch("""
                SELECT platform,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE scraped_at >= $2) AS last_24h,
                       AVG(source_confidence) AS avg_sentiment,
                       AVG(CASE WHEN price_amount IS NOT NULL THEN 0.9::real
                                WHEN intent = 'purchase' THEN 0.8::real
                                WHEN intent = 'save' THEN 0.6::real
                                ELSE 0.1::real
                           END) AS avg_commercial_intent
                FROM signals
                WHERE scraped_at >= $1
                GROUP BY platform
                ORDER BY last_24h DESC
            """, now - timedelta(hours=24), now - timedelta(hours=24))
        return {
            "status": "ok",
            "platforms": {
                r["platform"]: {
                    "total": r["total"],
                    "last_24h": r["last_24h"],
                    "avg_sentiment": round(float(r["avg_sentiment"] or 0), 3),
                    "avg_commercial_intent": round(float(r["avg_commercial_intent"] or 0), 3),
                }
                for r in rows
            },
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300], "platforms": {}}


# ---------------------------------------------------------------------------
# /api/platforms/trends  — hourly signal velocity per platform, last 7 days
# ---------------------------------------------------------------------------
@app.get("/api/platforms/trends")
async def platform_trends() -> dict[str, Any]:
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
            """, now - timedelta(days=7))
        trends: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            p = r["platform"]
            if p not in trends:
                trends[p] = []
            trends[p].append({"hour": r["hr"].isoformat(), "count": r["n"]})
        return {"status": "ok", "trends": trends}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300], "trends": {}}


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
                except Exception as exc:
                    _sse_log.warning("sse.xread_failed", error=str(exc))
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
# /api/execute/alerts  — Phase 4 recent alerts from DB
# ---------------------------------------------------------------------------
@app.get("/api/execute/alerts")
async def execute_alerts_list(limit: int = 30) -> list[dict[str, Any]]:
    cfg = settings()
    tenant = cfg.default_tenant_id or "00000000-0000-0000-0000-000000000001"
    try:
        async with _acquire_pg(cfg.pg_dsn_str) as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, TRUE)", tenant
            )
            rows = await conn.fetch("""
                SELECT alert_id, trend_id, verdict, priority, score, confidence,
                       source, title, halt_reason, blocked_by, created_at,
                       p_breakout_24h, p_decline_6h, expected_margin_usd,
                       advised_capital_usd
                FROM alerts
                ORDER BY created_at DESC
                LIMIT $1
            """, min(limit, 100))
        return [
            {
                "alert_id": r["alert_id"],
                "trend_id": r["trend_id"],
                "verdict": r["verdict"],
                "priority": r["priority"],
                "score": round(float(r["score"] or 0), 3),
                "confidence": round(float(r["confidence"] or 0), 3),
                "source": r["source"],
                "title": (r["title"] or "")[:200],
                "halt_reason": r["halt_reason"],
                "blocked_by": list(r["blocked_by"] or []),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "p_breakout_24h": round(float(r["p_breakout_24h"]), 3) if r["p_breakout_24h"] is not None else None,
                "p_decline_6h": round(float(r["p_decline_6h"]), 3) if r["p_decline_6h"] is not None else None,
                "expected_margin_usd": round(float(r["expected_margin_usd"]), 2) if r["expected_margin_usd"] is not None else None,
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# /api/llm/health  — Phase 11 LLM gateway provider health
# ---------------------------------------------------------------------------
@app.get("/api/llm/health")
async def llm_health_check() -> dict[str, Any]:
    try:
        from aegis.llm.bridge.agents_bridge import get_gateway  # type: ignore[import]
        gw = get_gateway()
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
            avg_conf = await conn.fetchval(
                "SELECT AVG(confidence) FROM predictions WHERE finished_at >= $1",
                now - timedelta(hours=24),
            )
            by_horizon = await conn.fetch("""
                SELECT horizon_h,
                       COUNT(*) AS n,
                       AVG(p_breakout)      AS avg_breakout,
                       AVG(p_decline)       AS avg_decline,
                       AVG(p_hold)          AS avg_hold,
                       AVG(kelly_fraction)  AS avg_kelly
                FROM predictions
                WHERE finished_at >= $1
                GROUP BY horizon_h ORDER BY horizon_h
            """, now - timedelta(hours=24))
            dist = await conn.fetchrow("""
                SELECT
                  COUNT(*) FILTER (WHERE p_breakout > p_decline AND p_breakout > p_hold) AS bullish,
                  COUNT(*) FILTER (WHERE p_decline > p_breakout AND p_decline > p_hold)  AS bearish,
                  COUNT(*) FILTER (WHERE p_hold >= GREATEST(p_breakout, p_decline))      AS neutral
                FROM predictions WHERE finished_at >= $1
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
        dl_cfg = DataLakeSettings()
        dl = DataLake(dl_cfg)
        health = dl.health()
        if asyncio.iscoroutine(health):
            health = await health
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
    """Evict old completed jobs when the in-memory store exceeds 20 entries."""
    if len(_research_jobs) <= 20:
        return
    done = [k for k, v in _research_jobs.items() if v["status"] in ("done", "error")]
    for k in done[:10]:
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

        graph_result = await run_trend(
            candidate=candidate,
            tenant_id=cfg.default_tenant_id,
            signals=[],
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
    task = asyncio.create_task(_run_research_job(job_id, topic, limit, body.use_llm))
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
