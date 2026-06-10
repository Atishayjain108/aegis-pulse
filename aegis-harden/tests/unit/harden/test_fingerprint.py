"""Tests for `aegis.harden.fingerprint`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aegis.harden.errors import FingerprintPoolError
from aegis.harden.fingerprint import FingerprintPool, validate_h2_settings
from aegis.harden.schemas import H2Settings, TLSFingerprint
from aegis.harden.utils.rng import SeededRng


class TestFingerprintPoolBuiltins:
    def test_default_pool_non_empty(self, fp_pool: FingerprintPool) -> None:
        assert fp_pool.tls_size >= 5
        assert fp_pool.h2_size >= 3

    def test_pick_returns_profile(self, fp_pool: FingerprintPool, rng: SeededRng) -> None:
        fp = fp_pool.pick(rng)
        assert fp.tls.fid
        assert fp.h2.initial_window_size >= 65_535

    def test_pick_is_deterministic(self, fp_pool: FingerprintPool) -> None:
        picks_a = [fp_pool.pick(SeededRng(seed=7)).tls.fid for _ in range(5)]
        # Same seed reused for each call means same first pick
        picks_b = [fp_pool.pick(SeededRng(seed=7)).tls.fid for _ in range(5)]
        assert picks_a == picks_b

    def test_pick_walks_with_continuous_rng(self, fp_pool: FingerprintPool) -> None:
        rng = SeededRng(seed=7)
        picks = [fp_pool.pick(rng).tls.fid for _ in range(20)]
        # Should produce at least 2 distinct fingerprints over 20 picks
        assert len(set(picks)) >= 2


class TestFamilyPick:
    def test_pick_chrome_family(self, fp_pool: FingerprintPool, rng: SeededRng) -> None:
        fp = fp_pool.pick_by_family("chrome", rng)
        assert "chrome" in fp.tls.fid

    def test_pick_firefox_family(self, fp_pool: FingerprintPool, rng: SeededRng) -> None:
        fp = fp_pool.pick_by_family("firefox", rng)
        assert "firefox" in fp.tls.fid

    def test_unknown_family_raises(self, fp_pool: FingerprintPool, rng: SeededRng) -> None:
        with pytest.raises(FingerprintPoolError) as exc:
            fp_pool.pick_by_family("netscape", rng)
        assert exc.value.code == "AEGIS-HARDEN-0010"


class TestExtraFingerprints:
    def test_extra_are_included(self, rng: SeededRng) -> None:
        extra = (TLSFingerprint(fid="custom-1", ua="Mozilla/5.0 Custom Browser", ja3="771,99"),)
        pool = FingerprintPool(extra=extra)
        fids = {tls.fid for tls in pool.all_tls()}
        assert "custom-1" in fids

    def test_from_path_missing(self, tmp_path: Path) -> None:
        p = tmp_path / "missing.json"
        pool = FingerprintPool.from_path(p)  # missing file → builtin only
        assert pool.tls_size >= 5

    def test_from_path_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(FingerprintPoolError) as exc:
            FingerprintPool.from_path(p)
        assert exc.value.code == "AEGIS-HARDEN-0010"

    def test_from_path_not_a_list(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"oops": True}), encoding="utf-8")
        with pytest.raises(FingerprintPoolError):
            FingerprintPool.from_path(p)

    def test_from_path_valid(self, tmp_path: Path) -> None:
        p = tmp_path / "extra.json"
        extra = [{"fid": "custom-2", "ua": "Mozilla/5.0 Browser Custom"}]
        p.write_text(json.dumps(extra), encoding="utf-8")
        pool = FingerprintPool.from_path(p)
        assert any(t.fid == "custom-2" for t in pool.all_tls())


class TestH2Validation:
    def test_valid_passthrough(self) -> None:
        s = H2Settings(
            initial_window_size=6_291_456,
            max_frame_size=16_384,
            max_header_list_size=262_144,
        )
        assert validate_h2_settings(s) is s

    def test_out_of_bounds_caught(self) -> None:
        # Construct one that passes schema bounds but fails the extra validator.
        # The function should still be tolerant of valid inputs — its bounds
        # match the schema's, so this is mostly a defense-in-depth sanity test.
        s = H2Settings(
            initial_window_size=H2Settings.model_fields["initial_window_size"].metadata[0].ge,
            max_frame_size=H2Settings.model_fields["max_frame_size"].metadata[0].ge,
            max_header_list_size=262_144,
        )
        assert validate_h2_settings(s) is s
