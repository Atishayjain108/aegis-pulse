"""
Click CLI for PROJECT OMEGA Phase D — Execution Intelligence.

Registered under the main ``aegis`` CLI as ``aegis exec``.

    aegis exec records    — recent execution records (plan → outcome)
    aegis exec suppliers  — supplier trust scores (UNVERIFIED when unmeasured)
    aegis exec supplier   — one supplier's measured trust score
"""

from __future__ import annotations

import asyncio
import json

import click

from aegis.execution_intel.taxonomy import UNVERIFIED


def _pool():  # noqa: ANN202 — returns asyncpg pool coroutine
    import asyncpg

    from aegis.db.pool import resolve_dsn

    return asyncpg.create_pool(resolve_dsn(), min_size=1, max_size=4)


@click.group("exec")
def exec_group() -> None:
    """Phase D — Execution Intelligence (execution memory · supplier trust)."""


@exec_group.command("records")
@click.option("--limit", default=20, show_default=True)
@click.option("--json-out", is_flag=True)
def cmd_records(limit: int, json_out: bool) -> None:
    """Show recent execution records."""

    async def _run() -> list[dict]:
        from aegis.execution_intel.memory import ExecutionMemory

        pool = await _pool()
        try:
            mem = ExecutionMemory(pool)
            recs = await mem.fetch_recent(limit=limit)
            return [
                {
                    "plan_id": r.plan_id,
                    "trend_id": r.trend_id,
                    "supplier": r.supplier_name,
                    "outcome": r.outcome.value,
                    "pnl_usd": r.realized_pnl_usd,
                    "failure": r.failure_category.value,
                }
                for r in recs
            ]
        finally:
            await pool.close()

    rows = asyncio.run(_run())
    if json_out:
        click.echo(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        click.echo("No execution records yet.")
        return
    for r in rows:
        click.echo(
            f"{r['plan_id'][:8]}  {r['outcome']:<10}  "
            f"supplier={r['supplier'] or '-'}  pnl={r['pnl_usd']}  "
            f"failure={r['failure']}"
        )


@exec_group.command("suppliers")
@click.option("--limit", default=20, show_default=True)
@click.option("--json-out", is_flag=True)
def cmd_suppliers(limit: int, json_out: bool) -> None:
    """Rank suppliers by measured fulfillment trust (verified first)."""

    async def _run() -> list[dict]:
        from aegis.execution_intel.supplier import SupplierIntel

        pool = await _pool()
        try:
            intel = SupplierIntel(pool)
            scores = await intel.top_suppliers(limit=limit)
            return [
                {
                    "supplier": s.supplier_name,
                    "trust": s.trust if s.trust is not None else UNVERIFIED,
                    "verification_rate": s.verification_rate,
                    "n_fulfillments": s.n_fulfillments,
                    "n_verifications": s.n_verifications,
                }
                for s in scores
            ]
        finally:
            await pool.close()

    rows = asyncio.run(_run())
    if json_out:
        click.echo(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        click.echo("No supplier reliability data yet.")
        return
    for r in rows:
        click.echo(
            f"{r['supplier']:<24}  trust={r['trust']}  "
            f"fulfillments={r['n_fulfillments']}  verifications={r['n_verifications']}"
        )


@exec_group.command("supplier")
@click.argument("name")
@click.option("--json-out", is_flag=True)
def cmd_supplier(name: str, json_out: bool) -> None:
    """Show one supplier's measured trust score."""

    async def _run() -> dict:
        from aegis.execution_intel.supplier import SupplierIntel

        pool = await _pool()
        try:
            intel = SupplierIntel(pool)
            s = await intel.trust_score(name)
            return {
                "supplier": s.supplier_name,
                "trust": s.trust if s.trust is not None else UNVERIFIED,
                "verification_rate": s.verification_rate,
                "delay_rate": s.delay_rate,
                "cancellation_rate": s.cancellation_rate,
                "avg_response_ms": s.avg_response_ms,
                "n_fulfillments": s.n_fulfillments,
                "n_verifications": s.n_verifications,
                "is_verified": s.is_verified,
            }
        finally:
            await pool.close()

    result = asyncio.run(_run())
    click.echo(json.dumps(result, indent=2, default=str))


@exec_group.command("demand")
@click.argument("region")
@click.argument("category")
@click.option("--json-out", is_flag=True)
def cmd_demand(region: str, category: str, json_out: bool) -> None:
    """Show the demand PROXY for a region/category (buyer trust is UNVERIFIED)."""

    async def _run() -> dict:
        from aegis.execution_intel.buyer import BuyerIntel

        pool = await _pool()
        try:
            intel = BuyerIntel(pool)
            p = await intel.demand_proxy(region, category)
            return {
                "region": p.region,
                "category": p.category,
                "demand_intensity": p.demand_intensity,
                "demand_is_proxy": p.demand_is_proxy,
                "buyer_trust": p.buyer_trust if p.buyer_trust is not None else UNVERIFIED,
                "buyer_is_verified": p.buyer_is_verified,
                "n_demand_observations": p.n_demand_observations,
                "n_orders": p.n_orders,
            }
        finally:
            await pool.close()

    click.echo(json.dumps(asyncio.run(_run()), indent=2, default=str))


@exec_group.command("simulate")
@click.argument("plan_id")
@click.option("--supplier", default=None)
@click.option("--region", default="GLOBAL", show_default=True)
@click.option("--category", default="general", show_default=True)
@click.option("--compliance-risk", type=float, default=None)
@click.option("--json-out", is_flag=True)
def cmd_simulate(
    plan_id: str, supplier: str | None, region: str,
    category: str, compliance_risk: float | None, json_out: bool,
) -> None:
    """Run an advisory Execution Survivability simulation for a plan (Rule 5)."""

    async def _run() -> dict:
        from aegis.execution_intel.simulator import ExecutionSimulator

        pool = await _pool()
        try:
            sim = ExecutionSimulator(pool)
            s = await sim.simulate(
                plan_id,
                supplier_name=supplier,
                region=region,
                category=category,
                compliance_risk=compliance_risk,
            )
            return {
                "plan_id": s.plan_id,
                "overall": s.overall if s.overall is not None else UNVERIFIED,
                "abstained": s.abstained,
                "reason": s.reason,
                "mode_survival": s.mode_survival,
                "basis": s.basis,
                "is_verified": s.is_verified,
            }
        finally:
            await pool.close()

    click.echo(json.dumps(asyncio.run(_run()), indent=2, default=str))


@exec_group.command("score")
@click.argument("plan_id")
@click.option("--supplier", default=None)
@click.option("--region", default="GLOBAL", show_default=True)
@click.option("--category", default="general", show_default=True)
@click.option("--risk", type=float, default=None)
@click.option("--confidence", type=float, default=None)
@click.option("--evidence", type=float, default=None)
@click.option("--compliance-risk", type=float, default=None)
def cmd_score(
    plan_id: str, supplier: str | None, region: str, category: str,
    risk: float | None, confidence: float | None, evidence: float | None,
    compliance_risk: float | None,
) -> None:
    """Build the six-axis ExecutionScoreVector (Rule 6 — metrics never merged)."""

    async def _run() -> dict:
        from aegis.execution_intel.scoring import ScoreVectorBuilder

        pool = await _pool()
        try:
            v = await ScoreVectorBuilder(pool).build(
                plan_id, supplier_name=supplier, region=region, category=category,
                risk=risk, confidence=confidence, evidence=evidence,
                compliance_risk=compliance_risk,
            )
            return {
                "plan_id": v.plan_id,
                "risk": v.risk if v.risk is not None else UNVERIFIED,
                "confidence": v.confidence if v.confidence is not None else UNVERIFIED,
                "trust": v.trust if v.trust is not None else UNVERIFIED,
                "evidence": v.evidence if v.evidence is not None else UNVERIFIED,
                "execution": v.execution if v.execution is not None else UNVERIFIED,
                "survivability": (
                    v.survivability if v.survivability is not None else UNVERIFIED
                ),
                "source": v.source,
                "verified_metrics": v.verified_metrics,
            }
        finally:
            await pool.close()

    click.echo(json.dumps(asyncio.run(_run()), indent=2, default=str))


@exec_group.command("forecast-accuracy")
@click.option("--json-out", is_flag=True)
def cmd_forecast_accuracy(json_out: bool) -> None:
    """Measure stored failure forecasts vs settled outcomes (Rule 8)."""

    async def _run() -> dict:
        from aegis.execution_intel.forecast import FailureForecaster

        pool = await _pool()
        try:
            a = await FailureForecaster(pool).measure_accuracy()
            return {
                "n": a.n,
                "base_rate": a.base_rate,
                "brier": a.brier,
                "brier_skill": a.brier_skill,
            }
        finally:
            await pool.close()

    click.echo(json.dumps(asyncio.run(_run()), indent=2, default=str))


@exec_group.command("audit")
@click.option("--days", default=7, show_default=True)
@click.option("--json-out", is_flag=True)
def cmd_audit(days: int, json_out: bool) -> None:
    """Weekly execution self-audit: best/worst plans, suppliers, failures (Rule 10)."""

    async def _run() -> dict:
        from aegis.execution_intel.audit import ExecutionAuditor

        pool = await _pool()
        try:
            a = await ExecutionAuditor(pool).weekly_report(period_days=days)
            return {
                "period_days": a.period_days,
                "n_settled": a.n_settled,
                "best_plans": a.best_plans,
                "worst_plans": a.worst_plans,
                "most_reliable_suppliers": a.most_reliable_suppliers,
                "most_demanded_markets": a.most_demanded_markets,
                "common_failure_causes": a.common_failure_causes,
            }
        finally:
            await pool.close()

    click.echo(json.dumps(asyncio.run(_run()), indent=2, default=str))


@exec_group.command("arbitrage")
@click.argument("opportunity_type", default="product")
def cmd_arbitrage(opportunity_type: str) -> None:
    """Show the three arbitrage questions for an opportunity type (Rule 7)."""
    from aegis.execution_intel.audit import arbitrage_rationale

    r = arbitrage_rationale(opportunity_type)
    click.echo(
        json.dumps(
            {
                "opportunity_type": r.opportunity_type,
                "why_exists": r.why_exists,
                "why_not_captured": r.why_not_captured,
                "what_destroys_it": r.what_destroys_it,
                "verified": r.verified,
            },
            indent=2,
        )
    )


__all__ = ["exec_group"]
