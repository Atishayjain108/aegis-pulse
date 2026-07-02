"""
aegis.cli.main
==============

Top-level ``aegis`` Click application.

Subcommands
-----------

* ``aegis up``                — bring up the docker-compose stack
* ``aegis down``              — tear it down (volumes preserved)
* ``aegis status``            — print container health
* ``aegis tail``              — follow alert/log stream
* ``aegis scrape``            — run a single source adapter to completion
* ``aegis signals tail``      — follow newest rows in the signals table
* ``aegis report daily``      — print yesterday's 1-page summary
* ``aegis reset``             — danger: wipe local data volumes
* ``aegis support-bundle``    — collect a sanitised diagnostic zip
* ``aegis doctor``            — run the bootstrap health check
* ``aegis migrate``           — alembic upgrade head
* ``aegis topic``             — one-word full-cycle: expand → scrape all sources → dedup → analyze

Every command exits with a non-zero code on failure and prints a
machine-readable error code (``AEGIS-CLI-NNNN``) for documentation
lookups under ``docs/errors/``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import click

from aegis import __version__
from aegis.config import settings

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _repo_root() -> Path:
    """Best-effort discovery of the repo root (where docker-compose lives)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docker-compose.yml").exists():
            return parent
    # Fall back to CWD; better than crashing.
    return Path.cwd()


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> int:
    """Run a subprocess and stream its output, returning the exit code."""
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    click.echo(f"$ {' '.join(cmd)}", err=True)
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=proc_env, check=False)
    if check and result.returncode != 0:
        click.echo(
            f"AEGIS-CLI-0001: command exited with {result.returncode}",
            err=True,
        )
        sys.exit(result.returncode)
    return result.returncode


def _docker_compose() -> list[str]:
    """Return the right docker-compose invocation for the host.

    Modern Docker uses the ``docker compose`` plugin; older boxes still
    ship the standalone ``docker-compose`` binary.
    """
    if shutil.which("docker"):
        # Probe the plugin once.
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    click.echo(
        "AEGIS-CLI-0002: neither `docker compose` nor `docker-compose` found "
        "on PATH; install Docker Desktop with the WSL2 backend enabled.",
        err=True,
    )
    sys.exit(2)


def _docker_ps_via_socket() -> list[dict[str, Any]] | None:
    """Query Docker Engine API via Unix socket — no docker binary needed.

    Works inside containers where the docker CLI is absent but
    /var/run/docker.sock is mounted.  Returns None when the socket is
    unreachable so callers can fall back to the CLI path.
    """
    import http.client
    import socket as _socket

    sock_path = Path("/var/run/docker.sock")
    if not sock_path.exists():
        return None

    class _UnixConn(http.client.HTTPConnection):
        def connect(self) -> None:
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.connect(str(sock_path))
            self.sock = s  # type: ignore[assignment]

    try:
        conn = _UnixConn("localhost")
        conn.request("GET", "/containers/json?all=true")
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        return json.loads(resp.read())  # type: ignore[return-value]
    except Exception:
        return None


# ---------------------------------------------------------------------
# Top-level group
# ---------------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="aegis")
def main() -> None:
    """AEGIS Pulse — autonomous market arbitrage intelligence engine."""


# Register dashboard subgroup (import deferred to keep startup fast)
try:
    from aegis.dashboard.cli import dashboard as _dashboard_group

    main.add_command(_dashboard_group, name="dashboard")
except ImportError:
    pass


@main.group(name="api")
def _api_group() -> None:
    """Unified REST API (geo / compliance / evolve / datalake)."""


@main.group(name="autonomous")
def _autonomous_group() -> None:
    """Autonomous self-driving scheduler (scrape → analyze → drift → retrain)."""


@_autonomous_group.command(name="run")
def _autonomous_run() -> None:
    """Run the autonomous scheduler loop in the foreground (ORPH-2)."""
    import asyncio as _asyncio

    from aegis.scheduler.autonomous import main as _sched_main

    _asyncio.run(_sched_main())


@main.group(name="realtime")
def _realtime_group() -> None:
    """Event-driven Phase 0 → Phase 2/3 stream consumer (CONN-4 / BRAIN-5)."""


@_realtime_group.command(name="run")
@click.option("--use-llm/--no-llm", default=False, show_default=True)
@click.option("--timeout-s", default=60.0, show_default=True, type=float)
def _realtime_run(use_llm: bool, timeout_s: float) -> None:
    """Consume aegis:phase0:raw_signals via XREADGROUP and dispatch run_trend.

    Push-based replacement for DB polling: each scrape batch emitted by
    ``scrape_topic(stream_client=...)`` is dispatched through the full Phase 2
    LangGraph + Phase 3 pipeline the moment it lands, with at-least-once
    delivery (consumer-group PEL).
    """
    import asyncio as _asyncio

    import redis.asyncio as aioredis

    from aegis.scrape.realtime_consumer import SignalStreamConsumer
    from aegis.scrape.stream_bridge import ensure_consumer_group

    cfg = settings()

    async def _run() -> None:
        client = aioredis.from_url(cfg.redis_url_str, decode_responses=True)
        await ensure_consumer_group(client)
        consumer = SignalStreamConsumer(
            redis_client=client,
            redis_client_p4=client,
            use_llm=use_llm,
            timeout_s=timeout_s,
        )
        click.echo("AEGIS realtime consumer started — Ctrl-C to stop.")
        try:
            await consumer.run()
        finally:
            await client.aclose()

    try:
        _asyncio.run(_run())
    except KeyboardInterrupt:
        click.echo("\nrealtime consumer stopped.")


@_api_group.command(name="serve")
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8400, show_default=True, type=int)
def _api_serve(host: str, port: int) -> None:
    """Serve the unified AEGIS API (ORPH-1)."""
    import uvicorn

    from aegis.api.main import create_app, mounted_routers

    app = create_app()
    click.echo(f"AEGIS unified API — mounted phases: {', '.join(mounted_routers()) or 'none'}")
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------
# Stack lifecycle
# ---------------------------------------------------------------------


@main.command()
@click.option(
    "--detach/--no-detach",
    default=True,
    help="Run containers in the background (default).",
)
@click.option(
    "--build",
    is_flag=True,
    default=False,
    help="Rebuild local images before starting.",
)
def up(detach: bool, build: bool) -> None:
    """Bring up the docker-compose stack."""
    cmd = [*_docker_compose(), "up"]
    if detach:
        cmd.append("-d")
    if build:
        cmd.append("--build")
    _run(cmd, cwd=_repo_root())
    click.echo(
        "Stack starting. Run `aegis status` in 30s to see health.",
        err=True,
    )


@main.command()
@click.option(
    "--volumes",
    is_flag=True,
    default=False,
    help="Also remove named volumes (DESTROYS LOCAL DATA).",
)
def down(volumes: bool) -> None:
    """Tear down the docker-compose stack."""
    cmd = [*_docker_compose(), "down"]
    if volumes:
        if not click.confirm(
            "This will DELETE all local Postgres, Redis, and MinIO data. " "Continue?",
            default=False,
        ):
            click.echo("Aborted.", err=True)
            return
        cmd.append("--volumes")
    _run(cmd, cwd=_repo_root())


@main.command()
def status() -> None:
    """Show the health status of every service."""
    # Try the Docker socket API first — works inside containers where the
    # docker binary is absent (e.g. the dashboard ops console).
    containers = _docker_ps_via_socket()
    if containers is not None:
        if not containers:
            click.echo("No containers running. Try `aegis up`.", err=True)
            return
        click.echo(f"{'NAME':<32} {'STATE':<12} STATUS")
        for c in containers:
            name = c.get("Names", ["?"])[0].lstrip("/")
            state = c.get("State", "?")
            status_str = c.get("Status", "?")
            click.echo(f"{name:<32} {state:<12} {status_str}")
        return

    # Fall back to `docker compose ps` on the host (socket unavailable).
    cmd = [*_docker_compose(), "ps", "--format", "json"]
    result = subprocess.run(cmd, cwd=_repo_root(), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        click.echo(
            f"AEGIS-CLI-0003: docker compose ps failed: {result.stderr}",
            err=True,
        )
        sys.exit(result.returncode)

    # Newer compose emits one JSON document per line.
    rows: list[dict[str, Any]] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            try:
                rows.extend(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not rows:
        click.echo("No services running. Try `aegis up`.", err=True)
        return

    click.echo(f"{'SERVICE':<24} {'STATE':<14} {'HEALTH':<14} PORTS")
    for r in rows:
        name = r.get("Service") or r.get("Name") or "?"
        state = r.get("State", "?")
        health = r.get("Health", "n/a") or "n/a"
        ports = r.get("Publishers") or []
        if isinstance(ports, list):
            ports_str = ",".join(
                f"{p.get('PublishedPort', '?')}->{p.get('TargetPort', '?')}"
                for p in ports
                if isinstance(p, dict)
            )
        else:
            ports_str = str(ports)
        click.echo(f"{name:<24} {state:<14} {health:<14} {ports_str}")


# ---------------------------------------------------------------------
# Logs / tails
# ---------------------------------------------------------------------


@main.command()
@click.argument("service", required=False)
def tail(service: str | None) -> None:
    """Tail logs for SERVICE (default: all)."""
    cmd = [*_docker_compose(), "logs", "-f", "--tail", "200"]
    if service:
        cmd.append(service)
    _run(cmd, cwd=_repo_root(), check=False)


# ---------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------


@main.command()
def migrate() -> None:
    """Run all outstanding database migrations."""
    _run(["uv", "run", "alembic", "upgrade", "head"], cwd=_repo_root())


# ---------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------

# The registry maps the friendly --source name to the adapter import path
# and the canonical AdapterConfig name. New adapters land here.
_ADAPTER_REGISTRY: dict[str, tuple[str, str]] = {
    # --- API-based (credentials required) ---
    "reddit": ("aegis.scrape.sources.reddit", "RedditAdapter"),
    "youtube": ("aegis.scrape.sources.youtube", "YouTubeAdapter"),
    "instagram": ("aegis.scrape.sources.instagram", "InstagramAdapter"),
    # --- No API key required (original) ---
    "tiktok": ("aegis.scrape.sources.tiktok", "TikTokAdapter"),
    "pinterest": ("aegis.scrape.sources.pinterest", "PinterestAdapter"),
    "amazon": ("aegis.scrape.sources.amazon", "AmazonAdapter"),
    "google-trends": ("aegis.scrape.sources.google_trends", "GoogleTrendsAdapter"),
    "google-news": ("aegis.scrape.sources.google_news_rss", "GoogleNewsRSSAdapter"),
    "bing-news": ("aegis.scrape.sources.bing_news_rss", "BingNewsRSSAdapter"),
    "hacker-news": ("aegis.scrape.sources.hacker_news", "HackerNewsAdapter"),
    "nitter": ("aegis.scrape.sources.nitter", "NitterAdapter"),
    "github-trending": ("aegis.scrape.sources.github_trending", "GitHubTrendingAdapter"),
    "reddit-rss": ("aegis.scrape.sources.reddit_rss", "RedditRSSAdapter"),
    # --- Phase 2+ swarm adapters ---
    "reddit-finance": ("aegis.scrape.sources.reddit_finance", "RedditFinanceAdapter"),
    "reddit-ecommerce": ("aegis.scrape.sources.reddit_ecommerce", "RedditEcommerceAdapter"),
    "devto": ("aegis.scrape.sources.devto", "DevToAdapter"),
    "gdelt": ("aegis.scrape.sources.gdelt", "GdeltAdapter"),
    "wikimedia": ("aegis.scrape.sources.wikimedia", "WikimediaAdapter"),
    "google-trends-global": ("aegis.scrape.sources.google_trends_global", "GoogleTrendsGlobalAdapter"),
    "producthunt": ("aegis.scrape.sources.producthunt", "ProductHuntAdapter"),
    "google-trends-india": ("aegis.scrape.sources.google_trends_india", "GoogleTrendsIndiaAdapter"),
    "youtube-rss": ("aegis.scrape.sources.youtube_rss", "YouTubeRSSAdapter"),
    "medium": ("aegis.scrape.sources.medium_rss", "MediumRSSAdapter"),
    "techcrunch": ("aegis.scrape.sources.techcrunch_rss", "TechCrunchRSSAdapter"),
    "wired": ("aegis.scrape.sources.wired_rss", "WiredRSSAdapter"),
    "bbc-news": ("aegis.scrape.sources.bbc_business", "BBCBusinessAdapter"),
    "reuters": ("aegis.scrape.sources.reuters_rss", "ReutersRSSAdapter"),
    "ndtv-profit": ("aegis.scrape.sources.ndtv_profit", "NDTVProfitAdapter"),
    "mint": ("aegis.scrape.sources.mint_rss", "MintRSSAdapter"),
    "business-standard": ("aegis.scrape.sources.business_standard_rss", "BusinessStandardRSSAdapter"),
    "economic-times": ("aegis.scrape.sources.economic_times_markets", "EconomicTimesMarketsAdapter"),
    "moneycontrol": ("aegis.scrape.sources.moneycontrol", "MoneycontrolAdapter"),
    "yahoo-finance": ("aegis.scrape.sources.yahoo_finance_rss", "YahooFinanceRSSAdapter"),
    "investing-com": ("aegis.scrape.sources.investing_com_rss", "InvestingComRSSAdapter"),
    "ebay": ("aegis.scrape.sources.ebay_browse", "EbayBrowseAdapter"),
    "bestbuy": ("aegis.scrape.sources.bestbuy", "BestBuyAdapter"),
    "etsy": ("aegis.scrape.sources.etsy", "EtsyAdapter"),
    "flipkart": ("aegis.scrape.sources.flipkart", "FlipkartAdapter"),
    "myntra": ("aegis.scrape.sources.myntra", "MyntraAdapter"),
    # nykaa/ajio/meesho/indiamart removed 2026-06-24 — WAF-gated, no free path.
    "snapdeal": ("aegis.scrape.sources.snapdeal", "SnapdealAdapter"),
    "amazon-in": ("aegis.scrape.sources.amazon_in", "AmazonINAdapter"),
    "nse-bse": ("aegis.scrape.sources.nse_bse", "NSEBSEAdapter"),
    "screener-in": ("aegis.scrape.sources.screener_in", "ScreenerInAdapter"),
    "github-public": ("aegis.scrape.sources.github_public", "GitHubPublicAdapter"),
    "npm-trends": ("aegis.scrape.sources.npm_trends", "NPMTrendsAdapter"),
}


# ADP-1: permanently-broken adapters (external service dead / auth-walled).
# They never return signals and are excluded from the default swarm waves. They
# remain reachable via `aegis scrape --source <name> --include-experimental` for
# manual debugging, but are quarantined by default so a user does not silently
# pick a dead source.
_QUARANTINED_ADAPTERS: frozenset[str] = frozenset({"tiktok", "pinterest", "nitter"})


def _load_adapter_class(name: str, *, include_experimental: bool = False) -> Any:
    if name not in _ADAPTER_REGISTRY:
        valid = ", ".join(sorted(_ADAPTER_REGISTRY))
        raise click.UsageError(f"Unknown source {name!r}. Valid: {valid}")
    if name in _QUARANTINED_ADAPTERS and not include_experimental:
        raise click.UsageError(
            f"Source {name!r} is quarantined (external service dead/auth-walled and "
            f"returns no signals). Pass --include-experimental to run it anyway."
        )
    module_path, class_name = _ADAPTER_REGISTRY[name]
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)


@main.command()
@click.option("--source", required=True, help="Adapter name (e.g. reddit, tiktok).")
@click.option(
    "--limit",
    type=int,
    default=50,
    show_default=True,
    help="Max signals to emit before stopping.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Run the adapter but do not persist to Postgres.",
)
@click.option(
    "--subreddit",
    default=None,
    help="(Reddit only) target subreddit.",
)
@click.option(
    "--query",
    default=None,
    help="(YouTube/HN/Pinterest/Google Trends) search query.",
)
@click.option(
    "--hashtag",
    default=None,
    help="(Instagram/TikTok) target hashtag.",
)
@click.option(
    "--country",
    default=None,
    help="(TikTok/Google Trends) 2-letter country code, e.g. US.",
)
@click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Suppress verbose logs; only show signal count.",
)
@click.option(
    "--include-experimental",
    is_flag=True,
    default=False,
    help="Allow quarantined/broken adapters (tiktok, pinterest, nitter).",
)
def scrape(
    source: str,
    limit: int,
    dry_run: bool,
    subreddit: str | None,
    query: str | None,
    hashtag: str | None,
    country: str | None,
    quiet: bool,
    include_experimental: bool,
) -> None:
    """Run a single source adapter end-to-end."""
    asyncio.run(
        _scrape_async(
            source=source,
            limit=limit,
            dry_run=dry_run,
            subreddit=subreddit,
            query=query,
            hashtag=hashtag,
            country=country,
            quiet=quiet,
            include_experimental=include_experimental,
        )
    )


def _build_adapter(source: str, adapter_cls: Any, *, limit: int) -> Any:
    """Construct the right adapter + config for *source* using settings()."""
    from uuid import UUID as _UUID  # noqa: F401

    cfg = settings()

    if source == "reddit":
        from aegis.scrape.sources.reddit import RedditConfig

        r = cfg.reddit
        if r.client_id is None or r.client_secret is None:
            raise click.UsageError(
                "Reddit adapter requires AEGIS_REDDIT_CLIENT_ID and "
                "AEGIS_REDDIT_CLIENT_SECRET.\n"
                "Register a script app at https://www.reddit.com/prefs/apps "
                "then set them in .env."
            )
        return adapter_cls(
            RedditConfig(
                client_id=r.client_id.get_secret_value(),
                client_secret=r.client_secret.get_secret_value(),
                user_agent=r.user_agent,
                max_submissions=limit,
            )
        )

    if source == "youtube":
        from aegis.scrape.sources.youtube import YouTubeConfig

        yt = cfg.youtube
        if yt.api_key is None:
            raise click.UsageError(
                "YouTube adapter requires AEGIS_YOUTUBE_API_KEY.\n"
                "Create one at https://console.cloud.google.com/ and set it in .env."
            )
        return adapter_cls(
            YouTubeConfig(
                api_key=yt.api_key.get_secret_value(),
                max_signals=min(50, limit),
            )
        )

    if source == "hacker-news":
        from aegis.scrape.sources.hacker_news import HackerNewsConfig

        return adapter_cls(HackerNewsConfig())

    if source == "google-trends":
        from aegis.scrape.sources.google_trends import GoogleTrendsConfig

        return adapter_cls(GoogleTrendsConfig())

    if source == "tiktok":
        from aegis.scrape.sources.tiktok import TikTokConfig

        return adapter_cls(TikTokConfig())

    if source == "instagram":
        from aegis.scrape.sources.instagram import InstagramConfig

        return adapter_cls(
            InstagramConfig(allow_red_tos=True)  # user opted in via CLI
        )

    if source == "pinterest":
        from aegis.scrape.sources.pinterest import PinterestConfig

        return adapter_cls(PinterestConfig())

    if source == "amazon":
        from aegis.scrape.sources.amazon import AmazonConfig

        return adapter_cls(AmazonConfig())

    if source == "nitter":
        from aegis.scrape.sources.nitter import NitterConfig

        return adapter_cls(NitterConfig())

    if source == "github-trending":
        from aegis.scrape.sources.github_trending import GitHubTrendingConfig

        return adapter_cls(GitHubTrendingConfig())

    if source == "reddit-rss":
        from aegis.scrape.sources.reddit_rss import RedditRSSConfig

        # subreddit is forwarded dynamically via run_params → fetch_raw
        return adapter_cls(RedditRSSConfig())

    if source == "google-news":
        from aegis.scrape.sources.google_news_rss import GoogleNewsRSSConfig

        return adapter_cls(GoogleNewsRSSConfig())

    if source == "bing-news":
        from aegis.scrape.sources.bing_news_rss import BingNewsRSSConfig

        return adapter_cls(BingNewsRSSConfig())

    # Fallback for future adapters
    from aegis.scrape.base import AdapterConfig

    return adapter_cls(AdapterConfig(name=source, max_signals=limit))


async def _scrape_async(
    *,
    source: str,
    limit: int,
    dry_run: bool,
    subreddit: str | None,
    query: str | None,
    hashtag: str | None,
    country: str | None,
    quiet: bool = False,
    include_experimental: bool = False,
) -> int:
    import uuid

    from aegis.cache.redis_cache import RedisCache
    from aegis.core.logging import configure_logging
    from aegis.db.pool import PgPool
    from aegis.db.signals import insert_signals

    configure_logging(level="ERROR" if quiet else "INFO")
    cfg = settings()
    tenant_uuid = uuid.UUID(cfg.default_tenant_id)

    # Build run params that get forwarded to adapter.run() → fetch_raw()
    run_params: dict[str, Any] = {"limit": limit}
    if subreddit:
        run_params["subreddit"] = subreddit
    if query:
        run_params["query"] = query
    if hashtag:
        run_params["hashtag"] = hashtag
    if country:
        run_params["country_code"] = country

    adapter_cls = _load_adapter_class(source, include_experimental=include_experimental)
    adapter = _build_adapter(source, adapter_cls, limit=limit)

    pool: PgPool | None = None
    cache: RedisCache | None = None

    try:
        if not dry_run:
            pool = PgPool(dsn=cfg.pg_dsn_str)
            await pool.start()
            cache = RedisCache(url=cfg.redis_url_str, namespace=cfg.redis_namespace)
            await cache.start()

        batch: list[Any] = []
        emitted = 0

        async for signal in adapter.run(**run_params):
            batch.append(signal)
            emitted += 1

            if dry_run:
                click.echo(
                    json.dumps(
                        {
                            "platform": signal.platform.value,
                            "external_id": signal.external_id,
                            "title": (signal.title or "")[:80],
                            "url": str(signal.url) if signal.url else None,
                        }
                    )
                )
            elif len(batch) >= 50 and pool is not None:
                inserted = await insert_signals(pool, batch, tenant_id=tenant_uuid)
                if not quiet:
                    click.echo(f"… inserted {inserted} (total emitted: {emitted})", err=True)
                batch.clear()

            if emitted >= limit:
                break

        if not dry_run and batch and pool is not None:
            inserted = await insert_signals(pool, batch, tenant_id=tenant_uuid)
            if not quiet:
                click.echo(f"flushed {inserted} (total emitted: {emitted})", err=True)

        if not quiet:
            click.echo(f"Done. Emitted {emitted} signals.", err=True)
    finally:
        if cache is not None:
            await cache.close()
        if pool is not None:
            await pool.close()
    return emitted


# ---------------------------------------------------------------------
# Signals subgroup
# ---------------------------------------------------------------------


@main.group()
def signals() -> None:
    """Inspect the signals table."""


@signals.command("tail")
@click.option(
    "--limit",
    type=int,
    default=20,
    show_default=True,
    help="Rows to show.",
)
@click.option(
    "--platform",
    default=None,
    help="Filter by platform (e.g. reddit, tiktok).",
)
def signals_tail(limit: int, platform: str | None) -> None:
    """Show the most recent signals."""
    asyncio.run(_signals_tail(limit=limit, platform=platform))


async def _signals_tail(*, limit: int, platform: str | None) -> None:
    import uuid as _uuid

    from aegis.db.pool import PgPool
    from aegis.db.signals import fetch_recent_signals

    cfg = settings()
    pool = PgPool(dsn=cfg.pg_dsn_str)
    await pool.start()
    try:
        tenant_uuid = _uuid.UUID(cfg.default_tenant_id)
        rows = await fetch_recent_signals(
            pool, tenant_id=tenant_uuid, limit=limit, platform=platform
        )
        if not rows:
            click.echo("(no signals)", err=True)
            return
        for row in rows:
            ts = row["captured_at"].isoformat() if row.get("captured_at") else "?"
            title = (row.get("title") or "")[:60]
            click.echo(f"{ts}  {row['platform']:<12} {title}")
    finally:
        await pool.close()


# ---------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------


@main.group()
def report() -> None:
    """Generate reports."""


@report.command("daily")
@click.option(
    "--date",
    default=None,
    metavar="YYYY-MM-DD",
    help="Report date (UTC). Defaults to yesterday.",
)
def report_daily(date: str | None) -> None:
    """Print a 1-page summary of a day's activity (default: yesterday).

    Examples:\n
        aegis report daily\n
        aegis report daily --date 2026-05-02
    """
    asyncio.run(_report_daily(date=date))


async def _report_daily(*, date: str | None = None) -> None:
    import uuid as _uuid

    from aegis.db.pool import PgPool

    cfg = settings()
    pool = PgPool(dsn=cfg.pg_dsn_str)
    await pool.start()
    try:
        if date:
            try:
                start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                click.echo(
                    f"AEGIS-CLI-0006: invalid date {date!r}; expected YYYY-MM-DD",
                    err=True,
                )
                return
        else:
            start = datetime.now(tz=UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=1)
        end = start + timedelta(days=1)
        tenant_uuid = _uuid.UUID(cfg.default_tenant_id)

        async with pool.acquire(tenant_id=tenant_uuid) as conn:
            total = await conn.fetchval(
                """
                SELECT COUNT(*) FROM signals
                WHERE scraped_at >= $1 AND scraped_at < $2
                """,
                start,
                end,
            )
            by_platform = await conn.fetch(
                """
                SELECT platform, COUNT(*) AS n FROM signals
                WHERE scraped_at >= $1 AND scraped_at < $2
                GROUP BY platform
                ORDER BY n DESC
                """,
                start,
                end,
            )

        click.echo("=" * 60)
        click.echo(f"AEGIS Pulse — daily report  {start.date()} (UTC)")
        click.echo("=" * 60)
        click.echo(f"Total signals: {total}")
        click.echo()
        click.echo("By platform:")
        for row in by_platform:
            click.echo(f"  {row['platform']:<16} {row['n']:>8,}")
    finally:
        await pool.close()


# ---------------------------------------------------------------------
# Phase 2 — Analyze (run agent pipeline)
# ---------------------------------------------------------------------


@main.command()
@click.option(
    "--limit",
    type=int,
    default=20,
    show_default=True,
    help="Number of recent signals to pull from DB for the candidate.",
)
@click.option(
    "--platform",
    default=None,
    help="Filter signals by platform (e.g. reddit, tiktok).",
)
@click.option(
    "--trend-id",
    default=None,
    help="Custom trend ID for this run (auto-generated if omitted).",
)
@click.option(
    "--title",
    default=None,
    help="Custom trend title (derived from signals if omitted).",
)
@click.option(
    "--no-llm",
    is_flag=True,
    default=False,
    help="Force heuristic-only path (ignores configured LLM providers).",
)
@click.option(
    "--json-out",
    is_flag=True,
    default=False,
    help="Print the full GraphResult as JSON instead of the summary table.",
)
def analyze(
    limit: int,
    platform: str | None,
    trend_id: str | None,
    title: str | None,
    no_llm: bool,
    json_out: bool,
) -> None:
    """Run recent signals through the Phase 2 agent intelligence pipeline.

    Fetches the N most recent signals from the DB, builds a TrendCandidate,
    runs all 10 LangGraph agent nodes, and prints the verdict.

    Examples:\n
        aegis analyze\n
        aegis analyze --limit 30 --platform reddit\n
        aegis analyze --no-llm --json-out
    """
    asyncio.run(
        _analyze_async(
            limit=limit,
            platform=platform,
            trend_id=trend_id,
            title=title,
            use_llm=not no_llm,
            json_out=json_out,
        )
    )


def _candidate_from_rows(
    rows: list[dict[str, Any]],
    *,
    trend_id: str,
    title: str | None = None,
) -> Any:
    """Build a TrendCandidate with real velocities computed from signal timestamps.

    Computes log-momentum velocities (v1h, v6h, v24h) by bucketing signals
    into 1-hour slots and using the same formula as the Phase 3 feature
    builder — so the candidate metrics align with what Phase 3 would see.
    """
    import math
    from collections import Counter
    from datetime import datetime

    from aegis.agents.schemas import TrendCandidate

    now = datetime.now(tz=UTC)

    # Count signals per hourly bucket (0 = current hour, 1 = 1h ago, …).
    bucket_counts: dict[int, int] = {}
    sentiments: list[float] = []
    commercial_intents: list[float] = []
    novelties: list[float] = []
    author_ids: set[str] = set()
    platform_counter: Counter = Counter()

    for r in rows:
        ts = r.get("captured_at")
        if ts is not None:
            if hasattr(ts, "tzinfo") and ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            hours_back = max(0, int((now - ts).total_seconds() // 3600))
            bucket_counts[hours_back] = bucket_counts.get(hours_back, 0) + 1

        if (sv := r.get("sentiment")) is not None:
            sentiments.append(float(sv))
        if (ci := r.get("commercial_intent")) is not None:
            commercial_intents.append(float(ci))
        if (nv := r.get("novelty")) is not None:
            novelties.append(float(nv))
        if aid := r.get("author_id"):
            author_ids.add(str(aid))
        if plat := r.get("platform"):
            platform_counter[str(plat)] += 1

    # Build ordered counts list (24 buckets, oldest first).
    window = 24
    counts = [float(bucket_counts.get(window - 1 - i, 0)) for i in range(window)]

    def _log1p_window_sum(c: list[float], h: int) -> float:
        return math.log1p(max(0.0, sum(c[max(0, len(c) - h) :])))

    cur_1h = _log1p_window_sum(counts, 1)
    prior_1h = _log1p_window_sum(counts[:-1], 1) if len(counts) > 1 else 0.0
    cur_6h = _log1p_window_sum(counts, 6)
    prior_6h = _log1p_window_sum(counts[:-6], 6) if len(counts) > 6 else 0.0
    cur_24h = _log1p_window_sum(counts, 24)
    prior_24h = _log1p_window_sum(counts[:-24], 24) if len(counts) > 24 else 0.0

    v1h = cur_1h - prior_1h
    v6h = cur_6h - prior_6h
    v24h = cur_24h - prior_24h

    sentiment_mean = sum(sentiments) / len(sentiments) if sentiments else 0.3
    ci_mean = sum(commercial_intents) / len(commercial_intents) if commercial_intents else 0.4
    nov_mean = sum(novelties) / len(novelties) if novelties else 0.5

    # Coordination risk: signals-per-author ratio proxy.
    n_authors = max(1, len(author_ids) or len(rows) // 2)
    ratio = len(rows) / n_authors
    coord_risk = min(1.0, max(0.0, (ratio - 1.0) / 9.0))

    platforms_found = list(platform_counter.keys()) or ["unknown"]
    auto_title = title or (
        f"{platforms_found[0].capitalize()} signal cluster — {len(rows)} signals"
        if len(platforms_found) == 1
        else f"Multi-platform signal cluster — {len(rows)} signals"
    )
    signal_ids = [str(r["signal_id"]) for r in rows if r.get("signal_id")][:10]
    titles_sample = [r.get("title") or "" for r in rows[:5]]
    rep_text = " | ".join(t[:60] for t in titles_sample if t)[:400] or "Signal cluster"

    return TrendCandidate(
        trend_id=trend_id,
        title=auto_title,
        summary=f"Auto-generated candidate from {len(rows)} recent DB signals.",
        velocity_1h=v1h,
        velocity_6h=v6h,
        velocity_24h=v24h,
        sentiment=max(-1.0, min(1.0, sentiment_mean)),
        commercial_intent=max(0.0, min(1.0, ci_mean)),
        novelty=max(0.0, min(1.0, nov_mean)),
        coordination_risk=coord_risk,
        signal_count=len(rows),
        unique_authors=n_authors,
        platforms=platforms_found,
        sample_signal_ids=signal_ids,
        representative_text=rep_text,
    )


async def _analyze_async(
    *,
    limit: int,
    platform: str | None,
    trend_id: str | None,
    title: str | None,
    use_llm: bool,
    json_out: bool,
) -> None:
    import uuid as _uuid

    import redis.asyncio as aioredis

    from aegis.agents.runner import run_trend
    from aegis.core.logging import configure_logging
    from aegis.db.pool import PgPool
    from aegis.db.signals import fetch_recent_signals

    configure_logging()
    cfg = settings()
    tenant_uuid = _uuid.UUID(cfg.default_tenant_id)
    auto_trend_id = trend_id or f"analyze-{_uuid.uuid4().hex[:8]}"

    pool = PgPool(dsn=cfg.pg_dsn_str)
    await pool.start()
    try:
        rows = await fetch_recent_signals(
            pool, tenant_id=tenant_uuid, limit=limit, platform=platform
        )
    finally:
        await pool.close()

    if not rows:
        click.echo("No signals found in DB. Run `aegis scrape` first.", err=True)
        return

    rows_as_dicts = [dict(r) for r in rows]
    candidate = _candidate_from_rows(rows_as_dicts, trend_id=auto_trend_id, title=title)

    click.echo(
        f"Running Phase 2+3 pipeline on {len(rows)} signals "
        f"({'heuristic' if not use_llm else 'LLM-assisted'}) "
        f"v1h={candidate.velocity_1h:.2f} v6h={candidate.velocity_6h:.2f} "
        f"v24h={candidate.velocity_24h:.2f}…",
        err=True,
    )
    redis_client = aioredis.from_url(cfg.redis_url_str, decode_responses=True)
    try:
        result = await run_trend(
            candidate,
            signals=rows_as_dicts,
            use_llm=use_llm,
            stream_client=redis_client,
        )
    finally:
        await redis_client.aclose()

    if json_out:
        click.echo(result.model_dump_json(indent=2))
        return

    verdict_color = {
        "proceed": "green",
        "hold": "yellow",
        "block": "red",
        "escalate": "bright_red",
    }.get(result.final_verdict.value, "white")

    click.echo()
    click.echo("=" * 62)
    click.echo(f"  AEGIS Pulse — Phase 2 Analysis   [{auto_trend_id}]")
    click.echo("=" * 62)
    click.echo(
        "  Verdict   : "
        + click.style(result.final_verdict.value.upper(), fg=verdict_color, bold=True)
    )
    click.echo(
        f"  Score     : {result.final_score:.3f}   Confidence: {result.final_confidence:.3f}"
    )
    click.echo(f"  Priority  : {result.final_priority.name}")
    click.echo(f"  Halt      : {result.halt_reason}")
    click.echo(f"  Agents    : {len(result.decisions)} ran   Duration: {result.duration_ms:.0f}ms")
    if result.blocked_by:
        click.echo(f"  Blocked by: {', '.join(result.blocked_by)}")
    click.echo()
    click.echo(f"  {'Agent':<16} {'Verdict':<10} {'Score':>6}  {'Conf':>6}  Rationale")
    click.echo(f"  {'-'*16} {'-'*10} {'-'*6}  {'-'*6}  {'-'*30}")
    for dec in sorted(result.decisions, key=lambda d: d.score, reverse=True):
        click.echo(
            f"  {dec.agent:<16} {dec.verdict.value:<10} {dec.score:>6.3f}  "
            f"{dec.confidence:>6.3f}  {(dec.reasoning or '')[:50]}"
        )
    click.echo("=" * 62)


# ---------------------------------------------------------------------
# PASS6-6B: Deep research — multi-pass ResearchEngine report
# ---------------------------------------------------------------------


@main.command()
@click.option("--topic", "topic_", required=True, help="Topic or query to research.")
@click.option(
    "--depth",
    type=click.Choice(["surface", "standard", "deep"]),
    default="standard",
    show_default=True,
    help="surface = harvest only; standard = + temporal/cross-verify; deep = + risks.",
)
@click.option(
    "--max-signals",
    type=int,
    default=200,
    show_default=True,
    help="Total signal budget across all routed adapters.",
)
@click.option(
    "--json-out",
    is_flag=True,
    default=False,
    help="Print the full research report as raw JSON.",
)
def research(topic_: str, depth: str, max_signals: int, json_out: bool) -> None:
    """Run the multi-pass ResearchEngine on a topic and print the report.

    5-pass methodology: broad harvest → cross-verification → temporal
    analysis → competitive landscape → risk synthesis. Every conclusion is
    backed by multiple independent sources where possible.

    Examples:\n
        aegis research --topic "wireless earbuds" --depth deep\n
        aegis research --topic "AI chips" --json-out
    """
    asyncio.run(_research_async(topic=topic_, depth=depth, max_signals=max_signals, json_out=json_out))


async def _research_async(
    *, topic: str, depth: str, max_signals: int, json_out: bool
) -> None:
    import json as _json

    from aegis.core.logging import configure_logging
    from aegis.intelligence.research_engine import ResearchEngine

    configure_logging()

    redis_client = None
    try:
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings().redis_url_str, decode_responses=True)
    except Exception:
        redis_client = None

    try:
        engine = ResearchEngine(redis=redis_client)
        report = await engine.research(topic, depth=depth, max_signals=max_signals)
    finally:
        if redis_client is not None:
            with contextlib.suppress(Exception):
                await redis_client.aclose()

    if json_out:
        click.echo(_json.dumps(report.to_dict(), indent=2, default=str))
        return

    click.echo("=" * 62)
    click.echo(f"  DEEP RESEARCH — {report.query}  [{report.research_depth}]")
    click.echo("=" * 62)
    click.echo(f"Topic type:  {report.topic_type}")
    click.echo(f"Verdict:     {report.trend_verdict}")
    click.echo(f"Confidence:  {report.confidence_score:.3f}")
    click.echo(f"Signals:     {report.signal_count} from {len(report.sources_consulted)} sources")
    click.echo("")
    click.echo(report.executive_summary)
    if report.cross_verified_findings:
        click.echo("\nCross-verified themes (2+ sources):")
        for theme in report.cross_verified_findings[:10]:
            click.echo(f"  • {theme}")
    if report.key_findings:
        click.echo("\nKey findings:")
        for f in report.key_findings[:8]:
            mark = "✓" if f.verified_by else "?"
            click.echo(f"  {mark} [{f.source}] {f.claim[:90]}")
    if report.risks:
        click.echo("\nRisks:")
        for r in report.risks:
            click.echo(f"  ⚠ {r}")
    if report.opportunities:
        click.echo("\nOpportunities:")
        for o in report.opportunities:
            click.echo(f"  ↑ {o}")
    click.echo("\nRecommended actions:")
    for a in report.recommended_actions:
        click.echo(f"  → {a}")
    click.echo("=" * 62)


# ---------------------------------------------------------------------
# Market intelligence — product-level competitive analysis
# ---------------------------------------------------------------------


@main.command()
@click.argument("query")
@click.option(
    "--depth",
    type=click.Choice(["surface", "standard", "deep"]),
    default="standard",
    show_default=True,
    help="surface = top marketplaces; standard/deep = wider adapter fan-out.",
)
@click.option(
    "--max-products",
    type=int,
    default=240,
    show_default=True,
    help="Total product budget across all routed marketplaces.",
)
@click.option("--llm/--no-llm", default=True, show_default=True,
              help="Add an LLM-written operator verdict (uses both models when council is on).")
@click.option("--gate/--no-gate", default=True, show_default=True,
              help="Run the best pick through compliance + geo + capital advisory gates.")
@click.option("--json-out", is_flag=True, default=False, help="Print the full report as JSON.")
def market(query: str, depth: str, max_products: int, llm: bool, gate: bool, json_out: bool) -> None:
    """Analyze a product market end-to-end like a market operator.

    Harvests live listings across every reachable marketplace, then compares
    price bands, competitors, top products, value picks and cross-platform
    arbitrage gaps for the QUERY you give it — no default topic.

    Examples:\n
        aegis market "men's trimmer"\n
        aegis market "wireless earbuds" --depth deep --json-out
    """
    asyncio.run(_market_async(
        query=query, depth=depth, max_products=max_products,
        llm=llm, gate=gate, json_out=json_out,
    ))


async def _market_async(
    *, query: str, depth: str, max_products: int, llm: bool, gate: bool, json_out: bool
) -> None:
    import json as _json

    from aegis.core.logging import configure_logging
    from aegis.intelligence.product_intel import ProductIntelligenceEngine

    configure_logging()

    pool = redis_client = None
    try:
        from aegis.db.pool import PgPool

        pool = PgPool(dsn=settings().pg_dsn_str)
        await pool.connect()
    except Exception:
        pool = None
    try:
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings().redis_url_str, decode_responses=True)
    except Exception:
        redis_client = None

    try:
        engine = ProductIntelligenceEngine(pool=pool, redis=redis_client)
        report = await engine.analyze(
            query, depth=depth, max_products=max_products, use_llm=llm, gate=gate,
        )
    finally:
        if redis_client is not None:
            with contextlib.suppress(Exception):
                await redis_client.aclose()
        if pool is not None:
            with contextlib.suppress(Exception):
                await pool.aclose()

    if json_out:
        click.echo(_json.dumps(report.to_dict(), indent=2, default=str))
        return

    d = report
    click.echo("=" * 64)
    click.echo(f"  MARKET ANALYSIS — {d.query}  [{depth}]")
    click.echo("=" * 64)
    click.echo(d.executive_summary)
    click.echo("")
    click.echo(f"Products:  {d.product_count} across {len(d.platforms)} marketplaces "
               f"({', '.join(d.platforms) or '—'})")
    if d.price_summary.get("available"):
        ps = d.price_summary
        click.echo(f"Price:     min {ps['min']} · median {ps['median']} · max {ps['max']} "
                   f"({ps.get('spread_pct')}% spread)")
    m = d.momentum
    click.echo(f"Momentum:  {m['label'].upper()} ({m['score']}) · {m['total_reviews']} reviews")
    if d.competitors:
        click.echo("\nTop competitors (by listings):")
        for c in d.competitors[:6]:
            click.echo(f"  • {c['brand']:<18} {c['listings']:>3} listings · "
                       f"{c['share_pct']}% · avg {c['avg_price']} · ★{c['avg_rating']}")
    if d.best_value:
        click.echo("\nBest value:")
        for p in d.best_value[:5]:
            click.echo(f"  ◆ [{p['platform']}] {p['title'][:60]} — {p['price']} ★{p['rating']}")
    if d.arbitrage:
        click.echo("\nCross-platform arbitrage gaps:")
        for a in d.arbitrage[:5]:
            click.echo(f"  ↔ {a['product'][:50]} — {a['cheapest']['platform']} "
                       f"{a['cheapest']['price']} → {a['dearest']['platform']} "
                       f"{a['dearest']['price']} (+{a['gap_pct']}%)")
    if d.gated_pick:
        g = d.gated_pick
        click.echo("\nGated best pick:")
        click.echo(f"  {g.get('product','—')[:60]} [{g.get('platform','?')}]")
        if g.get("compliance"):
            c = g["compliance"]
            click.echo(f"    compliance: {c.get('recommendation')} (risk {c.get('risk_score','—')})")
        if g.get("geo"):
            click.echo(f"    geo: {g['geo'].get('route')} · margin {g['geo'].get('gross_margin_pct')}%")
        if g.get("capital"):
            cap = g["capital"]
            click.echo(f"    capital ({cap.get('mode')}): kelly {cap.get('kelly_fraction')} — {cap.get('rationale')}")
    if d.llm_narrative:
        click.echo("\nLLM operator verdict:")
        for line in d.llm_narrative.splitlines():
            click.echo(f"  {line}")
    click.echo("\nRecommended actions:")
    for a in d.recommended_actions:
        click.echo(f"  → {a}")
    click.echo("=" * 64)


# ---------------------------------------------------------------------
# Topic intelligence — one-word full-cycle command
# ---------------------------------------------------------------------


@main.command()
@click.argument("topic")
@click.option(
    "--limit",
    type=int,
    default=30,
    show_default=True,
    help="Max signals to collect per individual source run.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Scrape and deduplicate but do not write to the database.",
)
@click.option(
    "--no-analyze",
    is_flag=True,
    default=False,
    help="Skip the Phase 2+3 analysis pipeline after scraping.",
)
@click.option(
    "--no-llm",
    is_flag=True,
    default=False,
    help="Force heuristic-only path in the analysis step.",
)
@click.option(
    "--dedup-threshold",
    type=float,
    default=0.82,
    show_default=True,
    help="Semantic similarity threshold for near-duplicate detection (0-1).",
)
@click.option(
    "--json-out",
    is_flag=True,
    default=False,
    help="Print the analysis result as raw JSON.",
)
def topic(
    topic: str,
    limit: int,
    dry_run: bool,
    no_analyze: bool,
    no_llm: bool,
    dedup_threshold: float,
    json_out: bool,
) -> None:
    """Scrape every relevant source for TOPIC and run the full analysis pipeline.

    One word becomes a complete intelligence cycle:\n
      1. Expand TOPIC into search queries and target subreddits\n
      2. Scrape HackerNews, Google News, Reddit, GitHub, Amazon in parallel\n
      3. Semantically deduplicate (near-identical titles removed)\n
      4. Persist unique signals to the database\n
      5. Run Phase 2+3 agent + ML pipeline and print the verdict\n
    \n
    Examples:\n
        aegis topic "AI chips"\n
        aegis topic "bitcoin" --limit 50\n
        aegis topic "e-commerce" --no-analyze --dry-run\n
        aegis topic "NVIDIA" --no-llm --json-out
    """
    asyncio.run(
        _topic_async(
            topic=topic,
            limit=limit,
            dry_run=dry_run,
            run_analyze=not no_analyze,
            use_llm=not no_llm,
            dedup_threshold=dedup_threshold,
            json_out=json_out,
        )
    )


async def _topic_async(
    *,
    topic: str,
    limit: int,
    dry_run: bool,
    run_analyze: bool,
    use_llm: bool,
    dedup_threshold: float,
    json_out: bool,
) -> None:
    import uuid as _uuid

    import redis.asyncio as aioredis

    from aegis.core.logging import configure_logging
    from aegis.db.pool import PgPool
    from aegis.scrape.topic import expand_topic, scrape_topic

    configure_logging()
    cfg = settings()
    tenant_uuid = _uuid.UUID(cfg.default_tenant_id)

    expansion = expand_topic(topic)

    click.echo()
    click.echo("=" * 68)
    click.echo(f"  AEGIS Topic Intelligence — \"{topic}\"")
    click.echo("=" * 68)
    click.echo(f"  Category  : {expansion.category.upper()}")
    if expansion.related_entities:
        click.echo(f"  Related   : {', '.join(expansion.related_entities[:5])}")
    click.echo(f"  Queries   : {len(expansion.search_terms)} search terms generated")
    for i, term in enumerate(expansion.search_terms[:8], 1):
        click.echo(f"              {i:2d}. {term}")
    if len(expansion.search_terms) > 8:
        click.echo(f"              … +{len(expansion.search_terms) - 8} more")
    if expansion.market_angle_queries:
        click.echo(f"  Market    : {' · '.join(q.split(topic + ' ')[-1] for q in expansion.market_angle_queries[:3])}")
    if expansion.competitor_queries:
        click.echo(f"  Compete   : {' · '.join(expansion.competitor_queries[:2])}")
    click.echo(f"  Subreddits: {', '.join(expansion.reddit_subreddits[:5])}")
    click.echo("  Sources   : HackerNews · Google News · Bing News · Reddit · GitHub · Amazon")
    if dry_run:
        click.echo(click.style("  Mode      : DRY RUN — no DB writes", fg="yellow"))
    click.echo("=" * 68)
    click.echo("  Scraping all sources in parallel…", err=True)

    pool: PgPool | None = None
    if not dry_run:
        pool = PgPool(dsn=cfg.pg_dsn_str)
        await pool.start()

    result = await scrape_topic(
        topic,
        pool=pool,
        tenant_id=tenant_uuid if pool else None,
        limit_per_source=limit,
        dry_run=dry_run,
        dedup_threshold=dedup_threshold,
    )

    # Print scrape summary
    click.echo()
    click.echo(f"  Fetched   : {result.total_fetched} signals across {len(result.sources_hit)} sources")
    click.echo(f"  Unique    : {result.total_unique} after semantic dedup (dropped {result.duplicates_dropped})")
    if not dry_run:
        click.echo(f"  Inserted  : {result.total_inserted} new DB rows")
    click.echo(f"  Duration  : {result.duration_s:.1f}s")
    if result.errors:
        click.echo(
            click.style(f"  Warnings  : {len(result.errors)} source errors (non-fatal)", fg="yellow")
        )
        for err in result.errors[:3]:
            click.echo(click.style(f"              - {err[:90]}", fg="yellow"))

    # ── Emerging patterns ──────────────────────────────────────────────
    if result.patterns:
        click.echo()
        click.echo(f"  ── Emerging Patterns ({len(result.patterns)} clusters) ──────────────────")
        for i, cluster in enumerate(result.patterns[:6], 1):
            vel_bar = "█" * int(cluster.velocity_score * 8) + "░" * (8 - int(cluster.velocity_score * 8))
            click.echo(
                f"  {i}. {click.style(cluster.label.title(), bold=True)}"
                f"  [{vel_bar}] {cluster.signal_count} signals"
            )
            if cluster.top_titles:
                click.echo(f"     → {cluster.top_titles[0][:75]}")

    # ── Top signals preview ────────────────────────────────────────────
    if result.signals:
        click.echo()
        click.echo("  ── Top Signals ──────────────────────────────────────────────")
        sorted_sigs = sorted(
            result.signals,
            key=lambda s: ((s.engagement.likes or 0) + (s.engagement.comments or 0)) if s.engagement else 0,
            reverse=True,
        )
        for sig in sorted_sigs[:8]:
            title = (sig.title or "")[:68]
            plat = sig.platform.value.replace("_", " ").title()
            eng = sig.engagement
            score = ((eng.likes or 0) + (eng.comments or 0)) if eng else 0
            click.echo(f"  [{plat:15s}] {title}  ↑{score}")

    if not run_analyze or result.total_unique == 0:
        if pool is not None:
            await pool.close()
        click.echo("=" * 68)
        return

    # ── Phase 2+3 Analysis ────────────────────────────────────────────
    click.echo()
    click.echo(f"  Running Phase 2+3 pipeline on {result.total_unique} signals…", err=True)

    if dry_run:
        # For dry-run, build candidate from in-memory signals directly
        rows_as_dicts = [
            {
                "signal_id": str(sig.signal_id),
                "title": sig.title,
                "platform": sig.platform.value,
                "captured_at": sig.posted_at or sig.provenance.scraped_at,
                "sentiment": 0.3,
                "commercial_intent": 0.4,
                "novelty": 0.5,
                "author_id": str(sig.author.platform_user_id) if sig.author else None,
            }
            for sig in result.signals
        ]
    else:
        # Re-fetch from DB to get enriched rows with computed columns
        from aegis.db.signals import fetch_recent_signals

        # Reuse the pool from the scraping phase — no second connect/close cycle.
        db_rows = await fetch_recent_signals(
            pool, tenant_id=tenant_uuid, limit=result.total_unique + 20
        )
        rows_as_dicts = [dict(r) for r in db_rows]

    if not rows_as_dicts:
        if pool is not None:
            await pool.close()
        click.echo("  No rows available for analysis.", err=True)
        click.echo("=" * 66)
        return

    from aegis.agents.runner import run_trend

    auto_trend_id = f"topic-{topic.lower().replace(' ', '-')[:24]}-{_uuid.uuid4().hex[:6]}"
    candidate = _candidate_from_rows(rows_as_dicts, trend_id=auto_trend_id, title=topic)

    redis_client = aioredis.from_url(cfg.redis_url_str, decode_responses=True)
    try:
        analysis = await run_trend(
            candidate,
            signals=rows_as_dicts,
            use_llm=use_llm,
            stream_client=redis_client if not dry_run else None,
        )
    finally:
        await redis_client.aclose()

    if json_out:
        click.echo(analysis.model_dump_json(indent=2))
        return

    verdict_color = {
        "proceed": "green",
        "hold": "yellow",
        "block": "red",
        "escalate": "bright_red",
    }.get(analysis.final_verdict.value, "white")

    click.echo()
    click.echo("  ── Analysis Result ──────────────────────────────────────────")
    click.echo(
        "  Verdict   : "
        + click.style(analysis.final_verdict.value.upper(), fg=verdict_color, bold=True)
    )
    click.echo(
        f"  Score     : {analysis.final_score:.3f}   "
        f"Confidence: {analysis.final_confidence:.3f}   "
        f"Priority: {analysis.final_priority.name}"
    )
    click.echo(f"  Halt      : {analysis.halt_reason}   Duration: {analysis.duration_ms:.0f}ms")
    if analysis.blocked_by:
        click.echo(f"  Blocked by: {', '.join(analysis.blocked_by)}")
    click.echo()
    click.echo(f"  {'Agent':<16} {'Verdict':<10} {'Score':>6}  {'Conf':>6}  Rationale")
    click.echo(f"  {'-'*16} {'-'*10} {'-'*6}  {'-'*6}  {'-'*30}")
    for dec in sorted(analysis.decisions, key=lambda d: d.score, reverse=True):
        click.echo(
            f"  {dec.agent:<16} {dec.verdict.value:<10} {dec.score:>6.3f}  "
            f"{dec.confidence:>6.3f}  {(dec.reasoning or '')[:50]}"
        )
    if pool is not None:
        await pool.close()
    click.echo("=" * 68)


# ---------------------------------------------------------------------
# DB duplicate sweep
# ---------------------------------------------------------------------


@main.command()
@click.option(
    "--lookback-hours",
    type=int,
    default=168,
    show_default=True,
    help="How far back to scan for duplicates (default: 7 days).",
)
@click.option(
    "--threshold",
    type=float,
    default=0.85,
    show_default=True,
    help="Similarity threshold (0-1). Higher = stricter matching.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=True,
    help="Report duplicates found without deleting them (default: dry-run on).",
)
@click.option(
    "--delete",
    is_flag=True,
    default=False,
    help="Actually DELETE detected duplicates (overrides --dry-run).",
)
def dedup(lookback_hours: int, threshold: float, dry_run: bool, delete: bool) -> None:
    """Scan the signals database for semantic duplicates and optionally remove them.

    Keeps the OLDEST version of each near-duplicate story; removes the newer
    rewrites. Runs dry by default — use --delete to actually remove rows.

    Examples:\n
        aegis dedup\n
        aegis dedup --lookback-hours 720 --threshold 0.90\n
        aegis dedup --delete
    """
    actually_delete = delete  # --delete overrides the dry-run default
    asyncio.run(
        _dedup_async(
            lookback_hours=lookback_hours,
            threshold=threshold,
            dry_run=not actually_delete,
        )
    )


async def _dedup_async(*, lookback_hours: int, threshold: float, dry_run: bool) -> None:
    import uuid as _uuid

    from aegis.core.logging import configure_logging
    from aegis.db.dedup import sweep_db_duplicates
    from aegis.db.pool import PgPool

    configure_logging(level="WARNING")
    cfg = settings()
    tenant_uuid = _uuid.UUID(cfg.default_tenant_id)

    mode_label = "DRY RUN — no rows will be deleted" if dry_run else "LIVE — duplicates will be DELETED"
    click.echo()
    click.echo("=" * 60)
    click.echo("  AEGIS — Semantic Duplicate Sweep")
    click.echo("=" * 60)
    click.echo(f"  Lookback : {lookback_hours}h ({lookback_hours // 24}d)")
    click.echo(f"  Threshold: {threshold:.0%} similarity")
    click.echo(click.style(f"  Mode     : {mode_label}", fg="yellow" if dry_run else "red"))
    click.echo()

    if not dry_run and not click.confirm(
        "This will DELETE rows permanently from the database. Continue?", default=False
    ):
        click.echo("Aborted.", err=True)
        return

    pool = PgPool(dsn=cfg.pg_dsn_str)
    await pool.start()
    try:
        click.echo("  Scanning signals…", err=True)
        stats = await sweep_db_duplicates(
            pool,
            tenant_uuid,
            lookback_hours=lookback_hours,
            threshold=threshold,
            dry_run=dry_run,
        )
    finally:
        await pool.close()

    click.echo(f"  Checked  : {stats['total_checked']:,} signals")
    click.echo(
        "  Dupes    : "
        + click.style(f"{stats['duplicates_found']:,} found", fg="yellow" if stats["duplicates_found"] else "green")
    )
    if not dry_run:
        click.echo(
            "  Deleted  : "
            + click.style(f"{stats['deleted']:,} rows removed", fg="red" if stats["deleted"] else "green")
        )
    else:
        click.echo(
            click.style(
                f"  (Dry run — re-run with `aegis dedup --delete` to remove {stats['duplicates_found']} rows)",
                fg="cyan",
            )
        )
    click.echo("=" * 60)


# ---------------------------------------------------------------------
# Emerging patterns from DB signals
# ---------------------------------------------------------------------


@main.command()
@click.option(
    "--limit",
    type=int,
    default=300,
    show_default=True,
    help="Number of recent signals to analyse.",
)
@click.option(
    "--min-cluster-size",
    type=int,
    default=2,
    show_default=True,
    help="Minimum signals per cluster to report.",
)
@click.option(
    "--platform",
    default=None,
    help="Filter signals by platform before clustering.",
)
@click.option(
    "--threshold",
    type=float,
    default=0.28,
    show_default=True,
    help="Cosine similarity threshold for clustering (0-1).",
)
def patterns(limit: int, min_cluster_size: int, platform: str | None, threshold: float) -> None:
    """Detect emerging thematic patterns in recent DB signals.

    Clusters recent signals by semantic similarity and ranks clusters by
    velocity (size × recency). Shows what topics are gaining momentum.

    Examples:\n
        aegis patterns\n
        aegis patterns --limit 500 --min-cluster-size 3\n
        aegis patterns --platform hacker_news
    """
    asyncio.run(
        _patterns_async(
            limit=limit,
            min_cluster_size=min_cluster_size,
            platform=platform,
            threshold=threshold,
        )
    )


async def _patterns_async(
    *,
    limit: int,
    min_cluster_size: int,
    platform: str | None,
    threshold: float,
) -> None:
    import uuid as _uuid

    from aegis.core.logging import configure_logging
    from aegis.db.pool import PgPool
    from aegis.db.signals import fetch_recent_signals
    from aegis.scrape.patterns import detect_patterns

    configure_logging(level="WARNING")
    cfg = settings()
    tenant_uuid = _uuid.UUID(cfg.default_tenant_id)

    pool = PgPool(dsn=cfg.pg_dsn_str)
    await pool.start()
    try:
        rows = await fetch_recent_signals(pool, tenant_id=tenant_uuid, limit=limit, platform=platform)
    finally:
        await pool.close()

    if not rows:
        click.echo("No signals in DB. Run `aegis scrape` or `aegis topic` first.", err=True)
        return

    click.echo()
    click.echo("=" * 66)
    click.echo(f"  AEGIS — Emerging Patterns  ({len(rows)} signals analysed)")
    click.echo("=" * 66)

    # Convert DB rows to dicts for the pattern detector
    rows_as_dicts = [dict(r) for r in rows]
    clusters = detect_patterns(rows_as_dicts, min_cluster_size=min_cluster_size, similarity_threshold=threshold)

    if not clusters:
        click.echo("  No significant clusters found. Try --min-cluster-size 1 or more signals.")
        click.echo("=" * 66)
        return

    click.echo(f"  Found {len(clusters)} emerging pattern clusters:\n")
    for i, cluster in enumerate(clusters, 1):
        vel_bar = "█" * int(cluster.velocity_score * 10) + "░" * (10 - int(cluster.velocity_score * 10))
        plat_str = ", ".join(cluster.platforms[:4])
        click.echo(
            f"  [{i:2d}] {click.style(cluster.label.title(), bold=True)}"
            f"  ({cluster.signal_count} signals)"
        )
        click.echo(
            f"       Velocity [{vel_bar}] {cluster.velocity_score:.2f}  "
            f"Recency {cluster.recency_weight:.0%}  "
            f"Sources: {plat_str}"
        )
        for title in cluster.top_titles[:3]:
            click.echo(f"       · {title[:80]}")
        click.echo()

    click.echo("=" * 66)


# ---------------------------------------------------------------------
# Daily workflow
# ---------------------------------------------------------------------


@main.command()
@click.option(
    "--subreddit",
    default="MachineLearning",
    show_default=True,
    help="Reddit subreddit to scrape.",
)
@click.option(
    "--limit",
    type=int,
    default=200,
    show_default=True,
    help="Total signals to collect across all sources.",
)
@click.option(
    "--swarm/--no-swarm",
    default=False,
    help="Use full swarm intelligence (30+ adapters). Default: --no-swarm.",
)
def daily(subreddit: str, limit: int, swarm: bool) -> None:
    """Run the full daily workflow in one command.

    Scrapes all working sources → stores in DB → runs AI analysis → prints verdict.
    This is the ONE command to run every day.

    Examples:\n
        aegis daily\n
        aegis daily --subreddit Entrepreneur\n
        aegis daily --limit 100\n
        aegis daily --swarm
    """
    if swarm:
        asyncio.run(_daily_swarm_async(limit=limit))
    else:
        asyncio.run(_daily_async(subreddit=subreddit, limit=limit))


async def _daily_async(*, subreddit: str, limit: int) -> None:
    import uuid as _uuid

    from aegis.agents.runner import run_trend
    from aegis.core.logging import configure_logging
    from aegis.db.pool import PgPool
    from aegis.db.signals import fetch_recent_signals

    # Suppress all logs — daily command only shows the friendly formatted output
    configure_logging(level="ERROR")

    cfg = settings()
    now_str = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    click.echo()
    click.echo("=" * 57)
    click.echo(f"  AEGIS Pulse — Daily Intelligence Run   {now_str}")
    click.echo("=" * 57)
    click.echo()
    click.echo("Collecting signals from 4 sources...")
    click.echo()

    per_source = max(20, limit // 4)
    sources = [
        ("reddit-rss", {"subreddit": subreddit}, f"Reddit (r/{subreddit})"),
        ("hacker-news", {}, "Hacker News"),
        ("github-trending", {}, "GitHub Trending"),
        ("amazon", {}, "Amazon Bestsellers"),
    ]

    total_collected = 0
    for i, (source_name, extra_params, label) in enumerate(sources, 1):
        click.echo(f"  [{i}/4] {label:<32}", nl=False)
        try:
            n = await _scrape_async(
                source=source_name,
                limit=per_source,
                dry_run=False,
                subreddit=extra_params.get("subreddit"),
                query=extra_params.get("query"),
                hashtag=None,
                country=None,
                quiet=True,
            )
            total_collected += n
            click.echo(click.style(f"✓  {n} signals", fg="green"))
        except Exception as e:
            click.echo(click.style(f"✗  failed ({e!s:.40})", fg="red"))

    click.echo()
    click.echo(f"  Total collected:  {total_collected} signals")
    click.echo()

    # Check DB for total + run analysis
    pool = PgPool(dsn=cfg.pg_dsn_str)
    rows = []
    db_total: int = 0
    try:
        await pool.start()
        tenant_uuid = _uuid.UUID(cfg.default_tenant_id)
        rows = await fetch_recent_signals(pool, tenant_id=tenant_uuid, limit=limit)
        try:
            async with pool.acquire(tenant_id=tenant_uuid) as conn:
                db_total = await conn.fetchval("SELECT COUNT(*) FROM signals") or 0
        except Exception:
            db_total = 0
    except Exception:
        db_total = 0
    finally:
        try:
            await pool.close()
        except Exception as _pool_exc:
            import logging as _stdlib_log
            _stdlib_log.getLogger("aegis.cli").warning("pool.close() failed during daily cmd: %s", _pool_exc)

    if not rows:
        click.echo("  No signals in database.", err=False)
        click.echo("  Make sure `aegis up` has been run and Docker is running.", err=False)
        return

    click.echo(f"  Total in database: {db_total:,} signals")
    click.echo()
    click.echo("-" * 57)
    click.echo("  Running AI intelligence analysis...")
    click.echo("-" * 57)
    click.echo()

    rows_as_dicts = [dict(r) for r in rows]
    daily_trend_id = f"daily-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M')}"
    candidate = _candidate_from_rows(rows_as_dicts, trend_id=daily_trend_id)

    result = await run_trend(candidate, signals=rows_as_dicts, use_llm=True)

    # ── Verdict display ────────────────────────────────────
    _VERDICT_COLORS = {
        "proceed": "green",
        "hold": "yellow",
        "block": "red",
        "escalate": "bright_red",
    }
    _VERDICT_MEANING = {
        "proceed": (
            "Strong signal detected!\n"
            "  The AI is confident this trend is real and moving fast.\n"
            "  Consider researching and acting on it soon."
        ),
        "hold": (
            "Moderate signal — not conclusive yet.\n"
            "  Interesting activity but not enough confidence to act.\n"
            "  Check again tomorrow; the pattern may strengthen."
        ),
        "block": (
            "Weak or suspicious signal.\n"
            "  The AI recommends ignoring this for now.\n"
            "  Could be noise, fake activity, or too low volume."
        ),
        "escalate": (
            "Urgent signal requiring immediate human review.\n"
            "  Multiple high-confidence agents flagged this as critical."
        ),
    }
    _PRIORITY_LABEL = {
        "P0": "BREAKOUT — act immediately",
        "P1": "Exit signal — act within hours",
        "P2": "Opportunity — act within 24 hours",
        "P3": "Monitor — no immediate action needed",
    }
    _AGENT_PLAIN = {
        "scout": "How fast is this trend growing?",
        "geo_arbitrage": "Are there regional pricing gaps?",
        "narrative": "Does the story hold together?",
        "historian": "Have we seen similar patterns before?",
        "sourcer": "How reliable are the sources?",
        "auditor": "Is the data fresh and complete?",
        "sentinel": "Any fake/coordinated activity?",
        "compliance": "Any legal/ToS risks?",
        "red_team": "Devil's advocate — what could go wrong?",
        "hedge": "Risk-adjusted expected value",
    }

    verdict_str = result.final_verdict.value
    color = _VERDICT_COLORS.get(verdict_str, "white")
    priority_key = (
        f"P{result.final_priority.value}"
        if hasattr(result.final_priority, "value")
        else str(result.final_priority)[:2]
    )

    click.echo("=" * 57)
    click.echo("  VERDICT   ── " + click.style(verdict_str.upper(), fg=color, bold=True))
    click.echo(
        f"  SCORE     ── {result.final_score:.2f} / 1.0"
        f"   (Confidence: {result.final_confidence:.0%})"
    )
    click.echo(f"  PRIORITY  ── {_PRIORITY_LABEL.get(priority_key, priority_key)}")
    click.echo("=" * 57)
    click.echo()
    click.echo("  What this means:")
    for line in _VERDICT_MEANING.get(verdict_str, "").split("\n"):
        click.echo(f"  {line}")
    click.echo()
    click.echo("  Agent breakdown:")
    click.echo(f"  {'Agent':<16} {'Result':<10} {'Score':>5}  Question")
    click.echo(f"  {'-'*16} {'-'*10} {'-'*5}  {'-'*30}")
    for dec in sorted(result.decisions, key=lambda d: d.score, reverse=True):
        v_color = _VERDICT_COLORS.get(dec.verdict.value, "white")
        question = _AGENT_PLAIN.get(dec.agent, "")
        click.echo(
            f"  {dec.agent:<16} "
            + click.style(f"{dec.verdict.value:<10}", fg=v_color)
            + f" {dec.score:>5.2f}  {question}"
        )
    click.echo()

    if result.final_score < 0.5:
        click.echo(
            click.style(
                "  Tip: add a free Groq API key to improve analysis quality.\n"
                "       Sign up at https://console.groq.com → copy key → add to .env:\n"
                "       GROQ_API_KEY=gsk_...",
                fg="cyan",
            )
        )
    click.echo()


# ---------------------------------------------------------------------
# Reset / Doctor / Support bundle
# ---------------------------------------------------------------------


@main.command()
def reset() -> None:
    """DANGER: stop the stack and wipe all local data volumes."""
    if not click.confirm(
        "This will permanently delete all local Postgres, Redis, and MinIO " "data. Continue?",
        default=False,
    ):
        click.echo("Aborted.", err=True)
        return
    cmd = [*_docker_compose(), "down", "--volumes", "--remove-orphans"]
    _run(cmd, cwd=_repo_root())
    click.echo("Reset complete. Run `aegis up` to start fresh.", err=True)


@main.command()
@click.option(
    "--secrets",
    is_flag=True,
    default=False,
    help="Only check that required secrets are present.",
)
def doctor(secrets: bool) -> None:
    """Run the bootstrap health check.

    Delegates to ``bootstrap/aegis-doctor`` which prints a coloured
    status table. The ``--secrets`` flag does a fast Python-only check
    instead of calling the full bash script.
    """
    if secrets:
        _check_secrets()
        return
    doctor_path = _repo_root() / "bootstrap" / "aegis-doctor"
    if not doctor_path.exists():
        click.echo(
            "AEGIS-CLI-0004: bootstrap/aegis-doctor not found; " "run from the repo root.",
            err=True,
        )
        sys.exit(1)
    # Ensure the script is executable (git doesn't always preserve mode)
    doctor_path.chmod(doctor_path.stat().st_mode | 0o111)
    _run([str(doctor_path)], check=False)


def _check_secrets() -> None:
    cfg = settings()
    missing: list[str] = []
    # Required: PG DSN and Redis URL must point somewhere real.
    if not cfg.pg_dsn_str:
        missing.append("AEGIS_PG_DSN")
    if not cfg.redis_url_str:
        missing.append("AEGIS_REDIS_URL")
    if missing:
        click.echo(
            "AEGIS-CLI-0005: missing required secrets: " + ", ".join(missing),
            err=True,
        )
        sys.exit(1)
    click.echo("all required secrets present")


@main.command("support-bundle")
@click.option(
    "--out",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Output path (default: aegis-support-YYYYMMDD-HHMMSS.zip).",
)
def support_bundle(out: str | None) -> None:
    """Collect a sanitised diagnostic zip for sharing.

    Captures: ``docker compose ps``, last 1000 log lines from each
    service, ``aegis doctor`` output, ``pyproject.toml``, the migrations
    directory, and a redacted snapshot of environment variables (any
    ``*_KEY``, ``*_TOKEN``, ``*_SECRET``, ``*_PASSWORD`` value is
    replaced with ``"<redacted>"``).
    """
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    out_path = Path(out) if out else Path.cwd() / f"aegis-support-{timestamp}.zip"

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        # 1. compose ps
        try:
            (tmpdir / "compose-ps.txt").write_text(
                subprocess.run(
                    [*_docker_compose(), "ps"],
                    cwd=_repo_root(),
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout,
            )
        except Exception as exc:
            (tmpdir / "compose-ps.error.txt").write_text(repr(exc))

        # 2. logs (last 1000 lines, all services)
        try:
            (tmpdir / "compose-logs.txt").write_text(
                subprocess.run(
                    [*_docker_compose(), "logs", "--tail", "1000"],
                    cwd=_repo_root(),
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout,
            )
        except Exception as exc:
            (tmpdir / "compose-logs.error.txt").write_text(repr(exc))

        # 3. redacted environment
        sensitive_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "DSN", "URL")
        env_dump = {}
        for k, v in os.environ.items():
            if not k.startswith("AEGIS_"):
                continue
            if any(m in k.upper() for m in sensitive_markers):
                env_dump[k] = "<redacted>"
            else:
                env_dump[k] = v
        (tmpdir / "env.json").write_text(json.dumps(env_dump, indent=2))

        # 4. version + python info
        (tmpdir / "version.txt").write_text(f"aegis-pulse {__version__}\npython {sys.version}\n")

        # 5. zip everything up
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in tmpdir.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=p.relative_to(tmpdir))

    click.echo(f"Support bundle written to {out_path}", err=True)


async def _daily_swarm_async(*, limit: int) -> None:
    """Run daily via full swarm intelligence (30+ adapters, 4 waves)."""
    from aegis.core.logging import configure_logging
    from aegis.scrape.swarm import SwarmOrchestrator

    configure_logging(level="ERROR")
    now_str = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    click.echo()
    click.echo("=" * 57)
    click.echo(f"  AEGIS Pulse — Swarm Intelligence Run   {now_str}")
    click.echo("=" * 57)
    click.echo("  Running 4 waves across 30+ adapters (dry-run mode)…")
    click.echo()

    orch = SwarmOrchestrator()
    result = await orch.run_all_waves(limit=max(10, limit // 4), dry_run=True)

    click.echo(f"  Total signals    : {result.total_signals}")
    click.echo(f"  Platforms hit    : {len(result.by_platform)}")
    click.echo(f"  Market pulse     : {result.market_pulse.upper()}")
    if result.cross_platform_themes:
        click.echo(f"  Top themes       : {', '.join(result.cross_platform_themes[:5])}")
    click.echo()
    for ws in result.wave_stats:
        click.echo(
            f"  Wave {ws.wave_number}: {ws.agents_run:2d} agents  "
            f"{ws.signals_collected:4d} signals  "
            f"{ws.duration_ms:.0f}ms  "
            f"({ws.failures} failures)"
        )
    click.echo()
    click.echo(f"  {result.conclusion}")
    click.echo("=" * 57)


# ---------------------------------------------------------------------
# Swarm Intelligence command group
# ---------------------------------------------------------------------


@main.group("swarm")
def swarm_group() -> None:
    """Swarm Intelligence — run all 30+ adapters in parallel waves."""


@swarm_group.command("run")
@click.option("--tiers", default="all", show_default=True, help="Waves to run: all | social | news | ecommerce | tech")
@click.option("--limit", type=int, default=50, show_default=True, help="Max signals per adapter.")
@click.option("--dry-run", is_flag=True, default=False, help="Run without persisting to DB or Redis.")
@click.option("--json-out", is_flag=True, default=False, help="Print SwarmResult as JSON.")
def swarm_run(tiers: str, limit: int, dry_run: bool, json_out: bool) -> None:
    """Run all scraper adapters in parallel waves.

    Examples:\n
        aegis swarm run --limit 20 --dry-run\n
        aegis swarm run --tiers social --limit 30\n
        aegis swarm run --json-out
    """
    asyncio.run(_swarm_run_async(tiers=tiers, limit=limit, dry_run=dry_run, json_out=json_out))


async def _swarm_run_async(*, tiers: str, limit: int, dry_run: bool, json_out: bool) -> None:
    from aegis.core.logging import configure_logging
    from aegis.scrape.governor import ConcurrencyGovernor
    from aegis.scrape.swarm import (
        WAVE_1_SOCIAL,
        WAVE_2_NEWS,
        WAVE_3_ECOMMERCE,
        WAVE_4_TECH,
        SwarmOrchestrator,
    )
    from aegis.scrape.swarm_agents import SwarmAgentPool

    configure_logging(level="WARNING")
    now_str = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    if not json_out:
        click.echo()
        click.echo("=" * 62)
        click.echo(f"  AEGIS Swarm Intelligence   {now_str}")
        if dry_run:
            click.echo(click.style("  Mode: DRY RUN — no DB writes", fg="yellow"))
        click.echo("=" * 62)

    # Filter waves by --tiers
    _tier_map = {
        "social": WAVE_1_SOCIAL,
        "news": WAVE_2_NEWS,
        "ecommerce": WAVE_3_ECOMMERCE,
        "tech": WAVE_4_TECH,
    }
    if tiers != "all" and tiers in _tier_map:
        from aegis.scrape.swarm import _build_all_agents  # type: ignore[attr-defined]

        wanted = set(_tier_map[tiers])
        agents = [a for a in _build_all_agents() if a.name in wanted]
        governor = ConcurrencyGovernor()
        pool = SwarmAgentPool(agents, governor)
        orch = SwarmOrchestrator(pool=pool)
    else:
        orch = SwarmOrchestrator()

    result = await orch.run_all_waves(limit=limit, dry_run=dry_run)

    if json_out:
        click.echo(result.model_dump_json(indent=2))
        return

    click.echo(f"  Total signals    : {result.total_signals}")
    click.echo(f"  Platforms hit    : {len(result.by_platform)}")
    click.echo(f"  Market pulse     : {click.style(result.market_pulse.upper(), bold=True)}")
    if result.cross_platform_themes:
        click.echo(f"  Top themes       : {', '.join(result.cross_platform_themes[:5])}")
    if result.hot_categories:
        click.echo(f"  Hot categories   : {', '.join(result.hot_categories[:5])}")
    click.echo()
    for ws in result.wave_stats:
        bar = "█" * ws.signals_collected + "░" * max(0, 20 - ws.signals_collected)
        click.echo(
            f"  Wave {ws.wave_number} [{bar[:20]}] "
            f"{ws.agents_run:2d} agents  "
            f"{ws.signals_collected:4d} signals  "
            f"{ws.duration_ms:6.0f}ms  "
            f"({ws.failures} fail)"
        )
    click.echo()
    click.echo(f"  {result.conclusion}")
    click.echo("=" * 62)


@swarm_group.command("agents")
def swarm_agents() -> None:
    """Print agent health table for all registered adapters."""
    from aegis.scrape.swarm import _build_all_agents

    agents = _build_all_agents()

    header = f"  {'AGENT':<22} {'PLATFORM':<18} {'TIER':<14} {'HEALTH':<10} {'SIGNALS':>7}  {'LATENCY':>8}  {'FAIL':>4}"
    sep = "  " + "─" * (len(header) - 2)
    click.echo()
    click.echo(header)
    click.echo(sep)

    health_colors = {
        "HEALTHY": "green",
        "DEGRADED": "yellow",
        "DOWN": "red",
        "COOLING": "bright_red",
        "UNKNOWN": "white",
    }
    for agent in agents:
        health_val = agent.health.value
        color = health_colors.get(health_val, "white")
        latency = f"{agent.avg_latency_ms:.0f}ms" if agent.avg_latency_ms else "—"
        click.echo(
            f"  {agent.name:<22} "
            f"{agent.platform:<18} "
            f"{agent.tier:<14} "
            + click.style(f"{health_val:<10}", fg=color)
            + f" {agent.last_signal_count:>7}  {latency:>8}  {agent.consecutive_failures:>4}"
        )
    click.echo()
    click.echo(f"  {len(agents)} adapters registered across 4 waves.")
    click.echo()


# ---------------------------------------------------------------------
# Data Lake command group (Phase 10)
# ---------------------------------------------------------------------


@main.group("datalake")
def datalake_group() -> None:
    """Data Lake — Bronze/Silver/Gold analytics over Parquet on MinIO."""


try:
    from aegis.datalake.cli.main import cli as _datalake_cli

    for _cmd in _datalake_cli.commands.values():
        datalake_group.add_command(_cmd)
except (ImportError, ModuleNotFoundError):  # datalake optional-deps may be absent
    pass


@main.group("llm")
def llm_group() -> None:
    """Phase 11 — Local LLM Orchestration (Ollama · Groq · OpenRouter · Gemini)."""


try:
    from aegis.llm.cli.commands import llm_group as _llm_cli

    for _cmd in _llm_cli.commands.values():
        llm_group.add_command(_cmd)
except (ImportError, ModuleNotFoundError):  # llm optional-deps (sentence-transformers) may be absent
    pass


@main.group("backup")
def backup_group() -> None:
    """Phase 15 — Disaster Recovery & Business Continuity."""


try:
    from aegis.backup.cli import backup_group as _backup_cli

    for _cmd in _backup_cli.commands.values():
        backup_group.add_command(_cmd)
except (ImportError, ModuleNotFoundError):
    pass


@main.group("dr")
def dr_group() -> None:
    """Phase 15 — DR orchestrator (backup/restore/drill/status/runbook)."""


try:
    from aegis.dr.cli import dr_group as _dr_cli  # type: ignore[import-not-found]

    for _cmd in _dr_cli.commands.values():
        dr_group.add_command(_cmd)
except (ImportError, ModuleNotFoundError):
    pass


@main.group("geo")
def geo_group() -> None:
    """Phase 7 — Geospatial Intelligence & Cross-Market Arbitrage."""


try:
    from aegis.geo.cli import geo_group as _geo_cli

    for _cmd in _geo_cli.commands.values():
        geo_group.add_command(_cmd)
except (ImportError, ModuleNotFoundError):
    pass


@main.group("evolve")
def evolve_group() -> None:
    """Phase 9 — Autonomous Self-Evolution (retrain · drift · RL policy)."""


try:
    from aegis.evolve.cli import evolve_group as _evolve_cli

    for _cmd in _evolve_cli.commands.values():
        evolve_group.add_command(_cmd)
except (ImportError, ModuleNotFoundError):
    pass


@main.group("trust")
def trust_group() -> None:
    """Phase B — Trust Reconstruction (calibration · trust scores)."""


try:
    from aegis.trust.cli import trust_group as _trust_cli

    for _cmd in _trust_cli.commands.values():
        trust_group.add_command(_cmd)
except (ImportError, ModuleNotFoundError):
    pass


@main.group("memory")
def memory_group() -> None:
    """Phase C — Knowledge Expansion (opportunity · failure memory)."""


try:
    from aegis.memory.cli import memory_group as _memory_cli

    for _cmd in _memory_cli.commands.values():
        memory_group.add_command(_cmd)
except (ImportError, ModuleNotFoundError):
    pass


@main.group("exec")
def exec_group() -> None:
    """Phase D — Execution Intelligence (execution memory · supplier trust)."""


try:
    from aegis.execution_intel.cli import exec_group as _exec_cli

    for _cmd in _exec_cli.commands.values():
        exec_group.add_command(_cmd)
except (ImportError, ModuleNotFoundError):
    pass


@main.group("comply")
def comply_group() -> None:
    """Phase 8 — Regulatory & Compliance Engine (check / rules / brands / doctor)."""


try:
    import typer.main as _typer_main

    from aegis.comply.cli import app as _comply_typer_app

    _comply_click = _typer_main.get_command(_comply_typer_app)
    for _cmd in _comply_click.commands.values():  # type: ignore[union-attr]
        comply_group.add_command(_cmd)
except (ImportError, ModuleNotFoundError):
    pass


if __name__ == "__main__":  # pragma: no cover
    main()
