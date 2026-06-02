"""Smoke tests for the Typer CLI."""

from __future__ import annotations

import json

import pytest

typer_testing = pytest.importorskip("typer.testing")
from typer.testing import CliRunner  # noqa: E402

from aegis.comply.cli import app  # noqa: E402

runner = CliRunner()


def test_version_command():
    from aegis.comply import VERSION

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert VERSION in result.stdout


def test_doctor_json():
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["rules_loaded"] >= 10
    assert "optional_dependencies" in data


def test_rules_json_listing():
    result = runner.invoke(app, ["rules", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert any(r["rule_id"] == "FTC-HEALTH-001" for r in rows)


def test_brands_listing():
    result = runner.invoke(app, ["brands", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert any(r["mark"] == "gucci" for r in rows)


def test_check_clean_exit_zero():
    result = runner.invoke(app, ["check", "--title", "plain reusable bottle", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["verdict"] == "clear"


def test_check_block_exits_nonzero():
    result = runner.invoke(
        app,
        ["check", "--title", "keto gummies", "--claim", "cures diabetes", "--json"],
    )
    # BLOCK verdict -> exit code 1 (usable as a CI gate).
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["verdict"] == "block"
