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

Every command exits with a non-zero code on failure and prints a
machine-readable error code (``AEGIS-CLI-NNNN``) for documentation
lookups under ``docs/errors/``.
"""

from __future__ import annotations

import asyncio
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


# ---------------------------------------------------------------------
# Top-level group
# ---------------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="aegis")
def main() -> None:
    """AEGIS Pulse — autonomous market arbitrage intelligence engine."""


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
            # Some versions emit a JSON array; try that.
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
    # --- No API key required ---
    "tiktok": ("aegis.scrape.sources.tiktok", "TikTokAdapter"),
    "pinterest": ("aegis.scrape.sources.pinterest", "PinterestAdapter"),
    "amazon": ("aegis.scrape.sources.amazon", "AmazonAdapter"),
    "google-trends": ("aegis.scrape.sources.google_trends", "GoogleTrendsAdapter"),
    "hacker-news": ("aegis.scrape.sources.hacker_news", "HackerNewsAdapter"),
    "nitter": ("aegis.scrape.sources.nitter", "NitterAdapter"),
    "github-trending": ("aegis.scrape.sources.github_trending", "GitHubTrendingAdapter"),
    "reddit-rss": ("aegis.scrape.sources.reddit_rss", "RedditRSSAdapter"),
}


def _load_adapter_class(name: str) -> Any:
    if name not in _ADAPTER_REGISTRY:
        valid = ", ".join(sorted(_ADAPTER_REGISTRY))
        raise click.UsageError(f"Unknown source {name!r}. Valid: {valid}")
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
def scrape(
    source: str,
    limit: int,
    dry_run: bool,
    subreddit: str | None,
    query: str | None,
    hashtag: str | None,
    country: str | None,
    quiet: bool,
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
        )
    )


def _build_adapter(source: str, adapter_cls: Any, *, limit: int) -> Any:
    """Construct the right adapter + config for *source* using settings()."""
    from uuid import UUID as _UUID  # noqa: F401 (used below for clarity)

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

    adapter_cls = _load_adapter_class(source)
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
    result = await run_trend(candidate, signals=rows_as_dicts, use_llm=use_llm)

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
def daily(subreddit: str, limit: int) -> None:
    """Run the full daily workflow in one command.

    Scrapes all working sources → stores in DB → runs AI analysis → prints verdict.
    This is the ONE command to run every day.

    Examples:\n
        aegis daily\n
        aegis daily --subreddit Entrepreneur\n
        aegis daily --limit 100
    """
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
        try:  # noqa: SIM105
            await pool.close()
        except Exception:
            pass

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


if __name__ == "__main__":  # pragma: no cover
    main()
