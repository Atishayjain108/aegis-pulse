"""CLI tests via Click's CliRunner — no subprocess needed."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from aegis.datalake.cli.main import cli


def _parse_doctor_payload(output: str) -> dict[str, object]:
    """Extract the doctor --json-out payload from mixed stdout/stderr.

    When structlog is configured for JSON (common in pytest because stderr is
    not a TTY), facade lifecycle events land on stderr.  CliRunner mixes stderr
    into ``result.output`` by default, so we locate the Phase 10 payload by its
    stable ``phase`` key rather than assuming stdout is clean JSON.
    """
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(output):
        start = output.find("{", pos)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(output, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        if isinstance(obj, dict) and obj.get("phase") == "phase10" and "backend_ok" in obj:
            return obj
        # raw_decode returns the absolute end index, not a relative advance.
        pos = end if end > start else start + 1
    raise AssertionError(f"No Phase 10 doctor payload in output: {output!r}")


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
        body = _parse_doctor_payload(result.output)
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
