"""Tests for `aegis.harden.detect`."""

from __future__ import annotations

import numpy as np

from aegis.harden.detect import (
    honeypot_to_verdict,
    poisoning_to_verdict,
    screen_dom_element,
    screen_inference,
    screen_training_batch,
    screen_url,
    smoothing_to_verdict,
)
from aegis.harden.schemas import (
    HoneypotVerdict,
    PoisoningReport,
    PoisoningSignal,
    SmoothingResult,
)

# ---------------------------------------------------------------------------
# Mapper unit tests
# ---------------------------------------------------------------------------


class TestHoneypotToVerdict:
    def test_block_mapping(self) -> None:
        v = HoneypotVerdict.make("https://x.com/trap", 0.9, ("style:display:none",))
        out = honeypot_to_verdict(v, trend_id="t1")
        assert out.verdict == "block"
        assert out.source == "honeypot"
        assert out.trend_id == "t1"
        assert out.reason_code == "style"

    def test_warn_mapping(self) -> None:
        v = HoneypotVerdict.make("https://x.com", 0.5, ("rect:offscreen",))
        out = honeypot_to_verdict(v)
        assert out.verdict == "warn"

    def test_proceed_mapping(self) -> None:
        v = HoneypotVerdict.make("https://x.com", 0.0, ())
        out = honeypot_to_verdict(v)
        assert out.verdict == "proceed"
        assert out.reason_code == "no-signal"

    def test_confidence_high_at_extremes(self) -> None:
        extreme = HoneypotVerdict.make("https://x.com", 1.0, ("a",))
        clean = HoneypotVerdict.make("https://x.com", 0.0, ())
        ambiguous = HoneypotVerdict.make("https://x.com", 0.5, ("a",))
        assert honeypot_to_verdict(extreme).confidence > honeypot_to_verdict(ambiguous).confidence
        assert honeypot_to_verdict(clean).confidence > honeypot_to_verdict(ambiguous).confidence


class TestSmoothingToVerdict:
    def test_agrees_is_proceed(self) -> None:
        r = SmoothingResult(
            smoothed_score=0.6,
            certified_radius=0.05,
            n_samples=32,
            sigma=0.1,
            agrees_with_raw=True,
            raw_score=0.6,
        )
        out = smoothing_to_verdict(r, trend_id="x")
        assert out.verdict == "proceed"
        assert out.source == "smoothing"
        assert "smoothing-stable" in out.reason_code

    def test_disagrees_is_warn(self) -> None:
        r = SmoothingResult(
            smoothed_score=0.3,
            certified_radius=0.05,
            n_samples=32,
            sigma=0.1,
            agrees_with_raw=False,
            raw_score=0.6,
        )
        out = smoothing_to_verdict(r)
        assert out.verdict == "warn"


class TestPoisoningToVerdict:
    def test_accept_proceeds(self) -> None:
        r = PoisoningReport(batch_id="b1", n_samples=200, signals=(), decision="accept")
        out = poisoning_to_verdict(r)
        assert out.verdict == "proceed"

    def test_reject_blocks(self) -> None:
        r = PoisoningReport(
            batch_id="b1",
            n_samples=200,
            signals=(PoisoningSignal(detector="label_flip", flagged=True, severity=0.9),),
            decision="reject",
        )
        out = poisoning_to_verdict(r)
        assert out.verdict == "block"
        assert "poisoning-reject" in out.reason_code

    def test_warn_intermediate(self) -> None:
        r = PoisoningReport(
            batch_id="b1",
            n_samples=200,
            signals=(PoisoningSignal(detector="feature_shift", flagged=False, severity=0.7),),
            decision="warn",
        )
        out = poisoning_to_verdict(r)
        assert out.verdict == "warn"

    def test_confidence_low_for_small_batch(self) -> None:
        r = PoisoningReport(batch_id="b1", n_samples=64, signals=(), decision="accept")
        out = poisoning_to_verdict(r)
        assert out.confidence < 1.0


# ---------------------------------------------------------------------------
# Convenience entry points
# ---------------------------------------------------------------------------


class TestScreeners:
    def test_screen_url_clean(self) -> None:
        v = screen_url("https://example.com/page", trend_id="t1")
        assert v.source == "honeypot"
        assert v.trend_id == "t1"
        assert v.verdict == "proceed"

    def test_screen_url_trap(self) -> None:
        v = screen_url("https://example.com/honeypot")
        assert v.verdict == "block"

    def test_screen_dom(self) -> None:
        el = {
            "href": "https://x.com/bad",
            "classes": ["donotclick"],
            "text": "hi",
            "rect": {"x": 1, "y": 1, "w": 10, "h": 10},
        }
        out = screen_dom_element(el, trend_id="t-dom")
        assert out.verdict == "block"

    def test_screen_inference_stable(self) -> None:
        def clf(x: np.ndarray) -> float:
            return 0.8

        out = screen_inference(clf, np.zeros(8), sigma=0.05, n_samples=64, trend_id="t2")
        assert out.source == "smoothing"
        assert out.verdict == "proceed"

    def test_screen_training_batch_accepts_clean(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.standard_normal((200, 6))
        out = screen_training_batch(x=x, batch_id="b1", trend_id="t3")
        assert out.source == "poisoning"
        assert out.verdict == "proceed"

    def test_screen_training_batch_rejects_poisoned(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.standard_normal((200, 6))
        labels = (x[:, 0] > 0).astype(int)
        poisoned = labels.copy()
        poisoned[:80] = 1 - poisoned[:80]
        out = screen_training_batch(x=x, labels=poisoned, reference_labels=labels, batch_id="b")
        assert out.verdict == "block"
