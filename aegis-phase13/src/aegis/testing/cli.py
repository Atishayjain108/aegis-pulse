"""
src/aegis/testing/cli.py — AEGIS Pulse Phase 13: Test CLI.

Provides the ``aegis test`` command for unified test running.
Integrates into the existing Click CLI at aegis.cli.

Usage::

    uv run aegis test unit              # fast unit tests
    uv run aegis test all               # unit + property + integration
    uv run aegis test benchmark         # performance benchmarks
    uv run aegis test coverage          # generate HTML coverage report
    uv run aegis test health            # verify test dependencies

Architecture: Phase 13 integrates as ``aegis test`` sub-command group.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import click

_REPO_ROOT = Path(__file__).parent.parent.parent.parent


def _run_pytest(
    *args: str,
    env_extra: dict[str, str] | None = None,
    check: bool = True,
) -> int:
    """Run pytest with the given arguments in the repo root."""
    env = {**os.environ, **(env_extra or {})}
    cmd = [sys.executable, "-m", "pytest", *args]
    click.echo(f"$ {' '.join(cmd)}", err=True)
    result = subprocess.run(cmd, cwd=_REPO_ROOT, env=env, check=False)  # noqa: S603
    if check and result.returncode not in (0, 5):
        raise SystemExit(result.returncode)
    return result.returncode


@click.group(name="test", invoke_without_command=True)
@click.pass_context
def test_cli(ctx: click.Context) -> None:
    """Phase 13: Run AEGIS test suites."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@test_cli.command("unit")
@click.option("--no-cov", is_flag=True, help="Skip coverage measurement (faster)")
@click.option(
    "--phase",
    type=click.Choice(["0", "2", "3", "4", "10", "11", "core"]),
    default=None,
    help="Test only a specific phase",
)
@click.option("-x", "--fail-fast", is_flag=True, help="Stop on first failure")
@click.option("-v", "--verbose", is_flag=True)
def test_unit(
    no_cov: bool,
    phase: str | None,
    fail_fast: bool,
    verbose: bool,
) -> None:
    """Run unit tests (no infrastructure required)."""
    phase_map = {
        "0": "tests/unit/scrape/",
        "2": "tests/unit/agents/",
        "3": "tests/unit/predict/",
        "4": "tests/unit/execute/",
        "10": "tests/unit/datalake/",
        "11": "tests/unit/llm/",
        "core": "tests/unit/core/",
    }
    path = phase_map.get(phase or "", "tests/unit/ tests/property/")
    args = [*path.split(), "-p", "no:hypothesis", "--no-header"]
    if no_cov:
        args += ["--no-cov"]
    else:
        args += [
            "--cov=src/aegis",
            "--cov-report=term-missing:skip-covered",
            "--cov-fail-under=78",
        ]
    if fail_fast:
        args.append("-x")
    args.append("-v" if verbose else "-q")
    _run_pytest(*args)


@test_cli.command("all")
@click.option("--include-integration", is_flag=True, help="Include integration tests")
def test_all(include_integration: bool) -> None:
    """Run all tests (unit + property, optionally integration)."""
    paths = ["tests/unit/", "tests/property/"]
    env_extra: dict[str, str] = {"HYPOTHESIS_PROFILE": "ci"}
    if include_integration:
        paths.append("tests/integration/")
        env_extra["AEGIS_TEST_INTEGRATION"] = "1"
        env_extra["AEGIS_TEST_POSTGRES"] = "1"
        env_extra["AEGIS_TEST_REDIS"] = "1"
    _run_pytest(
        *paths,
        "--cov=src/aegis",
        "--cov-report=term-missing:skip-covered",
        "--cov-fail-under=78",
        "-p", "no:hypothesis",
        "--no-header",
        "-q",
        env_extra=env_extra,
    )


@test_cli.command("integration")
def test_integration() -> None:
    """Run integration tests (requires running docker services)."""
    click.echo(
        "Starting integration tests...\n"
        "Requires: PostgreSQL, Redis, MinIO (via docker-compose or testcontainers)\n"
        "Set AEGIS_TEST_INTEGRATION=1 to enable.",
    )
    _run_pytest(
        "tests/integration/",
        "-v",
        "--no-cov",
        "--timeout=120",
        "-p", "no:hypothesis",
        "--no-header",
        env_extra={
            "AEGIS_TEST_INTEGRATION": "1",
            "AEGIS_TEST_POSTGRES": "1",
            "AEGIS_TEST_REDIS": "1",
        },
    )


@test_cli.command("benchmark")
@click.option("--save", is_flag=True, help="Save results to .benchmarks/results.json")
def test_benchmark(save: bool) -> None:
    """Run performance benchmark tests."""
    args = [
        "tests/perf/",
        "--benchmark-only",
        "--benchmark-sort=mean",
        "--benchmark-columns=min,mean,max,stddev,rounds",
        "--no-cov",
        "-v",
        "--no-header",
    ]
    if save:
        args.append("--benchmark-json=.benchmarks/results.json")
    _run_pytest(*args)


@test_cli.command("coverage")
def test_coverage() -> None:
    """Generate HTML coverage report."""
    rc = _run_pytest(
        "tests/unit/",
        "-p", "no:hypothesis",
        "--cov=src/aegis",
        "--cov-report=html:htmlcov",
        "--cov-report=term-missing",
        "--no-header",
        "-q",
        check=False,
    )
    click.echo(f"\nCoverage report -> htmlcov/index.html  (exit code: {rc})")


@test_cli.command("health")
def test_health() -> None:
    """Verify all test dependencies are importable."""
    deps = [
        ("pytest", "pytest"),
        ("hypothesis", "hypothesis"),
        ("pydantic", "pydantic"),
        ("faker", "faker"),
        ("httpx", "httpx"),
        ("structlog", "structlog"),
        ("aegis.testing", "aegis.testing"),
    ]
    all_ok = True
    for name, module in deps:
        try:
            imported = __import__(module)
            ver = getattr(imported, "__version__", getattr(imported, "VERSION", "?"))
            click.echo(f"  OK  {name} {ver}")
        except ImportError:
            click.echo(f"  MISSING  {name}", err=True)
            all_ok = False
    if not all_ok:
        raise SystemExit(1)
    click.echo("\nAll test dependencies available")


def main() -> None:
    """Entry point for ``aegis-test`` CLI script."""
    test_cli(standalone_mode=True)


def register_with_main_cli(main_group: Any) -> None:
    """Register ``aegis test`` into the main CLI group.

    Call this from ``aegis/cli/__init__.py``::

        from aegis.testing.cli import register_with_main_cli
        register_with_main_cli(cli)
    """
    main_group.add_command(test_cli)
