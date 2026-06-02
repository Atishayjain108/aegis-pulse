"""
Phase 7 CLI — `aegis geo` command group.

Commands:
  aegis geo analyze <sku> <title> --category <cat>   — find geo arb opportunities
  aegis geo fx                                        — show live FX rates
  aegis geo tariff <hs-code> <dest>                  — duty rate lookup
  aegis geo shipping <origin> <dest>                 — shipping cost quote
  aegis geo regions                                  — list supported regions
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import click


@click.group("geo")
def geo_group() -> None:
    """Phase 7 — Geospatial Intelligence & Cross-Market Arbitrage."""


@geo_group.command("analyze")
@click.argument("sku")
@click.argument("title")
@click.option("--category", "-c", default="general", show_default=True,
              help="Product category (apparel/electronics/beauty/…)")
@click.option("--top-n", default=5, show_default=True, type=int,
              help="Number of top opportunities to display")
@click.option("--json-out", is_flag=True, help="Output raw JSON")
@click.option("--tenant-id", default="00000000-0000-0000-0000-000000000001",
              help="Tenant UUID")
def analyze_cmd(
    sku: str,
    title: str,
    category: str,
    top_n: int,
    json_out: bool,
    tenant_id: str,
) -> None:
    """Find cross-market arbitrage opportunities for PRODUCT_SKU."""

    async def _run() -> None:
        from aegis.geo.arbitrage import CrossMarketAnalyzer

        analyzer = CrossMarketAnalyzer()
        report = await analyzer.find_opportunities(
            sku, title, category, tenant_id=tenant_id, top_n=top_n
        )

        if json_out:
            click.echo(json.dumps(report.model_dump(mode="json"), indent=2))
            return

        click.echo()
        click.echo(click.style(f"  Geo Arbitrage: {title} ({sku})", bold=True))
        click.echo(f"  Category: {category}  ·  HS Code: {report.all_opportunities[0].hs_code if report.all_opportunities else 'n/a'}")
        click.echo(f"  Pairs evaluated  : all {8*7} origin→destination combinations")
        click.echo(f"  Viable (margin ≥ 5%): {report.opportunities_found}")
        click.echo(f"  Analysis time    : {report.analysis_duration_ms:.0f} ms")
        click.echo()

        if not report.all_opportunities:
            click.echo("  No profitable opportunities found for this product.")
            return

        header = f"  {'ORIGIN':<6} {'DEST':<6} {'DEST PRICE':>10} {'LANDED':>10} {'MARGIN':>9} {'SCORE':>8}  CARRIER"
        sep = "  " + "─" * (len(header) - 2)
        click.echo(header)
        click.echo(sep)

        for opp in report.all_opportunities:
            margin_color = (
                "green" if opp.gross_margin_pct >= 30
                else "yellow" if opp.gross_margin_pct >= 15
                else "white"
            )
            click.echo(
                f"  {opp.origin_region.value:<6} "
                f"{opp.destination_region.value:<6} "
                f"${float(opp.destination_price_usd):>9.2f} "
                f"${float(opp.total_landed_cost_usd):>9.2f} "
                + click.style(f"{float(opp.gross_margin_pct):>8.1f}%", fg=margin_color)
                + f" {opp.opportunity_score:>8.2f}  "
                + opp.metadata.get("shipping_carrier", "")
            )

        click.echo()
        top = report.top_opportunity
        if top:
            click.echo(click.style("  TOP OPPORTUNITY", bold=True))
            click.echo(f"    {top.origin_region.value} → {top.destination_region.value}")
            origin_usd = float(top.origin_price_local) * float(top.fx_rate_used)
            click.echo(f"    Buy  : {top.origin_currency} {top.origin_price_local:.2f} "
                       f"(≈${origin_usd:.2f} USD)")
            click.echo(f"    Sell : {top.destination_currency} {top.destination_price_local:.2f} "
                       f"(${float(top.destination_price_usd):.2f})")
            click.echo(f"    Ship : ${float(top.shipping_cost_usd):.2f}  "
                       f"Duty: ${float(top.duty_cost_usd):.2f}  "
                       f"Fee: ${float(top.platform_fee_usd):.2f}")
            click.echo(f"    Margin: ${float(top.gross_margin_usd):.2f}  "
                       f"({float(top.gross_margin_pct):.1f}%)")
            click.echo(f"    FX rate used: 1 {top.origin_currency} = "
                       f"{float(top.fx_rate_used):.4f} USD")
        click.echo()

    asyncio.run(_run())


@geo_group.command("fx")
@click.option("--json-out", is_flag=True, help="Output raw JSON")
def fx_cmd(json_out: bool) -> None:
    """Show live FX rates (Frankfurter / ECB)."""

    async def _run() -> None:
        from aegis.geo.fx import FXRateFetcher

        fetcher = FXRateFetcher()
        rates = await fetcher.get_all_rates()

        if json_out:
            click.echo(json.dumps({k: float(v) for k, v in rates.items()}, indent=2))
            return

        click.echo()
        click.echo(click.style("  FX Rates vs USD (ECB / Frankfurter)", bold=True))
        click.echo("  Source: api.frankfurter.app")
        click.echo()
        for ccy, rate in sorted(rates.items()):
            if ccy == "USD":
                continue
            click.echo(f"  1 USD = {float(rate):>10.4f}  {ccy}")
        click.echo()

    asyncio.run(_run())


@geo_group.command("tariff")
@click.argument("hs_code")
@click.argument("destination")
@click.option("--value", default=100.0, type=float, show_default=True,
              help="Value of goods in USD")
@click.option("--origin", default="US", show_default=True, help="Origin country code")
@click.option("--json-out", is_flag=True)
def tariff_cmd(hs_code: str, destination: str, value: float, origin: str, json_out: bool) -> None:
    """Look up WTO MFN import duty for HS_CODE into DESTINATION."""

    async def _run() -> None:
        from aegis.geo.tariffs import TariffEstimator

        est = TariffEstimator()
        result = await est.estimate(
            hs_code=hs_code,
            origin=origin.upper(),
            destination=destination.upper(),
            value_usd=Decimal(str(value)),
        )

        if json_out:
            click.echo(json.dumps(result.model_dump(mode="json"), indent=2))
            return

        click.echo()
        click.echo(f"  HS Code    : {result.hs_code}")
        click.echo(f"  Description: {result.description}")
        click.echo(f"  {origin.upper()} → {destination.upper()}")
        click.echo(f"  Duty rate  : {float(result.duty_rate) * 100:.1f}%")
        click.echo(f"  Value      : ${float(result.value_usd):.2f}")
        click.echo(f"  Duty USD   : ${float(result.duty_usd):.2f}")
        click.echo(f"  Source     : {result.source}")
        click.echo()

    asyncio.run(_run())


@geo_group.command("shipping")
@click.argument("origin")
@click.argument("destination")
@click.option("--weight", default=0.5, type=float, show_default=True,
              help="Package weight in kg")
@click.option("--json-out", is_flag=True)
def shipping_cmd(origin: str, destination: str, weight: float, json_out: bool) -> None:
    """Get shipping cost quote for ORIGIN → DESTINATION."""

    async def _run() -> None:
        from aegis.geo.config import Region
        from aegis.geo.shipping import ShippingResolver

        try:
            orig = Region(origin.upper())
            dest = Region(destination.upper())
        except ValueError:
            click.echo(f"Unknown region. Supported: {[r.value for r in Region]}", err=True)
            return

        resolver = ShippingResolver()
        quote = await resolver.get_quote(orig, dest, weight_kg=weight)

        if json_out:
            click.echo(json.dumps({
                "origin": quote.origin.value,
                "destination": quote.destination.value,
                "cost_usd": float(quote.cost_usd),
                "carrier": quote.carrier,
                "transit_days_estimate": quote.transit_days_estimate,
                "source": quote.source,
            }, indent=2))
            return

        click.echo()
        click.echo(f"  {orig.value} → {dest.value}  ({weight} kg)")
        click.echo(f"  Cost    : ${float(quote.cost_usd):.2f} USD")
        click.echo(f"  Carrier : {quote.carrier}")
        click.echo(f"  Transit : ~{quote.transit_days_estimate} days")
        click.echo(f"  Source  : {quote.source}")
        click.echo()

    asyncio.run(_run())


@geo_group.command("regions")
def regions_cmd() -> None:
    """List supported regions and their e-commerce market sizes."""
    from aegis.geo.config import REGION_CONFIGS

    click.echo()
    click.echo(click.style("  Supported Regions (World Bank 2024)", bold=True))
    click.echo()
    header = f"  {'CODE':<6} {'NAME':<22} {'CURRENCY':<10} {'E-COMM MARKET':>14}  {'MARKET SCORE':>12}"
    click.echo(header)
    click.echo("  " + "─" * (len(header) - 2))
    for cfg in REGION_CONFIGS.values():
        click.echo(
            f"  {cfg.code.value:<6} "
            f"{cfg.name:<22} "
            f"{cfg.currency:<10} "
            f"${cfg.ecommerce_market_bn_usd:>12.0f}B  "
            f"{cfg.market_size_score:>12.2f}"
        )
    click.echo()
