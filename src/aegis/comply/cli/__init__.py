"""``aegis-comply`` command-line interface.

A thin, dependency-light Typer app over the deterministic engine. Every command
works with zero network and zero API keys. ``--json`` flags emit machine-
readable output for piping into the rest of AEGIS.

Commands:
    check    Evaluate a listing (inline flags or a JSON file) -> verdict.
    rules    List loaded rules, optionally filtered by jurisdiction.
    brands   List protected trademark brands the screener knows.
    doctor   Report which optional integrations are available.
    version  Print the engine version.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import typer
except Exception as exc:  # pragma: no cover - typer is a declared dependency
    raise SystemExit(
        "The 'typer' package is required for the aegis-comply CLI. "
        "Install with: pip install typer"
    ) from exc

from aegis.comply import VERSION
from aegis.comply.engine import ComplianceEngine
from aegis.comply.rules.loader import default_registry
from aegis.comply.schemas import ComplianceRequest, ComplianceVerdictResult, Jurisdiction
from aegis.comply.trademark.brands import PROTECTED_BRANDS

app = typer.Typer(
    name="aegis-comply",
    help="AEGIS Pulse Phase 8 — deterministic regulatory & compliance engine.",
    no_args_is_help=True,
    add_completion=False,
)


def _result_to_dict(result) -> dict:
    return {
        "trend_id": result.trend_id,
        "verdict": result.verdict.value,
        "risk_score": result.risk_score,
        "confidence": result.confidence,
        "content_id": result.content_id,
        "blocking_reasons": list(result.blocking_reasons),
        "rule_hits": [
            {
                "rule_id": h.rule_id,
                "name": h.name,
                "severity": h.severity.value,
                "category": h.category.value,
                "jurisdiction": h.jurisdiction.value,
            }
            for h in result.rule_hits
        ],
        "trademark_matches": [
            {"mark": m.mark, "owner": m.owner, "similarity": m.similarity}
            for m in result.trademark_matches
        ],
        "counterfeit_signals": [
            {"brand": s.brand, "risk": s.risk, "reason": s.reason}
            for s in result.counterfeit_signals
        ],
        "remediation": list(result.remediation),
        "reasoning": result.reasoning,
        "engine_version": result.engine_version,
    }


def _print_human(result) -> None:
    color = {"clear": typer.colors.GREEN, "flag": typer.colors.YELLOW, "block": typer.colors.RED}
    typer.echo(typer.style(f"  {result.verdict.value.upper()}", fg=color.get(result.verdict.value), bold=True), nl=False)
    typer.echo(f"   risk={result.risk_score:.3f}  confidence={result.confidence:.2f}")
    typer.echo(f"  trend_id : {result.trend_id}")
    typer.echo(f"  content  : {result.content_id[:16]}…")
    if result.blocking_reasons:
        typer.echo(typer.style("  blocking reasons:", bold=True))
        for r in result.blocking_reasons:
            typer.echo(f"    • {r}")
    if result.rule_hits:
        typer.echo(typer.style("  rule hits:", bold=True))
        for h in result.rule_hits:
            typer.echo(f"    • [{h.severity.value}] {h.rule_id} — {h.name} ({h.jurisdiction.value})")
    if result.trademark_matches:
        typer.echo(typer.style("  trademark matches:", bold=True))
        for m in result.trademark_matches:
            typer.echo(f"    • {m.mark} ({m.owner}) sim={m.similarity:.2f}")
    if result.counterfeit_signals:
        typer.echo(typer.style("  counterfeit signals:", bold=True))
        for s in result.counterfeit_signals:
            typer.echo(f"    • {s.brand} risk={s.risk:.2f} — {s.reason}")
    if result.remediation:
        typer.echo(typer.style("  remediation:", bold=True))
        for r in result.remediation:
            typer.echo(f"    • {r}")


@app.command()
def check(
    title: str = typer.Option("", "--title", "-t", help="Listing title."),
    description: str = typer.Option("", "--description", "-d", help="Listing description."),
    category: str = typer.Option("general", "--category", "-c"),
    audience: str = typer.Option("general", "--audience", "-a", help="general|children|adult"),
    price: float | None = typer.Option(None, "--price", "-p"),
    claim: list[str] = typer.Option([], "--claim", help="A marketing claim (repeatable)."),
    brand: list[str] = typer.Option([], "--brand", help="A brand mention (repeatable)."),
    jurisdiction: list[str] = typer.Option(["US"], "--jurisdiction", "-j", help="Repeatable."),
    trend_id: str = typer.Option("cli-check", "--id"),
    file: Path | None = typer.Option(None, "--file", "-f", help="JSON request file (overrides flags)."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a human report."),
) -> None:
    """Evaluate a single listing and print its compliance verdict."""
    if file is not None:
        try:
            data = json.loads(Path(file).read_text(encoding="utf-8"))
        except Exception as exc:
            typer.echo(f"error: cannot read JSON request: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        request = ComplianceRequest.model_validate(data)
    else:
        try:
            jurs = tuple(Jurisdiction(j.upper()) for j in jurisdiction)
        except ValueError as exc:
            typer.echo(f"error: bad jurisdiction: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        request = ComplianceRequest(
            trend_id=trend_id,
            title=title,
            description=description,
            category=category,
            audience=audience,
            price=price,
            claims=tuple(claim),
            brand_mentions=tuple(brand),
            target_jurisdictions=jurs,
        )

    engine = ComplianceEngine()
    result = engine.evaluate(request)

    if as_json:
        typer.echo(json.dumps(_result_to_dict(result), indent=2))
    else:
        _print_human(result)

    # Non-zero exit on BLOCK so the CLI is usable as a CI/pipeline gate.
    if result.verdict.value == "block":
        raise typer.Exit(code=1)


@app.command()
def rules(
    jurisdiction: str | None = typer.Option(None, "--jurisdiction", "-j"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List the rules currently loaded into the engine."""
    registry = default_registry()
    rows = []
    for rule in registry.all_rules:
        if jurisdiction and rule.jurisdiction.value.upper() != jurisdiction.upper():
            continue
        rows.append(
            {
                "rule_id": rule.id,
                "name": rule.name,
                "severity": rule.severity.value,
                "category": rule.category.value,
                "jurisdiction": rule.jurisdiction.value,
            }
        )
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    typer.echo(typer.style(f"{len(rows)} rule(s) loaded", bold=True))
    for r in rows:
        typer.echo(f"  [{r['severity']:<5}] {r['rule_id']:<26} {r['jurisdiction']:<6} {r['name']}")


@app.command()
def brands(as_json: bool = typer.Option(False, "--json")) -> None:
    """List the protected trademark brands the screener recognises."""
    rows = [
        {
            "mark": k,
            "owner": v[0],
            "jurisdiction": v[1].value if hasattr(v[1], "value") else str(v[1]),
            "category": v[2],
        }
        for k, v in sorted(PROTECTED_BRANDS.items())
    ]
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    typer.echo(typer.style(f"{len(rows)} protected brand(s)", bold=True))
    for r in rows:
        typer.echo(f"  {r['mark']:<18} {r['owner']:<22} {r['jurisdiction']:<6} {r['category']}")


@app.command()
def doctor(as_json: bool = typer.Option(False, "--json")) -> None:
    """Report which optional integrations are available in this environment."""
    checks: dict[str, bool] = {}
    for name, mod in [
        ("pydantic", "pydantic"),
        ("pyyaml", "yaml"),
        ("pydantic_settings", "pydantic_settings"),
        ("structlog", "structlog"),
        ("typer", "typer"),
        ("httpx (live trademark)", "httpx"),
        ("fastapi (api router)", "fastapi"),
        ("asyncpg (audit store)", "asyncpg"),
    ]:
        try:
            __import__(mod)
            checks[name] = True
        except Exception:
            checks[name] = False

    registry = default_registry()
    info = {
        "engine_version": VERSION,
        "rules_loaded": len(registry.all_rules),
        "protected_brands": len(PROTECTED_BRANDS),
        "optional_dependencies": checks,
    }
    if as_json:
        typer.echo(json.dumps(info, indent=2))
        return
    typer.echo(typer.style(f"aegis-comply {VERSION}", bold=True))
    typer.echo(f"  rules loaded     : {info['rules_loaded']}")
    typer.echo(f"  protected brands : {info['protected_brands']}")
    typer.echo(typer.style("  optional deps:", bold=True))
    for name, ok in checks.items():
        mark = typer.style("✓", fg=typer.colors.GREEN) if ok else typer.style("✗", fg=typer.colors.RED)
        typer.echo(f"    {mark} {name}")


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(VERSION)


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m aegis.comply.cli`` and the console script."""
    app(args=argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover
    main()
