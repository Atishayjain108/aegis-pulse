"""CLI smoke tests using Typer's CliRunner.

We only exercise the no-infrastructure subcommands here:
  * version
  * compose-demo   (pure compose, no DB/Redis)
  * killswitch state   (with redis_url empty → no-Redis fallback)

The serve/drain/tail/intake commands require external services, which
real-deployment integration tests cover elsewhere.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from aegis.execute.cli.main import app


def _extract_json(output: str) -> dict:
    """Extract the trailing JSON object from CLI stdout.

    The CLI may interleave structured log lines with its JSON output
    depending on the structlog handler. We locate the last balanced
    `{...}` block and parse it.
    """
    # Find the last top-level `{...}` block.
    depth = 0
    start_idx = -1
    for i, ch in enumerate(output):
        if ch == "{":
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start_idx >= 0:
                last_start, last_end = start_idx, i + 1
    return json.loads(output[last_start:last_end])


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_version_command(runner: CliRunner):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.4.0" in result.stdout


def test_compose_demo_enter(runner: CliRunner):
    result = runner.invoke(
        app,
        [
            "compose-demo",
            "--trend-id",
            "cli-demo-1",
            "--verdict",
            "ENTER",
            "--score",
            "0.80",
            "--confidence",
            "0.72",
            "--p-breakout",
            "0.88",
            "--p-decline",
            "0.10",
            "--margin",
            "4.50",
            "--loss-prob",
            "0.10",
        ],
    )
    assert result.exit_code == 0, result.stdout
    body = _extract_json(result.stdout)
    assert body["verdict"] == "ENTER"
    assert body["trend_id"] == "cli-demo-1"
    assert body["priority"] == 0  # P0 due to high breakout + conf


def test_compose_demo_block(runner: CliRunner):
    result = runner.invoke(
        app,
        [
            "compose-demo",
            "--verdict",
            "BLOCK",
            "--score",
            "0.10",
            "--confidence",
            "0.10",
        ],
    )
    assert result.exit_code == 0
    body = _extract_json(result.stdout)
    assert body["verdict"] == "BLOCK"


def test_killswitch_state_no_redis(runner: CliRunner):
    # redis-py may or may not be installed; if not, the CLI prints a hint
    # and the subcommand prints "TRIPPED" (the fail-closed default with
    # redis_client=None). Either way, the exit code is 0.
    result = runner.invoke(
        app,
        ["killswitch", "state", "--redis-url", ""],
    )
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    # Either "ARMED" or "TRIPPED" depending on fail_closed defaults.
    assert last in {"ARMED", "TRIPPED"}
