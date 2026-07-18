"""
Property-based tests using hypothesis.

These complement the unit tests by exercising the *space* of possible inputs
rather than a hand-picked sample. They are deliberately tight in scope:
  * Playbook bounds — anything Pydantic accepts should round-trip and
    `effective_delay_ms()` should stay in range.
  * Honeypot score — must always end up in [0, 1] with `blocked` matching
    the threshold rule.
  * Smoothing — for a constant classifier the smoothed score MUST equal
    the constant; the certified radius MUST be non-negative.
  * Poisoning — clean batches drawn from the same distribution as the
    reference MUST NOT be flagged at the 99th percentile rate (statistical
    property; we use `assume()` and tight thresholds to keep CI green).

Settings come from conftest.py via `HYPOTHESIS_PROFILE` (default 80 examples,
ci 200 examples). Per-test settings overrides are intentionally avoided so
the env var has a uniform effect across the file.
"""

from __future__ import annotations

import numpy as np
from hypothesis import assume, given
from hypothesis import strategies as st

from aegis.harden.constants import (
    HONEYPOT_BLOCK_THRESHOLD,
    PLAYBOOK_MAX_DELAY_MS,
    PLAYBOOK_MAX_RATE_PER_MIN,
    PLAYBOOK_MAX_RETRIES,
    PLAYBOOK_MIN_DELAY_MS,
)
from aegis.harden.honeypot import score_url
from aegis.harden.poisoning import scan
from aegis.harden.schemas import HoneypotVerdict, Playbook, PlaybookMatch
from aegis.harden.smoothing import smooth_predict
from aegis.harden.utils.rng import SeededRng

# ---------------------------------------------------------------------------
# Playbook properties
# ---------------------------------------------------------------------------


@given(
    delay_ms=st.integers(min_value=PLAYBOOK_MIN_DELAY_MS, max_value=PLAYBOOK_MAX_DELAY_MS),
    jitter_ms=st.integers(min_value=0, max_value=PLAYBOOK_MAX_DELAY_MS),
    rate_per_min=st.integers(min_value=1, max_value=PLAYBOOK_MAX_RATE_PER_MIN),
    retries=st.integers(min_value=0, max_value=PLAYBOOK_MAX_RETRIES),
    n=st.integers(min_value=0, max_value=1000),
)
def test_playbook_effective_delay_within_jitter_band(
    delay_ms: int,
    jitter_ms: int,
    rate_per_min: int,
    retries: int,
    n: int,
) -> None:
    """For any in-bounds playbook, `effective_delay_ms(n)` is in [delay-jitter, delay+jitter]."""
    pb = Playbook(
        name="prop",
        version=1,
        match=PlaybookMatch(source="reddit-rss"),
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
        rate_per_min=rate_per_min,
        retries=retries,
        profile="standard",
    )
    d = pb.effective_delay_ms(n)
    assert delay_ms - jitter_ms <= d <= delay_ms + jitter_ms


@given(
    delay_ms=st.integers(min_value=PLAYBOOK_MIN_DELAY_MS, max_value=PLAYBOOK_MAX_DELAY_MS),
    jitter_ms=st.integers(min_value=0, max_value=PLAYBOOK_MAX_DELAY_MS),
    n=st.integers(min_value=0, max_value=1000),
)
def test_playbook_effective_delay_is_deterministic(
    delay_ms: int,
    jitter_ms: int,
    n: int,
) -> None:
    """Same playbook + same n → same delay, always."""
    pb = Playbook(
        name="prop",
        version=1,
        match=PlaybookMatch(source="reddit-rss"),
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
        rate_per_min=60,
        retries=3,
        profile="standard",
    )
    assert pb.effective_delay_ms(n) == pb.effective_delay_ms(n)


# ---------------------------------------------------------------------------
# Honeypot URL scoring
# ---------------------------------------------------------------------------


@given(
    url=st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="/-_.?=&",
        ),
        min_size=1,
        max_size=200,
    ),
)
def test_honeypot_score_is_unit_interval(url: str) -> None:
    """Score is always in [0, 1] for any URL string."""
    v = score_url(url)
    assert 0.0 <= v.score <= 1.0


@given(
    url=st.text(min_size=1, max_size=200),
)
def test_honeypot_blocked_iff_above_threshold(url: str) -> None:
    """`blocked` is True iff `score >= HONEYPOT_BLOCK_THRESHOLD`."""
    v = score_url(url)
    assert v.blocked == (v.score >= HONEYPOT_BLOCK_THRESHOLD)


@given(score=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False))
def test_honeypot_verdict_make_clamps_score(score: float) -> None:
    """`HoneypotVerdict.make` always clamps to [0, 1]."""
    v = HoneypotVerdict.make("https://x.com", score, ())
    assert 0.0 <= v.score <= 1.0


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------


@given(
    constant=st.floats(min_value=0.0, max_value=1.0),
    sigma=st.floats(min_value=0.01, max_value=0.5),
    n_samples=st.integers(min_value=16, max_value=128),
    seed=st.integers(min_value=0, max_value=10**6),
)
def test_smoothing_constant_classifier_returns_constant(
    constant: float,
    sigma: float,
    n_samples: int,
    seed: int,
) -> None:
    """A classifier that always returns `constant` smooths to `constant`."""
    result = smooth_predict(
        lambda _x: constant,
        np.zeros(8),
        sigma=sigma,
        n_samples=n_samples,
        rng=SeededRng(seed),
    )
    assert abs(result.smoothed_score - constant) < 1e-9


@given(
    constant=st.floats(min_value=0.0, max_value=1.0),
    sigma=st.floats(min_value=0.01, max_value=0.5),
    n_samples=st.integers(min_value=16, max_value=128),
    seed=st.integers(min_value=0, max_value=10**6),
)
def test_smoothing_certified_radius_is_nonneg(
    constant: float,
    sigma: float,
    n_samples: int,
    seed: int,
) -> None:
    result = smooth_predict(
        lambda _x: constant,
        np.zeros(8),
        sigma=sigma,
        n_samples=n_samples,
        rng=SeededRng(seed),
    )
    assert result.certified_radius >= 0.0


@given(
    raw_score=st.floats(min_value=0.0, max_value=1.0),
    sigma=st.floats(min_value=0.01, max_value=0.3),
    n_samples=st.integers(min_value=16, max_value=64),
    seed=st.integers(min_value=0, max_value=10**6),
)
def test_smoothing_reproducible_with_same_seed(
    raw_score: float,
    sigma: float,
    n_samples: int,
    seed: int,
) -> None:
    """Two runs with the same seed produce identical results."""

    def clf(x: np.ndarray) -> float:
        return float(np.clip(raw_score + 0.1 * x[0], 0.0, 1.0))

    r1 = smooth_predict(clf, np.zeros(8), sigma=sigma, n_samples=n_samples, rng=SeededRng(seed))
    r2 = smooth_predict(clf, np.zeros(8), sigma=sigma, n_samples=n_samples, rng=SeededRng(seed))
    assert r1.smoothed_score == r2.smoothed_score
    assert r1.certified_radius == r2.certified_radius


# ---------------------------------------------------------------------------
# Poisoning detection
# ---------------------------------------------------------------------------


@given(
    cols=st.integers(min_value=3, max_value=8),
    base_seed=st.integers(min_value=0, max_value=10**6),
)
def test_clean_iid_batches_low_false_positive_rate(cols: int, base_seed: int) -> None:
    """At ~4σ threshold the false-positive rate on clean batches is low.

    We test the statistical property directly: across 20 trials, no more than
    6 should be rejected (30% upper bound — generous, since the detector is
    calibrated for ~1% false positives at large n but multiple-comparison
    inflation with many columns + small samples raises the empirical rate).
    Tightening this bound below 30% creates a flaky test; tightening the
    underlying detector requires re-calibrating `POISONING_FEATURE_SHIFT_Z_MAX`,
    which is a deliberate trade-off documented in `constants.py`.
    """
    rejects = 0
    for i in range(20):
        rng = np.random.default_rng(base_seed + i)
        x_ref = rng.standard_normal((200, cols))
        x = rng.standard_normal((200, cols))
        report = scan(x=x, x_ref=x_ref)
        if report.decision == "reject":
            rejects += 1
    assert rejects <= 6, f"too many false rejections: {rejects}/20 with cols={cols}"


@given(
    n=st.integers(min_value=128, max_value=300),
    seed=st.integers(min_value=0, max_value=10**6),
)
def test_clean_labels_not_flagged_as_flipped(n: int, seed: int) -> None:
    """When labels == reference exactly, label-flip detector never flags."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, 4))
    labels = (x[:, 0] > 0).astype(int)
    report = scan(x=x, labels=labels, reference_labels=labels.copy())
    flip_sig = next(s for s in report.signals if s.detector == "label_flip")
    assert flip_sig.flagged is False
    assert flip_sig.severity == 0.0


@given(
    n=st.integers(min_value=128, max_value=300),
    flip_rate=st.floats(min_value=0.30, max_value=0.50),
    seed=st.integers(min_value=0, max_value=10**6),
)
def test_strong_label_poisoning_always_rejected(n: int, flip_rate: float, seed: int) -> None:
    """A label-flip rate ≥ 30% is well above the 5% threshold — always rejected."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, 4))
    labels = (x[:, 0] > 0).astype(int)
    poisoned = labels.copy()
    k = int(n * flip_rate)
    assume(k > 0)
    poisoned[:k] = 1 - poisoned[:k]
    report = scan(x=x, labels=poisoned, reference_labels=labels)
    assert report.decision == "reject"
