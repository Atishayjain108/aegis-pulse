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
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, HTTPException, Request
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


async def _docker_ps() -> list[dict[str, Any]]:
    """Query the Docker Engine API via Unix socket — no docker CLI needed."""
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
                    "State": c.get("State", "?"),
                    "Status": c.get("Status", "?"),
                }
                for c in raw
            ]
    except Exception as exc:
        return [{"error": str(exc)[:200]}]

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="AEGIS Command Center", version="1.0.0", docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


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
        conn = await asyncpg.connect(cfg.pg_dsn_str, timeout=3.0)
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
        await conn.close()
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
        r = aioredis.from_url(cfg.redis_url_str, decode_responses=True, socket_timeout=3.0)
        await r.ping()
        info = await r.info()
        stream_len = 0
        with contextlib.suppress(Exception):
            stream_len = await r.xlen("aegis:phase2:graph_results")
        await r.aclose()
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
        conn = await asyncpg.connect(cfg.pg_dsn_str, timeout=3.0)
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

        # avg sentiment / commercial_intent by platform
        sentiment_avg = await conn.fetch("""
            SELECT platform,
                   AVG(sentiment) AS avg_sentiment,
                   AVG(commercial_intent) AS avg_commercial_intent,
                   AVG(novelty) AS avg_novelty
            FROM signals
            WHERE scraped_at >= $1
            GROUP BY platform
        """, now - timedelta(hours=24))

        await conn.close()
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
        conn = await asyncpg.connect(cfg.pg_dsn_str, timeout=3.0)
        if platform and platform != "all":
            rows = await conn.fetch("""
                SELECT signal_id, platform, title, url, scraped_at,
                       sentiment, commercial_intent, novelty,
                       views, likes, comments, shares
                FROM signals WHERE platform = $1
                ORDER BY scraped_at DESC LIMIT $2
            """, platform, limit)
        else:
            rows = await conn.fetch("""
                SELECT signal_id, platform, title, url, scraped_at,
                       sentiment, commercial_intent, novelty,
                       views, likes, comments, shares
                FROM signals
                ORDER BY scraped_at DESC LIMIT $1
            """, limit)
        await conn.close()
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
    cfg = settings()
    try:
        r = aioredis.from_url(cfg.redis_url_str, decode_responses=True, socket_timeout=3.0)
        entries = await r.xrevrange("aegis:phase2:graph_results", count=limit)
        await r.aclose()
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
        except Exception:
            pass
    return results


# ---------------------------------------------------------------------------
# /api/swarm/latest  — latest SwarmResult from Redis
# ---------------------------------------------------------------------------
@app.get("/api/swarm/latest")
async def swarm_latest() -> dict[str, Any]:
    cfg = settings()
    try:
        r = aioredis.from_url(cfg.redis_url_str, decode_responses=True, socket_timeout=3.0)
        raw = await r.get("aegis:swarm:latest")
        await r.aclose()
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
        conn = await asyncpg.connect(cfg.pg_dsn_str, timeout=3.0)
        now = datetime.now(UTC)
        rows = await conn.fetch("""
            SELECT run_id, started_at, finished_at, total_signals, unique_signals,
                   dedup_removed, market_pulse, batch_confidence, conclusion
            FROM swarm_results
            WHERE started_at >= $1
            ORDER BY started_at DESC
            LIMIT $2
        """, now - timedelta(hours=24), limit)
        await conn.close()
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
    cfg = settings()
    try:
        r = aioredis.from_url(cfg.redis_url_str, decode_responses=True, socket_timeout=3.0)
        raw = await r.hgetall("aegis:swarm:agent_health")
        await r.aclose()
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
        conn = await asyncpg.connect(cfg.pg_dsn_str, timeout=3.0)
        now = datetime.now(UTC)
        rows = await conn.fetch("""
            SELECT platform,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE scraped_at >= $2) AS last_24h,
                   AVG(sentiment) AS avg_sentiment,
                   AVG(commercial_intent) AS avg_commercial_intent
            FROM signals
            WHERE scraped_at >= $1
            GROUP BY platform
            ORDER BY last_24h DESC
        """, now - timedelta(hours=24), now - timedelta(hours=24))
        await conn.close()
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
        conn = await asyncpg.connect(cfg.pg_dsn_str, timeout=3.0)
        now = datetime.now(UTC)
        rows = await conn.fetch("""
            SELECT platform,
                   DATE_TRUNC('hour', scraped_at) AS hr,
                   COUNT(*) AS n
            FROM signals
            WHERE scraped_at >= $1
            GROUP BY platform, hr
            ORDER BY hr ASC
        """, now - timedelta(days=7))
        await conn.close()
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


@app.post("/api/ops/run")
async def run_op(op: RunOp) -> StreamingResponse:
    if not op.cmd or op.cmd[0] not in _ALLOWED_ROOTS:
        raise HTTPException(status_code=400, detail=f"Root command must be one of: {_ALLOWED_ROOTS}")

    cmd = op.cmd
    if cmd[0] == "aegis":
        # Invoke via `uv run aegis …` so the venv is guaranteed
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
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield line
            await proc.wait()
            yield f"\n[Process exited with code {proc.returncode}]\n".encode()
        except Exception as exc:
            yield f"\n[Error launching process: {exc}]\n".encode()

    return StreamingResponse(_stream(), media_type="text/plain")


# ---------------------------------------------------------------------------
# /stream/events  — SSE feed (new signals, new verdicts, system ticks)
# ---------------------------------------------------------------------------
@app.get("/stream/events")
async def sse_events(request: Request) -> StreamingResponse:
    cfg = settings()

    async def _generate() -> AsyncIterator[bytes]:
        last_id = "$"
        tick = 0
        # One persistent connection per SSE client — O(1) lifetime cost.
        # Eliminates the prior O(2/s) reconnect pattern (was: from_url+aclose per tick).
        _r = aioredis.from_url(cfg.redis_url_str, decode_responses=True)
        try:
            while not await request.is_disconnected():
                tick += 1

                # System health tick every 20 iterations
                if tick % 20 == 1:
                    try:
                        conn = await asyncpg.connect(cfg.pg_dsn_str, timeout=2)
                        cnt = await conn.fetchval("SELECT COUNT(*) FROM signals")
                        await conn.close()
                        payload = json.dumps({
                            "type": "tick",
                            "signal_count": cnt,
                            "ts": datetime.now(UTC).isoformat(),
                        })
                        yield f"data: {payload}\n\n".encode()
                    except Exception as exc:
                        _sse_log.warning("sse.health_tick_failed", error=str(exc))

                # XREAD blocks for up to 1500ms — yields to the event loop
                # during the wait, so no asyncio.sleep() needed.
                try:
                    entries = await _r.xread(
                        {"aegis:phase2:graph_results": last_id}, count=5, block=1500
                    )
                    if entries:
                        for _, msgs in entries:
                            for mid, data in msgs:
                                last_id = mid
                                try:
                                    raw = data.get("body") or data.get("payload") or "{}"
                                    p = json.loads(raw)
                                    evt = json.dumps({
                                        "type": "analysis_complete",
                                        "trend_id": p.get("trend_id"),
                                        "verdict": p.get("final_verdict"),
                                        "score": p.get("final_score"),
                                        "confidence": p.get("final_confidence"),
                                        "halt_reason": p.get("halt_reason"),
                                        "ts": datetime.now(UTC).isoformat(),
                                    })
                                    yield f"data: {evt}\n\n".encode()
                                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                                    _sse_log.warning(
                                        "sse.entry_decode_failed",
                                        entry_id=mid,
                                        error=str(exc),
                                    )
                except Exception as exc:
                    _sse_log.warning("sse.xread_failed", error=str(exc))
        finally:
            await _r.aclose()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
