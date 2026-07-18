"""
Phase 8 CLI — `aegis compliance` command group.

Commands:
  aegis compliance assess <sku> <title>           — full compliance assessment
  aegis compliance trademark <query>              — trademark lookup
  aegis compliance sanctions <country>            — OFAC/FATF check
  aegis compliance ftc                            — FTC advertising rule check
  aegis compliance batch <json-file>              — bulk assessment from JSON file
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import click


@click.group("compliance")
def compliance_group() -> None:
    """Phase 8 — Regulatory & Compliance Engine."""


@compliance_group.command("assess")
@click.argument("sku")
@click.argument("title")
@click.option("--description", "-d", default="", help="Product description")
@click.option("--category", "-c", default="general", show_default=True, help="Product category")
@click.option("--origin", default="US", show_default=True, help="Origin country (ISO-2)")
@click.option("--dest", default="US", show_default=True, help="Destination country (ISO-2)")
@click.option("--price", type=float, default=None, help="Price in USD")
@click.option("--image-url", default=None, help="Product image URL (for CLIP check)")
@click.option("--json-out", is_flag=True, help="Output raw JSON")
def assess_cmd(
    sku: str,
    title: str,
    description: str,
    category: str,
    origin: str,
    dest: str,
    price: float | None,
    image_url: str | None,
    json_out: bool,
) -> None:
    """Run a full compliance risk assessment for a product + trade route."""

    async def _run() -> None:
        from aegis.compliance.engine import ComplianceEngine
        from aegis.compliance.schemas import ComplianceRequest

        engine = ComplianceEngine()
        req = ComplianceRequest(
            product_sku=sku,
            product_title=title,
            product_description=description,
            product_image_url=image_url,
            category=category,
            origin_country=origin.upper(),
            destination_country=dest.upper(),
            price_usd=price,
        )
        result = await engine.assess(req)

        if json_out:
            click.echo(json.dumps(result.model_dump(mode="json"), indent=2))
            return

        _print_assessment(result)

    asyncio.run(_run())


@compliance_group.command("trademark")
@click.argument("query")
@click.option("--description", "-d", default="", help="Product description (improves search)")
@click.option("--json-out", is_flag=True)
def trademark_cmd(query: str, description: str, json_out: bool) -> None:
    """Search for trademark registrations matching QUERY (USPTO + EUIPO)."""

    async def _run() -> None:
        from aegis.compliance.ipr import IPRChecker

        checker = IPRChecker()
        matches, risk = await checker.check_trademark(query, description)

        if json_out:
            click.echo(json.dumps({
                "query": query,
                "trademark_risk": risk,
                "matches": [m.model_dump() for m in matches],
            }, indent=2))
            return

        click.echo()
        click.echo(click.style(f"  Trademark Search: {query}", bold=True))
        click.echo(f"  Risk score: {risk:.0%}  ·  Matches found: {len(matches)}")
        click.echo()

        if not matches:
            click.echo("  No trademark matches found.")
        else:
            header = f"  {'MARK':<30} {'OWNER':<28} {'STATUS':<12} {'CONF':>6}  SOURCE"
            click.echo(header)
            click.echo("  " + "─" * (len(header) - 2))
            for m in matches:
                conf_color = "red" if m.confidence_score > 0.8 else "yellow" if m.confidence_score > 0.5 else "white"
                click.echo(
                    f"  {m.registered_mark:<30.28} "
                    f"{m.owner:<28.26} "
                    f"{m.status:<12} "
                    + click.style(f"{m.confidence_score:>5.0%}", fg=conf_color)
                    + f"  {m.source}"
                )
        click.echo()

    asyncio.run(_run())


@compliance_group.command("sanctions")
@click.argument("country")
@click.option("--json-out", is_flag=True)
def sanctions_cmd(country: str, json_out: bool) -> None:
    """Check if COUNTRY (ISO-2) is OFAC-sanctioned or FATF high-risk."""
    from aegis.compliance.aml import _OFAC_COUNTRY_PROGRAM
    from aegis.compliance.constants import FATF_HIGH_RISK, OFAC_SANCTIONED_COUNTRIES

    code = country.upper()
    is_sanctioned = code in OFAC_SANCTIONED_COUNTRIES
    is_fatf = code in FATF_HIGH_RISK
    program = _OFAC_COUNTRY_PROGRAM.get(code, "") if is_sanctioned else ""
    risk = 0.99 if is_sanctioned else (0.50 if is_fatf else 0.02)

    if json_out:
        click.echo(json.dumps({
            "country": code,
            "is_sanctioned": is_sanctioned,
            "is_fatf_high_risk": is_fatf,
            "sanction_program": program,
            "risk_score": risk,
        }, indent=2))
        return

    click.echo()
    click.echo(click.style(f"  Sanctions Check: {code}", bold=True))
    sanction_label = click.style("SANCTIONED", fg="red", bold=True) if is_sanctioned else click.style("CLEAR", fg="green")
    fatf_label = click.style("HIGH-RISK", fg="yellow") if is_fatf else click.style("CLEAR", fg="green")
    click.echo(f"  OFAC Status : {sanction_label}  {program}")
    click.echo(f"  FATF Status : {fatf_label}")
    click.echo(f"  Risk Score  : {risk:.0%}")
    click.echo()


@compliance_group.command("ftc")
@click.option("--title", "-t", required=True, help="Product title")
@click.option("--description", "-d", default="", help="Product description")
@click.option("--json-out", is_flag=True)
def ftc_cmd(title: str, description: str, json_out: bool) -> None:
    """Run FTC advertising rule engine against product title + description."""
    from aegis.compliance.ftc import FTCRuleEngine

    engine = FTCRuleEngine()
    violations, risk = engine.assess(title, description)

    if json_out:
        click.echo(json.dumps({
            "ftc_risk": risk,
            "violations": [v.model_dump() for v in violations],
        }, indent=2))
        return

    click.echo()
    click.echo(click.style(f"  FTC Rule Check: {title[:60]}", bold=True))
    click.echo(f"  Risk score: {risk:.0%}")
    click.echo()

    if not violations:
        click.echo(click.style("  ✓  No FTC violations detected.", fg="green"))
    else:
        click.echo(click.style(f"  ✗  {len(violations)} violation(s) found:", fg="red", bold=True))
        for v in violations:
            sev_color = "red" if v.severity >= 0.7 else "yellow"
            click.echo(
                f"  [{click.style(f'{v.severity:.0%}', fg=sev_color)}] "
                f"{v.violation_type}\n"
                f'     Match: "{v.matched_text}"\n'
                f"     Rule:  {v.rule_reference}"
            )
    click.echo()


@compliance_group.command("batch")
@click.argument("json_file", type=click.Path(exists=True))
@click.option("--json-out", is_flag=True)
def batch_cmd(json_file: str, json_out: bool) -> None:
    """Run compliance assessment on a batch of products from JSON_FILE.

    JSON_FILE must be a list of objects with fields:
      product_sku, product_title, [product_description], [category],
      [origin_country], [destination_country], [price_usd]
    """
    from pathlib import Path

    with Path(json_file).open() as f:
        items = json.load(f)

    async def _run() -> None:
        from aegis.compliance.engine import ComplianceEngine
        from aegis.compliance.schemas import ComplianceRequest, Recommendation

        if not isinstance(items, list):
            click.echo("Error: JSON file must contain a list of product objects.", err=True)
            sys.exit(1)

        engine = ComplianceEngine()
        requests = [
            ComplianceRequest(
                product_sku=item.get("product_sku", f"SKU-{i:04d}"),
                product_title=item.get("product_title", ""),
                product_description=item.get("product_description", ""),
                category=item.get("category", "general"),
                origin_country=item.get("origin_country", "US").upper(),
                destination_country=item.get("destination_country", "US").upper(),
                price_usd=item.get("price_usd"),
            )
            for i, item in enumerate(items)
        ]

        results = await engine.assess_many(requests)

        if json_out:
            click.echo(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
            return

        click.echo()
        click.echo(click.style(f"  Compliance Batch Assessment — {len(results)} products", bold=True))
        click.echo()
        header = f"  {'SKU':<20} {'TITLE':<36} {'RISK':>6}  REC"
        click.echo(header)
        click.echo("  " + "─" * (len(header) - 2))
        for r in results:
            rec_color = "red" if r.recommendation == Recommendation.BLOCK else \
                        "yellow" if r.recommendation == Recommendation.ESCALATE else "green"
            click.echo(
                f"  {r.product_sku:<20.18} "
                f"{r.product_sku:<36.34} "
                + click.style(f"{r.overall_risk_score:>5.0%}", fg=rec_color)
                + f"  {r.recommendation.value}"
            )
        click.echo()
        blocks = sum(1 for r in results if r.recommendation == Recommendation.BLOCK)
        escalates = sum(1 for r in results if r.recommendation == Recommendation.ESCALATE)
        proceeds = sum(1 for r in results if r.recommendation == Recommendation.PROCEED)
        click.echo(f"  Summary: {proceeds} PROCEED · {escalates} ESCALATE · {blocks} BLOCK")
        click.echo()

    asyncio.run(_run())


def _print_assessment(result: Any) -> None:
    from aegis.compliance.schemas import Recommendation as Rec

    rec_color = {
        Rec.BLOCK: "red",
        Rec.ESCALATE: "yellow",
        Rec.PROCEED: "green",
    }.get(result.recommendation, "white")

    click.echo()
    click.echo(click.style(f"  Compliance Assessment: {result.product_sku}", bold=True))
    click.echo(
        "  Recommendation: "
        + click.style(result.recommendation.value, fg=rec_color, bold=True)
        + f"   Overall risk: {result.overall_risk_score:.0%}"
        + f"  ({result.duration_ms:.0f} ms)"
    )
    click.echo()

    if result.reasons:
        click.echo(click.style("  Risk Factors:", bold=True))
        for reason in result.reasons:
            click.echo(f"    • {reason[:120]}")
        click.echo()

    # Risk breakdown table
    b = result.risk_breakdown
    click.echo(click.style("  Risk Breakdown:", bold=True))
    dims = [
        ("Trademark",   b.trademark),
        ("Patent",      b.patent),
        ("FDA",         b.fda),
        ("Counterfeit", b.counterfeit),
        ("FTC",         b.ftc),
        ("Privacy",     b.privacy),
        ("AML",         b.aml),
    ]
    for name, score in dims:
        bar_len = int(score * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        color = "red" if score >= 0.7 else "yellow" if score >= 0.4 else "green"
        click.echo(
            f"    {name:<12} "
            + click.style(bar, fg=color)
            + f"  {score:.0%}"
        )
    click.echo()
