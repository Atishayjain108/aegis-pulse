"""Tests for `aegis.harden.playbooks`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aegis.harden.errors import PlaybookValidationError
from aegis.harden.playbooks import (
    PlaybookRegistry,
    builtin_registry,
    load_dir,
    load_playbook_dict,
    load_playbook_file,
)
from aegis.harden.schemas import Playbook, PlaybookMatch

# ---------------------------------------------------------------------------
# load_playbook_dict
# ---------------------------------------------------------------------------


class TestLoadDict:
    def _good(self, **over) -> dict:
        base = {
            "name": "t",
            "version": 1,
            "match": {"source": "reddit-rss"},
            "delay_ms": 500,
            "retries": 3,
            "profile": "standard",
        }
        base.update(over)
        return base

    def test_minimal_valid(self) -> None:
        pb = load_playbook_dict({**self._good(), "rate_per_min": 60})
        assert pb.name == "t"
        assert pb.profile == "standard"

    def test_missing_required_key(self) -> None:
        bad = self._good(rate_per_min=60)
        del bad["name"]
        with pytest.raises(PlaybookValidationError) as exc:
            load_playbook_dict(bad)
        assert exc.value.code == "AEGIS-HARDEN-0001"

    def test_not_a_mapping(self) -> None:
        with pytest.raises(PlaybookValidationError):
            load_playbook_dict([])  # type: ignore[arg-type]

    def test_match_not_a_mapping(self) -> None:
        bad = self._good(rate_per_min=60)
        bad["match"] = "reddit-rss"
        with pytest.raises(PlaybookValidationError):
            load_playbook_dict(bad)

    def test_out_of_bounds_delay(self) -> None:
        bad = self._good(rate_per_min=60, delay_ms=10)
        with pytest.raises(PlaybookValidationError) as exc:
            load_playbook_dict(bad)
        assert exc.value.code == "AEGIS-HARDEN-0002"

    def test_unknown_profile(self) -> None:
        bad = self._good(rate_per_min=60, profile="evil-mode")
        with pytest.raises(PlaybookValidationError) as exc:
            load_playbook_dict(bad)
        assert exc.value.code == "AEGIS-HARDEN-0002"


# ---------------------------------------------------------------------------
# load_playbook_file
# ---------------------------------------------------------------------------


class TestLoadFile:
    def test_load_real_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "x.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "name": "x",
                    "version": 1,
                    "match": {"source": "hacker-news"},
                    "delay_ms": 300,
                    "jitter_ms": 50,
                    "rate_per_min": 60,
                    "retries": 3,
                    "profile": "minimal",
                }
            ),
            encoding="utf-8",
        )
        pb = load_playbook_file(p)
        assert pb.name == "x"

    def test_empty_file_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("", encoding="utf-8")
        with pytest.raises(PlaybookValidationError) as exc:
            load_playbook_file(p)
        assert exc.value.code == "AEGIS-HARDEN-0001"

    def test_bad_yaml_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("name: t\n  invalid yaml :::", encoding="utf-8")
        with pytest.raises(PlaybookValidationError):
            load_playbook_file(p)


# ---------------------------------------------------------------------------
# load_dir
# ---------------------------------------------------------------------------


class TestLoadDir:
    def test_load_bundled_playbooks(self, playbooks_dir: Path) -> None:
        reg = load_dir(playbooks_dir)
        assert reg.size >= 4
        assert reg.has_default

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        reg = load_dir(tmp_path / "does-not-exist")
        assert reg.size == 0

    def test_skips_invalid_files(self, tmp_path: Path) -> None:
        good = tmp_path / "good.yaml"
        good.write_text(
            yaml.safe_dump(
                {
                    "name": "g",
                    "version": 1,
                    "match": {"source": "hacker-news"},
                    "delay_ms": 300,
                    "rate_per_min": 60,
                    "retries": 3,
                    "profile": "standard",
                }
            ),
            encoding="utf-8",
        )
        bad = tmp_path / "bad.yaml"
        bad.write_text("not: a playbook", encoding="utf-8")
        reg = load_dir(tmp_path)
        assert reg.size == 1  # bad was skipped


# ---------------------------------------------------------------------------
# Registry matching
# ---------------------------------------------------------------------------


class TestRegistryMatch:
    def test_source_match(self, reg: PlaybookRegistry) -> None:
        pb = reg.match(source="reddit-rss")
        assert pb.name == "reddit-rss"

    def test_url_domain_match(self, reg: PlaybookRegistry) -> None:
        pb = reg.match(url="https://www.amazon.com/bestsellers/")
        assert pb.name == "amazon"

    def test_falls_back_to_default(self, reg: PlaybookRegistry) -> None:
        pb = reg.match(source="totally-unknown")
        assert pb.name == "default"

    def test_no_default_raises(self) -> None:
        reg = PlaybookRegistry()
        reg.register(
            Playbook(
                name="solo",
                version=1,
                match=PlaybookMatch(source="reddit-rss"),
                delay_ms=500,
                rate_per_min=60,
                retries=3,
                profile="standard",
            )
        )
        with pytest.raises(PlaybookValidationError) as exc:
            reg.match(source="unknown")
        assert exc.value.code == "AEGIS-HARDEN-0004"

    def test_source_wins_over_url(self, reg: PlaybookRegistry) -> None:
        # Even with an amazon URL, an explicit source slug wins.
        pb = reg.match(source="hacker-news", url="https://www.amazon.com/x")
        assert pb.name == "hacker-news"


class TestBuiltins:
    def test_builtin_size(self, reg: PlaybookRegistry) -> None:
        assert reg.size >= 5
        assert reg.has_default

    def test_builtin_names_unique(self, reg: PlaybookRegistry) -> None:
        names = [pb.name for pb in reg.all()]
        assert len(names) == len(set(names))
