"""
Click CLI for Phase C — Knowledge Expansion.

Registered under the main ``aegis`` CLI as ``aegis memory``.

    aegis memory backfill   — reconstruct opportunity + failure memory from
                              settled signal_outcomes (idempotent)
    aegis memory patterns   — recurring opportunity patterns (realized vs failed)
    aegis memory failures   — failure-category recurrence counts
"""

from __future__ import annotations

import asyncio
import json

import click


def _pool():  # noqa: ANN202 — returns asyncpg pool coroutine
    import asyncpg

    from aegis.db.pool import resolve_dsn

    return asyncpg.create_pool(resolve_dsn(), min_size=1, max_size=4)


@click.group("memory")
def memory_group() -> None:
    """Phase C — Knowledge Expansion (opportunity · failure memory)."""


@memory_group.command("backfill")
@click.option("--max-rows", default=5000, show_default=True)
@click.option("--json-out", is_flag=True)
def cmd_backfill(max_rows: int, json_out: bool) -> None:
    """Populate opportunity + failure memory from settled signal_outcomes."""
    from aegis.memory.backfill import MemoryBackfill

    async def _run() -> dict:
        pool = await _pool()
        try:
            summary = await MemoryBackfill(pool).run(max_rows=max_rows)
            return {
                "examined": summary.examined,
                "opportunities": summary.opportunities,
                "failures": summary.failures,
                "skipped": summary.skipped,
            }
        finally:
            await pool.close()

    result = asyncio.run(_run())
    if json_out:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(
            f"backfill: examined={result['examined']} "
            f"opportunities={result['opportunities']} "
            f"failures={result['failures']} skipped={result['skipped']}"
        )


@memory_group.command("patterns")
@click.option("--days-back", default=90, show_default=True)
@click.option("--limit", default=20, show_default=True)
@click.option("--json-out", is_flag=True)
def cmd_patterns(days_back: int, limit: int, json_out: bool) -> None:
    """Recurring opportunity patterns with realized vs failed counts."""
    from aegis.memory.opportunity import OpportunityMemory

    async def _run() -> list[dict]:
        pool = await _pool()
        try:
            return await OpportunityMemory(pool).patterns(days_back=days_back, limit=limit)
        finally:
            await pool.close()

    rows = asyncio.run(_run())
    if json_out:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("no settled opportunities yet — run 'aegis memory backfill'")
        return
    for r in rows:
        rate = r["realized_rate"]
        rate_s = f"{rate:.0%}" if rate is not None else "n/a"
        click.echo(
            f"{r['opportunity_type']:14} {r['category']:14} "
            f"total={r['total']:4} realized={r['realized']:4} "
            f"failed={r['failed']:4} realized_rate={rate_s}"
        )


@memory_group.command("sources")
@click.option("--min-outcomes", default=5, show_default=True)
@click.option("--json-out", is_flag=True)
def cmd_sources(min_outcomes: int, json_out: bool) -> None:
    """Rebuild + print per-platform source reliability profiles (Rule 4)."""
    from aegis.memory.source import SourceMemory

    async def _run() -> list[dict]:
        pool = await _pool()
        try:
            profiles = await SourceMemory(pool).build_profiles(min_outcomes=min_outcomes)
            return [p.model_dump(mode="json") for p in profiles]
        finally:
            await pool.close()

    rows = asyncio.run(_run())
    if json_out:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("no source profiles yet — need settled outcomes + signals")
        return
    for r in rows:
        rel = r["reliability_score"]
        rel_s = f"{rel:.0%}" if rel is not None else "n/a"
        fresh = r["freshness_score"]
        fresh_s = f"{fresh:.2f}" if fresh is not None else "n/a"
        click.echo(
            f"{r['source_id']:18} trust={r['trust']:.2f} reliability={rel_s:>5} "
            f"freshness={fresh_s} n_out={r['n_outcomes']:4} n_sig={r['n_signals']}"
        )


@memory_group.command("verify")
@click.argument("trend_key")
@click.option("--category", default="general", show_default=True)
@click.option("--signals", "signal_count", type=int, default=None)
@click.option("--authors", "author_count", type=int, default=None)
@click.option("--platforms", "platform_list", default="",
              help="comma-separated source platforms")
@click.option("--confidence", "confidence", type=float, default=None)
@click.option("--json-out", is_flag=True)
def cmd_verify(
    trend_key: str, category: str, signal_count: int | None,
    author_count: int | None, platform_list: str, confidence: float | None,
    json_out: bool,
) -> None:
    """Reality-verify a candidate opportunity (advisory scores; Rule 8)."""
    from aegis.memory.verify import RealityVerifier

    platforms = [p.strip() for p in platform_list.split(",") if p.strip()]

    async def _run() -> dict:
        pool = await _pool()
        try:
            a = await RealityVerifier(pool).assess(
                trend_key=trend_key, category=category,
                signal_count=signal_count, author_count=author_count,
                platform_count=len(platforms) or None,
                source_platforms=platforms or None,
                prediction_confidence=confidence,
            )
            return a.model_dump(mode="json")
        finally:
            await pool.close()

    a = asyncio.run(_run())
    if json_out:
        click.echo(json.dumps(a, indent=2))
        return
    click.echo(f"trend={a['trend_key']} category={a['category']}")
    click.echo(f"  evidence={a['evidence_score']}  trust={a['trust_score']}")
    click.echo(f"  reality ={a['reality_score']}  unknowns={a['unknowns_score']}")
    click.echo(f"  PASSED={a['passed']}")


@memory_group.command("audit")
@click.option("--days-back", default=7, show_default=True)
@click.option("--json-out", is_flag=True)
def cmd_audit(days_back: int, json_out: bool) -> None:
    """Weekly self-audit: best/worst opportunities, sources, failures (Rule 10)."""
    from aegis.memory.report import SelfAudit

    async def _run() -> dict:
        pool = await _pool()
        try:
            return await SelfAudit(pool).weekly_report(days_back=days_back)
        finally:
            await pool.close()

    rep = asyncio.run(_run())
    if json_out:
        click.echo(json.dumps(rep, indent=2, default=str))
        return
    click.echo(f"=== self-audit ({rep['window_days']}d) ===")
    click.echo(f"opportunity patterns: {rep['opportunity_patterns']}")
    click.echo("best opportunities:")
    for o in rep["best_opportunities"]:
        click.echo(f"  {o['opportunity_type']}/{o['category']} "
                   f"realized_rate={o['realized_rate']}")
    click.echo(f"recurring failures: {rep['recurring_failures']}")
    click.echo("best sources:")
    for s in rep["best_sources"]:
        click.echo(f"  {s['source_id']} trust={s['trust']}")


@memory_group.command("backtest")
@click.option("--json-out", is_flag=True)
def cmd_backtest(json_out: bool) -> None:
    """Validate the Reality gate OOS: realized rate by evidence bucket (Rule 12)."""
    from aegis.memory.backtest import RealityBacktester

    async def _run() -> dict:
        pool = await _pool()
        try:
            return await RealityBacktester(pool).run()
        finally:
            await pool.close()

    res = asyncio.run(_run())
    if json_out:
        click.echo(json.dumps(res, indent=2))
        return
    click.echo(f"status={res['status']} n={res['n']}")
    if res.get("buckets"):
        for b in ("low", "mid", "high"):
            click.echo(f"  {b:5} realized_rate={res['buckets'].get(b)}")
        click.echo(f"  separation (high-low)={res['separation']}")


@memory_group.command("entities")
@click.argument("kind")
@click.option("--limit", default=20, show_default=True)
@click.option("--json-out", is_flag=True)
def cmd_entities(kind: str, limit: int, json_out: bool) -> None:
    """List the most-trusted entities of a kind (Rule 3)."""
    from aegis.memory.entity import EntityMemory
    from aegis.memory.taxonomy import EntityKind

    async def _run() -> list[dict]:
        pool = await _pool()
        try:
            ents = await EntityMemory(pool).top_entities(EntityKind(kind), limit=limit)
            return [e.model_dump(mode="json") for e in ents]
        finally:
            await pool.close()

    rows = asyncio.run(_run())
    if json_out:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo(f"no {kind} entities yet")
        return
    for r in rows:
        click.echo(f"{r['canonical_name']:24} trust={r['trust']:.2f} "
                   f"n_obs={r['n_observations']}")


@memory_group.command("failures")
@click.option("--days-back", default=90, show_default=True)
@click.option("--json-out", is_flag=True)
def cmd_failures(days_back: int, json_out: bool) -> None:
    """Failure-category recurrence — the reusable failure knowledge (Rule 5)."""
    from aegis.memory.failure import FailureMemory

    async def _run() -> dict:
        pool = await _pool()
        try:
            return await FailureMemory(pool).category_counts(days_back=days_back)
        finally:
            await pool.close()

    counts = asyncio.run(_run())
    if json_out:
        click.echo(json.dumps(counts, indent=2))
        return
    if not counts:
        click.echo("no failures recorded yet")
        return
    for cat, n in counts.items():
        click.echo(f"{cat:22} {n}")
