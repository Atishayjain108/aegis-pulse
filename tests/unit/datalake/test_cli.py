"""CLI tests via Click's CliRunner — no subprocess needed."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from aegis.datalake.cli.main import cli


def _common_opts(tmp_root: str) -> list[str]:
    return [
        "--local-root", tmp_root,
        "--catalog-db-path", str(Path(tmp_root) / "catalog.db"),
    ]


class TestHelp:
    def test_top_level_help(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "AEGIS" in result.output

    def test_subcommand_help_each(self) -> None:
        for sub in [
            "doctor", "migrate", "list-tables", "list-partitions",
            "build-silver", "build-gold", "query", "retention", "daily",
        ]:
            result = CliRunner().invoke(cli, [sub, "--help"])
            assert result.exit_code == 0, f"{sub} --help failed: {result.output}"


class TestDoctor:
    def test_doctor_json(self, tmp_root: str) -> None:
        result = CliRunner().invoke(
            cli, [*_common_opts(tmp_root), "doctor", "--json-out"]
        )
        assert result.exit_code == 0, result.output
        # Output may include log lines from structlog; find the JSON.
        # The last lines should be the JSON block.
        output = result.output
        # find first '{' and last '}'
        start = output.find("{")
        end = output.rfind("}")
        body = json.loads(output[start : end + 1])
        assert body["backend_ok"] is True
        assert body["catalog_ok"] is True


class TestListTables:
    def test_list_tables_empty(self, tmp_root: str) -> None:
        result = CliRunner().invoke(
            cli, [*_common_opts(tmp_root), "list-tables"]
        )
        assert result.exit_code == 0


class TestMigrate:
    def test_migrate_noop(self, tmp_root: str) -> None:
        result = CliRunner().invoke(
            cli, [*_common_opts(tmp_root), "migrate", "--json-out"]
        )
        assert result.exit_code == 0


class TestBuildSilver:
    def test_build_silver_no_bronze_is_ok(self, tmp_root: str) -> None:
        result = CliRunner().invoke(
            cli,
            [*_common_opts(tmp_root), "build-silver", "--date", "2026-05-20", "--json-out"],
        )
        assert result.exit_code == 0, result.output


class TestBuildGold:
    def test_build_gold_no_silver_is_ok(self, tmp_root: str) -> None:
        result = CliRunner().invoke(
            cli,
            [*_common_opts(tmp_root), "build-gold", "--date", "2026-05-20", "--json-out"],
        )
        assert result.exit_code == 0, result.output


class TestQuery:
    def test_query_literal(self, tmp_root: str) -> None:
        result = CliRunner().invoke(
            cli,
            [*_common_opts(tmp_root), "query", "SELECT 1 AS one"],
        )
        assert result.exit_code == 0
        assert "one" in result.output


class TestRetention:
    def test_retention_silver_dry_run(self, tmp_root: str) -> None:
        result = CliRunner().invoke(
            cli, [*_common_opts(tmp_root), "retention", "silver", "--json-out"]
        )
        assert result.exit_code == 0
