"""Tests for `aegis.harden.schemas`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aegis.harden.schemas import (
    FingerprintProfile,
    H2Settings,
    HardenVerdict,
    HoneypotVerdict,
    Playbook,
    PlaybookMatch,
    PoisoningReport,
    PoisoningSignal,
    SmoothingResult,
    TLSFingerprint,
)


class TestTLSFingerprint:
    def test_valid(self) -> None:
        fp = TLSFingerprint(fid="chrome-120", ja3="771,4865", ua="Mozilla/5.0 Browser")
        assert fp.fid == "chrome-120"

    def test_empty_ja3_normalized_to_none(self) -> None:
        fp = TLSFingerprint(fid="x123", ja3="   ", ua="Mozilla/5.0 Browser")
        assert fp.ja3 is None

    def test_short_ua_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TLSFingerprint(fid="x123", ua="short")

    def test_frozen(self) -> None:
        fp = TLSFingerprint(fid="x123", ua="Mozilla/5.0 Browser")
        with pytest.raises(ValidationError):
            fp.fid = "y"  # type: ignore[misc]


class TestH2Settings:
    def test_valid(self) -> None:
        s = H2Settings(
            initial_window_size=6_291_456, max_frame_size=16_384, max_header_list_size=262_144
        )
        assert s.enable_push is False

    def test_window_too_small(self) -> None:
        with pytest.raises(ValidationError):
            H2Settings(initial_window_size=1, max_frame_size=16_384, max_header_list_size=4_096)

    def test_window_too_big(self) -> None:
        with pytest.raises(ValidationError):
            H2Settings(initial_window_size=10**9, max_frame_size=16_384, max_header_list_size=4_096)


class TestFingerprintProfile:
    def test_compose(self) -> None:
        tls = TLSFingerprint(fid="chrome-120", ua="Mozilla/5.0 Browser")
        h2 = H2Settings(
            initial_window_size=6_291_456, max_frame_size=16_384, max_header_list_size=262_144
        )
        fp = FingerprintProfile(tls=tls, h2=h2)
        assert fp.tls.fid == "chrome-120"


class TestPlaybook:
    def _make(self, **overrides) -> Playbook:
        kw = dict(
            name="test",
            version=1,
            match=PlaybookMatch(source="reddit-rss"),
            delay_ms=500,
            jitter_ms=100,
            rate_per_min=60,
            retries=3,
            profile="standard",
        )
        kw.update(overrides)
        return Playbook(**kw)

    def test_valid(self) -> None:
        pb = self._make()
        assert pb.name == "test"
        assert pb.profile == "standard"

    def test_delay_floor_enforced(self) -> None:
        with pytest.raises(ValidationError):
            self._make(delay_ms=10)  # below PLAYBOOK_MIN_DELAY_MS=50

    def test_rate_ceiling_enforced(self) -> None:
        with pytest.raises(ValidationError):
            self._make(rate_per_min=1_000_000)

    def test_retries_ceiling(self) -> None:
        with pytest.raises(ValidationError):
            self._make(retries=999)

    def test_source_with_dot_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Playbook(
                name="x",
                version=1,
                match=PlaybookMatch(source="example.com"),
                delay_ms=500,
                rate_per_min=60,
                retries=3,
                profile="standard",
            )

    def test_effective_delay_no_jitter_is_constant(self) -> None:
        pb = self._make(jitter_ms=0)
        assert pb.effective_delay_ms(0) == 500
        assert pb.effective_delay_ms(42) == 500

    def test_effective_delay_with_jitter_in_range(self) -> None:
        pb = self._make(jitter_ms=200)
        for n in range(50):
            d = pb.effective_delay_ms(n)
            assert 300 <= d <= 700

    def test_effective_delay_is_deterministic(self) -> None:
        pb = self._make(jitter_ms=200)
        seq1 = [pb.effective_delay_ms(n) for n in range(20)]
        seq2 = [pb.effective_delay_ms(n) for n in range(20)]
        assert seq1 == seq2


class TestHoneypotVerdict:
    def test_make_blocked_high_score(self) -> None:
        v = HoneypotVerdict.make("https://x.com/trap", 0.9, ("trap",))
        assert v.blocked is True
        assert v.warn is False

    def test_make_warn_medium_score(self) -> None:
        v = HoneypotVerdict.make("https://x.com/?", 0.5, ("hidden",))
        assert v.blocked is False
        assert v.warn is True

    def test_make_clean_low_score(self) -> None:
        v = HoneypotVerdict.make("https://x.com/foo", 0.1, ())
        assert v.blocked is False
        assert v.warn is False

    def test_score_clamped_above(self) -> None:
        v = HoneypotVerdict.make("https://x.com/foo", 99.0, ())
        assert v.score == 1.0
        assert v.blocked is True

    def test_score_clamped_below(self) -> None:
        v = HoneypotVerdict.make("https://x.com/foo", -3.0, ())
        assert v.score == 0.0


class TestSmoothingResult:
    def test_valid(self) -> None:
        r = SmoothingResult(
            smoothed_score=0.6,
            certified_radius=0.05,
            n_samples=32,
            sigma=0.1,
            agrees_with_raw=True,
            raw_score=0.55,
        )
        assert r.agrees_with_raw is True

    def test_sigma_bounds(self) -> None:
        with pytest.raises(ValidationError):
            SmoothingResult(
                smoothed_score=0.6,
                certified_radius=0.05,
                n_samples=32,
                sigma=99.0,
                agrees_with_raw=True,
                raw_score=0.55,
            )


class TestPoisoningSignal:
    def test_valid(self) -> None:
        s = PoisoningSignal(detector="label_flip", flagged=True, severity=0.8)
        assert s.detector == "label_flip"


class TestPoisoningReport:
    def test_valid(self) -> None:
        r = PoisoningReport(batch_id="b1", n_samples=100, signals=(), decision="accept")
        assert r.decision == "accept"

    def test_decision_literal_enforced(self) -> None:
        with pytest.raises(ValidationError):
            PoisoningReport(batch_id="b1", n_samples=100, signals=(), decision="maybe")  # type: ignore[arg-type]


class TestHardenVerdict:
    def test_valid(self) -> None:
        v = HardenVerdict(
            trend_id="t1",
            source="honeypot",
            verdict="block",
            score=0.9,
            confidence=0.95,
            reason_code="trap",
            detail="x",
        )
        assert v.verdict == "block"

    def test_unknown_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HardenVerdict(
                trend_id="t1",
                source="unknown",
                verdict="block",  # type: ignore[arg-type]
                score=0.9,
                confidence=0.95,
                reason_code="trap",
            )
