"""
Click CLI for Phase B — Trust Reconstruction.

Registered under the main ``aegis`` CLI as ``aegis trust``.

    aegis trust emit-claims   — emit varying-confidence claims from the heuristic
    aegis trust report        — calibration report (ECE / Brier / reliability)
    aegis trust scores        — per-model + per-source trust scores
"""

from __future__ import annotations

import json

import click


def _pool():  # noqa: ANN202 — returns asyncpg pool coroutine; importing asyncpg at top is avoided
    import asyncpg

    from aegis.db.pool import resolve_dsn

    return asyncpg.create_pool(resolve_dsn(), min_size=1, max_size=4)


@click.group("trust")
def trust_group() -> None:
    """Phase B — Trust Reconstruction (calibration · trust scores)."""


@trust_group.command("emit-claims")
@click.option("--horizon-hours", default=72, show_default=True)
@click.option("--max-claims", default=500, show_default=True)
@click.option("--historical/--live", default=True,
              help="historical = reconstruct+settle from past signals (immediate "
                   "calibration data); live = pending claims for the future.")
@click.option("--json-out", is_flag=True)
def cmd_emit(horizon_hours: int, max_claims: int, historical: bool, json_out: bool) -> None:
    """Emit the heuristic's own varying-confidence falsifiable claims."""
    import asyncio

    from aegis.trust.claim_emitter import ClaimEmitter

    async def _run() -> None:
        from aegis.trust.store import TrustStore

        pool = await _pool()
        try:
            calibrator = await TrustStore(pool).load_map()
            emitter = ClaimEmitter(pool, calibrator=calibrator)
            s = (
                await emitter.backfill_heuristic_claims(
                    horizon_hours=horizon_hours, max_claims=max_claims
                )
                if historical
                else await emitter.emit_active_claims(
                    horizon_hours=horizon_hours, max_trends=max_claims
                )
            )
        finally:
            await pool.close()
        data = {"examined": s.examined, "emitted": s.emitted, "skipped": s.skipped}
        click.echo(json.dumps(data) if json_out else
                   f"Emitted {s.emitted} claims (examined {s.examined}, skipped {s.skipped})")

    asyncio.run(_run())


@trust_group.command("report")
@click.option("--entity-id", default="heuristic", show_default=True)
@click.option("--persist", is_flag=True, help="Write a calibration_snapshots row.")
@click.option("--json-out", is_flag=True)
def cmd_report(entity_id: str, persist: bool, json_out: bool) -> None:
    """Compute the calibration report over settled signal outcomes."""
    import asyncio

    from aegis.trust.calibration import calibration_report
    from aegis.trust.store import TrustStore

    async def _run() -> None:
        pool = await _pool()
        try:
            conn = await pool.acquire()
            await conn.execute(
                "SELECT set_config('app.current_tenant',"
                "'00000000-0000-0000-0000-000000000001',false)"
            )
            rows = await conn.fetch(
                "SELECT prediction_confidence p, resolution_status s "
                "FROM signal_outcomes WHERE resolution_status IN ('correct','incorrect')"
            )
            await pool.release(conn)
            ps = [float(r["p"]) for r in rows]
            ys = [1.0 if r["s"] == "correct" else 0.0 for r in rows]
            rep = calibration_report(ps, ys, entity_kind="model", entity_id=entity_id)
            if persist:
                await TrustStore(pool).save_snapshot(rep)
        finally:
            await pool.close()

        data = rep.model_dump(mode="json", exclude={"bins"})
        if json_out:
            click.echo(json.dumps(data, default=str))
        else:
            click.echo(f"Calibration [{rep.entity_id}]: status={rep.status} n={rep.n}")
            if rep.status == "ok":
                click.echo(
                    f"  ECE={rep.ece:.4f}  Brier={rep.brier:.4f}  "
                    f"BrierSkill={rep.brier_skill_score:+.4f}  base_rate={rep.base_rate:.3f}"
                )
                for b in rep.bins:
                    click.echo(
                        f"  p~{b.mean_predicted:.2f} -> observed {b.observed_freq:.2f} "
                        f"(n={b.count}, gap={b.gap:.2f})"
                    )
            else:
                click.echo(f"  {rep.notes}")

    asyncio.run(_run())


@trust_group.command("fit-calibration")
@click.option("--json-out", is_flag=True)
def cmd_fit_calibration(json_out: bool) -> None:
    """Fit + persist the isotonic P(rise) calibration map from settled outcomes.

    Reads (raw_rise, did-rise) from settled signal_outcomes, fits the map, and
    upserts it. The emitter loads it so future confidences are calibrated.
    """
    import asyncio

    from aegis.trust.calibrator import Calibrator
    from aegis.trust.store import TrustStore

    async def _run() -> None:
        pool = await _pool()
        try:
            conn = await pool.acquire()
            await conn.execute(
                "SELECT set_config('app.current_tenant',"
                "'00000000-0000-0000-0000-000000000001',false)"
            )
            rows = await conn.fetch(
                "SELECT (metadata->>'raw_rise')::float AS raw, observed_direction AS d "
                "FROM signal_outcomes "
                "WHERE resolution_status IN ('correct','incorrect') "
                "AND metadata ? 'raw_rise' AND observed_direction IS NOT NULL"
            )
            await pool.release(conn)
            ps = [float(r["raw"]) for r in rows]
            ys = [1.0 if r["d"] == "rise" else 0.0 for r in rows]
            cal = Calibrator.fit(ps, ys)
            await TrustStore(pool).save_map(cal)
        finally:
            await pool.close()

        data = {"n_fit": cal.n_fit, "knots": len(cal.knots), "base_rate": round(cal.base_rate, 4)}
        click.echo(json.dumps(data) if json_out else
                   f"Fitted calibration map: n={cal.n_fit}, knots={len(cal.knots)}, "
                   f"base_rate={cal.base_rate:.3f}"
                   + ("" if cal.knots else "  [identity — thin/low-variance data]"))

    asyncio.run(_run())


@trust_group.command("scores")
@click.option("--persist", is_flag=True, help="Upsert trust_scores rows.")
@click.option("--json-out", is_flag=True)
def cmd_scores(persist: bool, json_out: bool) -> None:
    """Compute per-model and per-source trust scores."""
    import asyncio

    from aegis.trust.scores import TrustScorer
    from aegis.trust.store import TrustStore

    async def _run() -> None:
        pool = await _pool()
        try:
            scorer = TrustScorer(pool)
            model = await scorer.model_trust("heuristic")
            sources = await scorer.source_trust()
            if persist:
                store = TrustStore(pool)
                await store.save_trust(model)
                for sc in sources:
                    await store.save_trust(sc)
        finally:
            await pool.close()

        all_scores = [model, *sources]
        if json_out:
            click.echo(json.dumps([s.model_dump(mode="json") for s in all_scores], default=str))
        else:
            for s in all_scores:
                click.echo(
                    f"  [{s.entity_kind}] {s.entity_id:<20} trust={s.trust:.3f} "
                    f"(n={s.n_outcomes}, ece={s.ece if s.ece is None else round(s.ece,3)})"
                )

    asyncio.run(_run())
