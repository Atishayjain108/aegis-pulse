"""
Click CLI for Phase 9 Autonomous Self-Evolution.

Registered under the main ``aegis`` CLI as ``aegis evolve``.

Commands:
    aegis evolve status         — print aggregated evolve health
    aegis evolve retrain        — trigger a manual retraining run
    aegis evolve drift          — run a drift check on recent outcomes
    aegis evolve policy         — print current RL policy weights
    aegis evolve outcomes       — count / list recent trade outcomes
    aegis evolve record         — record a single trade outcome from CLI args
"""

from __future__ import annotations

import json
import sys

import click
import structlog

_log = structlog.get_logger("aegis.evolve.cli")


@click.group("evolve")
def evolve_group() -> None:
    """Phase 9 — Autonomous Self-Evolution (retraining · drift · RL policy)."""


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@evolve_group.command("status")
@click.option("--json-out", is_flag=True, help="Emit raw JSON.")
def cmd_status(json_out: bool) -> None:
    """Print aggregated Phase 9 health snapshot."""
    import asyncio

    from aegis.evolve.config import EvolveSettings
    from aegis.evolve.drift import DriftDetector
    from aegis.evolve.retrain import RetrainingPipeline
    from aegis.evolve.rl_policy import OnlinePricingPolicy

    async def _run() -> None:
        cfg = EvolveSettings()
        pipeline = RetrainingPipeline(settings=cfg)
        detector = DriftDetector(settings=cfg)
        policy = OnlinePricingPolicy(learning_rate=cfg.rl_learning_rate, settings=cfg)

        champion_auc = await pipeline.get_champion_auc()
        runs = await pipeline.fetch_recent_runs(limit=1)
        snap = await detector.fetch_latest_snapshot()

        data = {
            "champion_auc": round(champion_auc, 4),
            "last_retrain_status": runs[0].status if runs else None,
            "last_retrain_at": runs[0].started_at.isoformat() if runs else None,
            "drift_score": round(snap.drift_score, 4) if snap else None,
            "is_drifted": snap.is_drifted if snap else False,
            "policy_weights": {
                "cost_based": round(policy.get_weights()[0], 4),
                "demand_based": round(policy.get_weights()[1], 4),
                "inventory_based": round(policy.get_weights()[2], 4),
                "competitor_based": round(policy.get_weights()[3], 4),
            },
        }

        if json_out:
            click.echo(json.dumps(data, indent=2))
        else:
            click.echo(f"\n{'─'*60}")
            click.echo("  AEGIS Phase 9 — Evolve Status")
            click.echo(f"{'─'*60}")
            click.echo(f"  Champion AUC       : {data['champion_auc']}")
            click.echo(f"  Last retrain       : {data['last_retrain_status']} ({data['last_retrain_at']})")
            drift = data['drift_score']
            drifted_flag = " ⚠  DRIFTED" if data['is_drifted'] else ""
            click.echo(f"  Drift score        : {drift}{drifted_flag}")
            click.echo("  RL policy weights  :")
            for k, v in data["policy_weights"].items():
                click.echo(f"    {k:<24}: {v:.4f}")
            click.echo(f"{'─'*60}\n")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# retrain
# ---------------------------------------------------------------------------

@evolve_group.command("retrain")
@click.option("--json-out", is_flag=True)
def cmd_retrain(json_out: bool) -> None:
    """Trigger a manual model retraining run."""
    import asyncio

    from aegis.evolve.config import EvolveSettings
    from aegis.evolve.retrain import RetrainingPipeline

    async def _run() -> None:
        cfg = EvolveSettings()
        pipeline = RetrainingPipeline(settings=cfg)
        click.echo("Starting manual retraining pipeline…")
        run = await pipeline.run_weekly_retrain(triggered_by="manual")

        if json_out:
            click.echo(json.dumps(run.model_dump(mode="json"), indent=2, default=str))
        else:
            click.echo(f"\nRetrain run ID   : {run.run_id}")
            click.echo(f"Status           : {run.status}")
            click.echo(f"Outcomes used    : {run.outcomes_count}")
            if run.improvement_pct is not None:
                click.echo(f"AUC improvement  : +{run.improvement_pct:.2f}%")
            if run.champion_after and run.champion_after != run.champion_before:
                click.echo(f"New champion     : {run.champion_after}")
            if run.error_message:
                click.echo(f"Error            : {run.error_message}", err=True)

        if run.status == "failed":
            sys.exit(1)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# drift
# ---------------------------------------------------------------------------

@evolve_group.command("drift")
@click.option("--json-out", is_flag=True)
def cmd_drift(json_out: bool) -> None:
    """Run a drift check on recent prediction outcomes."""
    import asyncio

    import numpy as np

    from aegis.evolve.config import EvolveSettings
    from aegis.evolve.constants import EVOLVE_FEATURE_DIM
    from aegis.evolve.drift import DriftDetector

    async def _run() -> None:
        cfg = EvolveSettings()
        detector = DriftDetector(settings=cfg)

        # Build a lightweight feature matrix from recent prediction scores
        # Use zero vector as proxy when no DB is available
        recent_features = np.zeros((10, EVOLVE_FEATURE_DIM))

        snap = await detector.run_all_checks(recent_features)

        if json_out:
            click.echo(json.dumps(snap.model_dump(mode="json"), indent=2, default=str))
        else:
            click.echo(f"\nDrift score : {snap.drift_score:.4f}")
            click.echo(f"Is drifted  : {snap.is_drifted}")
            if snap.precision_now is not None:
                click.echo(f"Precision↓  : {snap.precision_prev:.3f} → {snap.precision_now:.3f} (drop {snap.precision_drop:.3f})")
            if snap.should_rollback:
                click.echo("⚠  Auto-rollback recommended!", err=True)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------

@evolve_group.command("policy")
@click.option("--json-out", is_flag=True)
def cmd_policy(json_out: bool) -> None:
    """Show the current RL pricing policy weights."""
    from aegis.evolve.config import EvolveSettings
    from aegis.evolve.constants import POLICY_WEIGHT_LABELS
    from aegis.evolve.rl_policy import OnlinePricingPolicy

    cfg = EvolveSettings()
    policy = OnlinePricingPolicy(learning_rate=cfg.rl_learning_rate, settings=cfg)
    state = policy.get_state()

    if json_out:
        click.echo(json.dumps(state.model_dump(mode="json"), indent=2, default=str))
    else:
        click.echo(f"\nPolicy ID    : {state.policy_id}")
        click.echo(f"Updates      : {state.update_count}")
        click.echo(f"Learning rate: {state.learning_rate}")
        click.echo("Weights      :")
        for label, w in zip(POLICY_WEIGHT_LABELS, state.weights, strict=False):
            bar = "█" * int(w * 40)
            click.echo(f"  {label:<24} {w:.4f}  {bar}")
        click.echo("")


# ---------------------------------------------------------------------------
# outcomes
# ---------------------------------------------------------------------------

@evolve_group.command("outcomes")
@click.option("--days", default=30, show_default=True, help="Look-back window in days.")
@click.option("--json-out", is_flag=True)
def cmd_outcomes(days: int, json_out: bool) -> None:
    """Count recent trade outcomes available for retraining."""
    import asyncio


    async def _run() -> None:
        try:
            from aegis.db.pool import get_shared_pool
            pool = get_shared_pool()
            if pool is None:
                raise RuntimeError("no pool")
        except Exception:
            data = {"count": 0, "days_back": days, "note": "No database connection"}
            click.echo(json.dumps(data) if json_out else f"Outcomes (last {days}d): 0  [no DB]")
            return

        from aegis.evolve.outcomes import OutcomeRecorder
        n = await OutcomeRecorder(pool).count_recent_outcomes(days_back=days)
        data = {"count": n, "days_back": days}
        if json_out:
            click.echo(json.dumps(data))
        else:
            click.echo(f"Outcomes in last {days} days: {n}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

@evolve_group.command("record")
@click.option("--plan-id", required=True, help="execution_plan_id from Phase 6.")
@click.option("--trend-id", required=True, help="trend_id from Phase 2.")
@click.option("--score", type=float, required=True, help="Original prediction score (0–1).")
@click.option("--confidence", type=float, required=True, help="Original prediction confidence (0–1).")
@click.option("--roi", type=float, default=0.0, help="Actual ROI percentage.")
@click.option("--pnl", type=float, default=0.0, help="Profit/loss in USD.")
@click.option("--units", type=int, default=0, help="Units sold.")
@click.option("--status", "resolution_status", default="successful",
              type=click.Choice(["successful", "partial_refund", "full_refund", "dispute", "pending"]))
@click.option("--notes", default="", help="Resolution notes.")
def cmd_record(
    plan_id: str, trend_id: str, score: float, confidence: float,
    roi: float, pnl: float, units: int, resolution_status: str, notes: str,
) -> None:
    """Record a trade outcome for retraining (requires DB)."""
    import asyncio
    from decimal import Decimal

    from aegis.evolve.outcomes import OutcomeRecorder
    from aegis.evolve.schemas import TradeOutcome

    async def _run() -> None:
        try:
            from aegis.db.pool import get_shared_pool
            pool = get_shared_pool()
            if pool is None:
                click.echo("Error: no database connection", err=True)
                sys.exit(1)
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

        outcome = TradeOutcome(
            execution_plan_id=plan_id,
            trend_id=trend_id,
            prediction_score=score,
            prediction_confidence=confidence,
            actual_roi_pct=Decimal(str(roi)),
            pnl_usd=Decimal(str(pnl)),
            units_sold=units,
            resolution_status=resolution_status,
            resolution_notes=notes,
        )
        ok = await OutcomeRecorder(pool).record_outcome(outcome)
        if ok:
            click.echo(f"Recorded outcome {outcome.outcome_id}")
        else:
            click.echo("Failed to record outcome", err=True)
            sys.exit(1)

    asyncio.run(_run())
