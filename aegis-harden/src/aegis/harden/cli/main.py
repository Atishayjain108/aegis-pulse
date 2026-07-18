"""
`aegis-harden` CLI.

Subcommands:
  * `version`               — print Phase 5 version.
  * `doctor`                — environment sanity check.
  * `playbooks list`        — list registered playbooks.
  * `playbooks validate`    — validate a playbook file or directory.
  * `playbooks match`       — show which playbook would match a URL.
  * `fingerprint show`      — show one or more fingerprint profiles.
  * `honeypot scan-url`     — score a URL.
  * `smooth demo`           — randomized-smoothing demo against a built-in heuristic.

Designed to be useful both as an ops tool and as a smoke test in CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import typer

from aegis.harden.config import get_settings
from aegis.harden.constants import PHASE5_VERSION
from aegis.harden.fingerprint import FingerprintPool
from aegis.harden.honeypot import score_url
from aegis.harden.playbooks import builtin_registry, load_dir, load_playbook_file
from aegis.harden.smoothing import smooth_predict
from aegis.harden.utils.rng import SeededRng

app = typer.Typer(
    help="AEGIS Pulse — Phase 5 (Adversarial Hardening) CLI.",
    add_completion=False,
    no_args_is_help=True,
)

playbooks_app = typer.Typer(help="Playbook tooling.", no_args_is_help=True)
fingerprint_app = typer.Typer(help="Fingerprint tooling.", no_args_is_help=True)
honeypot_app = typer.Typer(help="Honeypot tooling.", no_args_is_help=True)
smooth_app = typer.Typer(help="Smoothing tooling.", no_args_is_help=True)

app.add_typer(playbooks_app, name="playbooks")
app.add_typer(fingerprint_app, name="fingerprint")
app.add_typer(honeypot_app, name="honeypot")
app.add_typer(smooth_app, name="smooth")


# ---------------------------------------------------------------------------
# Root commands
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the Phase 5 version."""
    typer.echo(PHASE5_VERSION)


@app.command()
def doctor() -> None:
    """Run a sanity check over Phase 5 modules.

    Mirrors the spirit of `aegis doctor` from the main project — every check
    is independent, every check prints OK/FAIL, exit code is non-zero if any
    check fails.
    """
    failures = 0

    def _check(name: str, ok: bool, note: str = "") -> None:
        nonlocal failures
        status = "OK  " if ok else "FAIL"
        typer.echo(f"  [{status}] {name}{(' — ' + note) if note else ''}")
        if not ok:
            failures += 1

    typer.echo(f"Phase 5 doctor — version {PHASE5_VERSION}")

    # 1. Settings load
    try:
        settings = get_settings()
        _check("settings.load", True, note=f"profile={settings.default_profile}")
    except Exception as exc:  # noqa: BLE001
        _check("settings.load", False, note=str(exc))

    # 2. Fingerprint pool
    try:
        pool = FingerprintPool()
        _check(
            "fingerprint.pool",
            pool.tls_size > 0 and pool.h2_size > 0,
            note=f"tls={pool.tls_size} h2={pool.h2_size}",
        )
    except Exception as exc:  # noqa: BLE001
        _check("fingerprint.pool", False, note=str(exc))

    # 3. Built-in playbooks
    try:
        reg = builtin_registry()
        _check(
            "playbooks.builtin",
            reg.size > 0 and reg.has_default,
            note=f"count={reg.size} default={reg.has_default}",
        )
    except Exception as exc:  # noqa: BLE001
        _check("playbooks.builtin", False, note=str(exc))

    # 4. Honeypot detector
    try:
        v = score_url("https://x.com/foo")
        _check("honeypot.url-scoring", isinstance(v.score, float))
    except Exception as exc:  # noqa: BLE001
        _check("honeypot.url-scoring", False, note=str(exc))

    # 5. Randomized smoothing
    try:
        rng = SeededRng(123)
        result = smooth_predict(lambda x: 0.7, np.zeros(8), n_samples=32, sigma=0.1, rng=rng)
        _check("smoothing.basic", result.n_samples == 32)
    except Exception as exc:  # noqa: BLE001
        _check("smoothing.basic", False, note=str(exc))

    if failures:
        typer.echo(f"\n{failures} check(s) failed.", err=True)
        raise typer.Exit(code=1)
    typer.echo("\nAll checks passed.")


# ---------------------------------------------------------------------------
# Playbooks
# ---------------------------------------------------------------------------


@playbooks_app.command("list")
def playbooks_list(
    path: Path | None = typer.Option(
        None, "--path", "-p", help="Directory of playbook YAMLs. Defaults to settings."
    ),
) -> None:
    """List all registered playbooks."""
    reg = (
        load_dir(path or get_settings().playbooks_dir)
        if path or get_settings().playbooks_dir.exists()
        else builtin_registry()
    )
    if reg.size == 0:
        reg = builtin_registry()
    for pb in reg.all():
        typer.echo(f"{pb.name:24} v{pb.version}  profile={pb.profile:8} rate={pb.rate_per_min}/min")


@playbooks_app.command("validate")
def playbooks_validate(
    path: Path = typer.Argument(..., help="A playbook file or directory."),
) -> None:
    """Validate a single playbook file or every YAML in a directory."""
    if path.is_dir():
        reg = load_dir(path)
        typer.echo(f"loaded={reg.size} default={reg.has_default}")
        if reg.size == 0:
            raise typer.Exit(1)
        return
    pb = load_playbook_file(path)
    typer.echo(json.dumps(pb.model_dump(mode="json"), indent=2, default=str))


@playbooks_app.command("match")
def playbooks_match(
    source: str | None = typer.Option(None, "--source", "-s", help="Source slug."),
    url: str | None = typer.Option(None, "--url", "-u", help="URL to match against domains."),
    path: Path | None = typer.Option(None, "--path", "-p", help="Override playbooks dir."),
) -> None:
    """Show which playbook matches a given source/URL."""
    if not source and not url:
        typer.echo("at least one of --source / --url is required", err=True)
        raise typer.Exit(2)
    reg = load_dir(path) if path else builtin_registry()
    if reg.size == 0:
        reg = builtin_registry()
    try:
        pb = reg.match(source=source, url=url)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"no match: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(pb.model_dump(mode="json"), indent=2, default=str))


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


@fingerprint_app.command("show")
def fingerprint_show(
    count: int = typer.Option(3, "--count", "-n", help="How many to print."),
    seed: int = typer.Option(0xA5615, "--seed"),
) -> None:
    """Print N fingerprint profiles."""
    pool = FingerprintPool()
    rng = SeededRng(seed)
    for _ in range(max(1, count)):
        fp = pool.pick(rng)
        typer.echo(json.dumps(fp.model_dump(mode="json"), indent=2, default=str))


# ---------------------------------------------------------------------------
# Honeypot
# ---------------------------------------------------------------------------


@honeypot_app.command("scan-url")
def honeypot_scan_url(url: str = typer.Argument(...)) -> None:
    """Score a URL."""
    v = score_url(url)
    typer.echo(json.dumps(v.model_dump(mode="json"), indent=2, default=str))


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------


@smooth_app.command("demo")
def smooth_demo(
    n_samples: int = typer.Option(128, "--samples"),
    sigma: float = typer.Option(0.1, "--sigma"),
    seed: int = typer.Option(0xA5615, "--seed"),
) -> None:
    """Run a randomized-smoothing demo against a synthetic heuristic.

    The fake heuristic is the first feature, clipped to [0,1] — easy to
    reason about analytically. The smoothed score should track the raw,
    with a small certified radius.
    """

    def base(x: np.ndarray) -> float:
        return float(np.clip(x[0], 0.0, 1.0))

    x = np.zeros(20, dtype=np.float64)
    x[0] = 0.7
    rng = SeededRng(seed)
    result = smooth_predict(base, x, sigma=sigma, n_samples=n_samples, rng=rng)
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, default=str))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    """Entrypoint for `python -m aegis.harden.cli`."""
    try:
        app()
        return 0
    except typer.Exit as exc:
        return int(exc.exit_code or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
