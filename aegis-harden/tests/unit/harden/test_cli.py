"""Tests for `aegis.harden.cli`."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from aegis.harden.cli.main import app

runner = CliRunner()


class TestCLI:
    def test_version(self) -> None:
        res = runner.invoke(app, ["version"])
        assert res.exit_code == 0
        assert "0.5.0" in res.stdout

    def test_doctor_passes(self) -> None:
        res = runner.invoke(app, ["doctor"])
        assert res.exit_code == 0
        assert "All checks passed." in res.stdout

    def test_playbooks_list_builtin(self) -> None:
        res = runner.invoke(app, ["playbooks", "list"])
        assert res.exit_code == 0
        assert "reddit-rss" in res.stdout

    def test_playbooks_validate_file(self, tmp_path: Path) -> None:
        p = tmp_path / "x.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "name": "x",
                    "version": 1,
                    "match": {"source": "reddit-rss"},
                    "delay_ms": 500,
                    "rate_per_min": 60,
                    "retries": 3,
                    "profile": "standard",
                }
            ),
            encoding="utf-8",
        )
        res = runner.invoke(app, ["playbooks", "validate", str(p)])
        assert res.exit_code == 0
        assert '"name"' in res.stdout

    def test_playbooks_validate_dir(self, playbooks_dir: Path) -> None:
        res = runner.invoke(app, ["playbooks", "validate", str(playbooks_dir)])
        assert res.exit_code == 0
        assert "loaded=" in res.stdout

    def test_playbooks_match_source(self) -> None:
        res = runner.invoke(app, ["playbooks", "match", "--source", "reddit-rss"])
        assert res.exit_code == 0
        assert "reddit-rss" in res.stdout

    def test_playbooks_match_requires_arg(self) -> None:
        res = runner.invoke(app, ["playbooks", "match"])
        assert res.exit_code != 0

    def test_fingerprint_show(self) -> None:
        res = runner.invoke(app, ["fingerprint", "show", "--count", "2"])
        assert res.exit_code == 0
        # Output contains two JSON blobs
        assert res.stdout.count('"tls"') >= 2

    def test_honeypot_scan_url_clean(self) -> None:
        res = runner.invoke(app, ["honeypot", "scan-url", "https://example.com/articles"])
        assert res.exit_code == 0
        assert '"blocked": false' in res.stdout

    def test_honeypot_scan_url_blocked(self) -> None:
        res = runner.invoke(app, ["honeypot", "scan-url", "https://example.com/honeypot"])
        assert res.exit_code == 0
        assert '"blocked": true' in res.stdout

    def test_smooth_demo(self) -> None:
        res = runner.invoke(app, ["smooth", "demo", "--samples", "32"])
        assert res.exit_code == 0
        assert '"smoothed_score"' in res.stdout
