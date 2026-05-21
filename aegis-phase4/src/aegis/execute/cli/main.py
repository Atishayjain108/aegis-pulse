"""`aegis-execute` command-line interface.

Subcommands:
  serve         Run the FastAPI app (HTTP + SSE).
  drain         Run the outbox drainer.
  intake        Run the Redis Streams intake worker (Phase 2/3 fan-in).
  tail          Print recent alerts from the DB.
  killswitch    Inspect or toggle the kill switch.
  compose-demo  One-shot synthetic alert composition (no DB / Redis needed).
  version       Print the package version.

Every subcommand is fail-loud, recover-silent: errors print a one-line
diagnostic prefixed with the AEGIS-EXEC code and exit non-zero.

Output discipline: this CLI sends all structured logs to STDERR and only
machine-readable JSON / human-readable plain values to STDOUT, so
`aegis-execute compose-demo | jq` works cleanly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
import typer

from aegis.execute import __version__
from aegis.execute.bridge.types import ComposerInput
from aegis.execute.errors import AegisExecuteError


def _configure_logging_to_stderr() -> None:
    """Route structlog output to stderr so stdout stays parseable."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


_configure_logging_to_stderr()


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="AEGIS Pulse — Execute (Phase 4) CLI.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _emit_error(exc: AegisExecuteError) -> None:
    typer.echo(f"[{exc.spec.code}] {exc.spec.message}", err=True)
    if exc.context:
        for k, v in exc.context.items():
            typer.echo(f"  {k}: {v}", err=True)


async def _maybe_redis(url: str | None) -> Any:
    if not url:
        return None
    try:
        # redis-py 5 has an async client under redis.asyncio
        import redis.asyncio as redis  # type: ignore[import-not-found]

        return redis.from_url(url, decode_responses=False)
    except ImportError:
        typer.echo("redis-py not installed; running without Redis.", err=True)
        return None


async def _maybe_pool(dsn: str | None) -> Any:
    if not dsn:
        return None
    try:
        import asyncpg  # type: ignore[import-not-found]

        return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    except ImportError:
        typer.echo("asyncpg not installed; running without Postgres pool.", err=True)
        return None


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------
@app.command("version")
def version_cmd() -> None:
    """Print the package version."""
    typer.echo(__version__)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------
@app.command("serve")
def serve_cmd(
    host: Annotated[str, typer.Option(help="Bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Bind port")] = 8200,
    pg_dsn: Annotated[str | None, typer.Option("--pg-dsn", help="Postgres DSN")] = None,
    redis_url: Annotated[str | None, typer.Option("--redis-url", help="Redis URL")] = None,
) -> None:
    """Run the HTTP API + SSE server."""
    try:
        import uvicorn  # type: ignore[import-not-found]
    except ImportError:
        typer.echo("uvicorn not installed. Install with: pip install uvicorn[standard]", err=True)
        raise typer.Exit(code=2) from None

    async def _setup_and_run() -> None:
        from aegis.execute.api import build_app

        pool = await _maybe_pool(pg_dsn)
        redis_client = await _maybe_redis(redis_url)
        a = build_app(pool=pool, redis_client=redis_client)
        cfg = uvicorn.Config(a, host=host, port=port, log_level="info", access_log=False)
        srv = uvicorn.Server(cfg)
        try:
            await srv.serve()
        finally:
            if pool is not None:
                await pool.close()
            if redis_client is not None:
                with contextlib.suppress(Exception):
                    await redis_client.close()

    asyncio.run(_setup_and_run())


# ---------------------------------------------------------------------------
# drain
# ---------------------------------------------------------------------------
@app.command("drain")
def drain_cmd(
    tenant: Annotated[str, typer.Option(help="Tenant UUID")],
    pg_dsn: Annotated[str | None, typer.Option("--pg-dsn")] = None,
    redis_url: Annotated[str | None, typer.Option("--redis-url")] = None,
) -> None:
    """Run the outbox drainer until interrupted."""
    from aegis.execute.workers.drain_worker import run_drain_worker

    async def _go() -> None:
        pool = await _maybe_pool(pg_dsn)
        redis_client = await _maybe_redis(redis_url)
        try:
            await run_drain_worker(
                tenant_id=str(UUID(tenant)),
                pool=pool,
                redis_client=redis_client,
            )
        finally:
            if pool is not None:
                await pool.close()
            if redis_client is not None:
                with contextlib.suppress(Exception):
                    await redis_client.close()

    try:
        asyncio.run(_go())
    except KeyboardInterrupt:
        typer.echo("\ninterrupted; bye.", err=True)


# ---------------------------------------------------------------------------
# tail
# ---------------------------------------------------------------------------
@app.command("tail")
def tail_cmd(
    tenant: Annotated[str, typer.Option(help="Tenant UUID")],
    limit: Annotated[int, typer.Option(help="Max rows")] = 20,
    pg_dsn: Annotated[str | None, typer.Option("--pg-dsn")] = None,
) -> None:
    """Print recent alerts for a tenant (most recent first)."""
    from aegis.execute.store.repository import AlertRepository

    async def _go() -> None:
        pool = await _maybe_pool(pg_dsn)
        if pool is None:
            typer.echo("no Postgres pool — cannot tail.", err=True)
            raise typer.Exit(code=2)
        try:
            repo = AlertRepository(pool=pool)
            alerts = await repo.list_recent_alerts(tenant_id=str(UUID(tenant)), limit=limit)
            for a in alerts:
                typer.echo(
                    f"{a.created_at.isoformat()}  P{a.priority}  {a.verdict:8s}  "
                    f"{a.trend_id:32s}  score={a.score:.2f} conf={a.confidence:.2f}"
                )
        finally:
            await pool.close()

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# killswitch
# ---------------------------------------------------------------------------
killswitch_app = typer.Typer(help="Inspect or toggle the kill switch.")
app.add_typer(killswitch_app, name="killswitch")


@killswitch_app.command("state")
def ks_state(
    redis_url: Annotated[str, typer.Option("--redis-url")] = "redis://127.0.0.1:6379/0",
) -> None:
    """Print the current kill-switch state."""
    from aegis.execute.killswitch.switch import KillSwitch

    async def _go() -> None:
        rc = await _maybe_redis(redis_url)
        ks = KillSwitch(redis_client=rc)
        typer.echo(await ks.state())
        if rc is not None:
            with contextlib.suppress(Exception):
                await rc.close()

    asyncio.run(_go())


@killswitch_app.command("trip")
def ks_trip(
    reason: Annotated[str, typer.Option("--reason")] = "manual",
    redis_url: Annotated[str, typer.Option("--redis-url")] = "redis://127.0.0.1:6379/0",
) -> None:
    """Trip the kill switch (halt dispatch)."""
    from aegis.execute.killswitch.switch import KillSwitch

    async def _go() -> None:
        rc = await _maybe_redis(redis_url)
        if rc is None:
            typer.echo("Redis required.", err=True)
            raise typer.Exit(code=2)
        ks = KillSwitch(redis_client=rc)
        try:
            await ks.trip(reason=reason)
            typer.echo("TRIPPED")
        except AegisExecuteError as exc:
            _emit_error(exc)
            raise typer.Exit(code=1) from exc
        finally:
            with contextlib.suppress(Exception):
                await rc.close()

    asyncio.run(_go())


@killswitch_app.command("arm")
def ks_arm(
    reason: Annotated[str, typer.Option("--reason")] = "manual",
    redis_url: Annotated[str, typer.Option("--redis-url")] = "redis://127.0.0.1:6379/0",
) -> None:
    """Arm the kill switch (resume dispatch)."""
    from aegis.execute.killswitch.switch import KillSwitch

    async def _go() -> None:
        rc = await _maybe_redis(redis_url)
        if rc is None:
            typer.echo("Redis required.", err=True)
            raise typer.Exit(code=2)
        ks = KillSwitch(redis_client=rc)
        try:
            await ks.arm(reason=reason)
            typer.echo("ARMED")
        except AegisExecuteError as exc:
            _emit_error(exc)
            raise typer.Exit(code=1) from exc
        finally:
            with contextlib.suppress(Exception):
                await rc.close()

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# compose-demo  (no DB / Redis)
# ---------------------------------------------------------------------------
@app.command("compose-demo")
def compose_demo(
    trend_id: Annotated[str, typer.Option()] = "demo-trend-1",
    verdict: Annotated[str, typer.Option()] = "ENTER",
    score: Annotated[float, typer.Option()] = 0.78,
    confidence: Annotated[float, typer.Option()] = 0.72,
    p_breakout: Annotated[float, typer.Option("--p-breakout")] = 0.85,
    p_decline: Annotated[float, typer.Option("--p-decline")] = 0.10,
    margin: Annotated[float, typer.Option()] = 3.50,
    loss_prob: Annotated[float, typer.Option("--loss-prob")] = 0.15,
) -> None:
    """Run a single composition end-to-end with NO infrastructure.

    Useful as a smoke test of the policy chain.
    """
    from aegis.execute.policy.composer import compose

    tenant_id = uuid4()
    ci = ComposerInput(
        tenant_id=tenant_id,
        trend_id=trend_id,
        phase2_verdict=verdict,
        phase2_score=score,
        phase2_confidence=confidence,
        phase2_priority=1,
        phase3_p_breakout_24h=p_breakout,
        phase3_p_decline_6h=p_decline,
        phase3_expected_margin_usd=margin,
        phase3_loss_probability=loss_prob,
        phase3_confidence=confidence,
        phase3_policy_action=verdict.lower(),
    )
    try:
        alert = compose(ci)
    except AegisExecuteError as exc:
        _emit_error(exc)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(alert.model_dump(mode="json"), indent=2, default=str))


def main() -> None:
    """Entry point for the `aegis-execute` console script."""
    try:
        app()
    except AegisExecuteError as exc:
        _emit_error(exc)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["app", "main"]
