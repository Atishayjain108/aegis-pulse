"""
High-level detection facade.

Wraps the individual detectors (honeypot, smoothing, poisoning) and converts
their typed outputs into the unified `HardenVerdict` schema consumed by the
Phase 4 alert composer.

Why this layer exists:
  * Phase 4 expects one outbound shape per source.
  * Phase 5's individual detectors return rich, domain-specific verdicts.
  * The mapping deserves explicit code rather than ad-hoc inline lambdas.

This module is the *only* place Phase 5 maps internal verdicts to the
Phase 4 vocabulary (`proceed` | `warn` | `block`), so the mapping rules are
auditable in one location.
"""

from __future__ import annotations

import numpy as np

from aegis.harden.honeypot import score_element, score_url
from aegis.harden.metrics import M
from aegis.harden.poisoning import scan as poisoning_scan
from aegis.harden.schemas import (
    HardenVerdict,
    HoneypotVerdict,
    PoisoningReport,
    SmoothingResult,
)
from aegis.harden.smoothing import ScalarBase, smooth_predict
from aegis.harden.utils.rng import SeededRng

# ---------------------------------------------------------------------------
# Mappers — internal verdict → unified `HardenVerdict`
# ---------------------------------------------------------------------------


def honeypot_to_verdict(v: HoneypotVerdict, *, trend_id: str | None = None) -> HardenVerdict:
    """Map a honeypot verdict to the unified outbound type."""
    if v.blocked:
        verdict = "block"
    elif v.warn:
        verdict = "warn"
    else:
        verdict = "proceed"
    reason_code = v.reasons[0].split(":", 1)[0] if v.reasons else "no-signal"
    out = HardenVerdict(
        trend_id=trend_id,
        source="honeypot",
        verdict=verdict,  # type: ignore[arg-type]
        score=v.score,
        # Confidence is high when the score is near a boundary, low when ambiguous.
        confidence=_boundary_confidence(v.score),
        reason_code=reason_code,
        detail=f"url={v.url[:200]} reasons={','.join(v.reasons[:5])}",
    )
    M.harden_verdicts_total.labels(source="honeypot", verdict=out.verdict).inc()
    return out


def smoothing_to_verdict(r: SmoothingResult, *, trend_id: str | None = None) -> HardenVerdict:
    """Map a smoothing result to the unified outbound type.

    A smoothing run that disagrees with the raw verdict is a `warn` —
    the underlying prediction is unstable under small perturbations.
    """
    if not r.agrees_with_raw:
        verdict = "warn"
    else:
        verdict = "proceed"
    out = HardenVerdict(
        trend_id=trend_id,
        source="smoothing",
        verdict=verdict,  # type: ignore[arg-type]
        score=r.smoothed_score,
        confidence=float(min(1.0, max(0.0, 1.0 - abs(r.raw_score - r.smoothed_score) * 2))),
        reason_code="smoothing-stable" if r.agrees_with_raw else "smoothing-unstable",
        detail=f"sigma={r.sigma:.3f} radius={r.certified_radius:.4f} raw={r.raw_score:.3f} smooth={r.smoothed_score:.3f}",
    )
    M.harden_verdicts_total.labels(source="smoothing", verdict=out.verdict).inc()
    return out


def poisoning_to_verdict(r: PoisoningReport, *, trend_id: str | None = None) -> HardenVerdict:
    """Map a poisoning report to the unified outbound type."""
    if r.decision == "reject":
        verdict = "block"
    elif r.decision == "warn":
        verdict = "warn"
    else:
        verdict = "proceed"
    worst = max((s.severity for s in r.signals), default=0.0)
    flagged = ",".join(s.detector for s in r.signals if s.flagged) or "none"
    out = HardenVerdict(
        trend_id=trend_id,
        source="poisoning",
        verdict=verdict,  # type: ignore[arg-type]
        score=float(worst),
        confidence=1.0 if r.n_samples >= 256 else (r.n_samples / 256.0),
        reason_code=f"poisoning-{r.decision}",
        detail=f"batch={r.batch_id} n={r.n_samples} flagged={flagged}",
    )
    M.harden_verdicts_total.labels(source="poisoning", verdict=out.verdict).inc()
    return out


# ---------------------------------------------------------------------------
# Convenience entry points — Phase 1/3/4 integration helpers
# ---------------------------------------------------------------------------


def screen_url(url: str, *, trend_id: str | None = None) -> HardenVerdict:
    """Phase 1 helper: returns a HardenVerdict for a URL pre-fetch."""
    return honeypot_to_verdict(score_url(url), trend_id=trend_id)


def screen_dom_element(el: dict, *, trend_id: str | None = None) -> HardenVerdict:
    """Phase 1 helper: returns a HardenVerdict for a DOM element snapshot."""
    return honeypot_to_verdict(score_element(el), trend_id=trend_id)  # type: ignore[arg-type]


def screen_inference(
    base_clf: ScalarBase,
    x: np.ndarray,
    *,
    sigma: float | None = None,
    n_samples: int | None = None,
    rng: SeededRng | None = None,
    trend_id: str | None = None,
) -> HardenVerdict:
    """Phase 3 helper: wraps a single inference in randomized smoothing.

    `base_clf` should be a callable returning a scalar in [0,1]. To use a
    Phase 3 InferenceRunner, supply an adapter like:
        def base(v: np.ndarray) -> float:
            fw = FeatureWindow.from_vector(v)        # caller's adapter
            return runner.infer_one(fw).p_breakout_at(24)
    """
    kwargs: dict = {}
    if sigma is not None:
        kwargs["sigma"] = sigma
    if n_samples is not None:
        kwargs["n_samples"] = n_samples
    if rng is not None:
        kwargs["rng"] = rng
    r = smooth_predict(base_clf, x, **kwargs)
    return smoothing_to_verdict(r, trend_id=trend_id)


def screen_training_batch(
    x: np.ndarray,
    *,
    labels: np.ndarray | None = None,
    reference_labels: np.ndarray | None = None,
    x_ref: np.ndarray | None = None,
    batch_id: str | None = None,
    trend_id: str | None = None,
) -> HardenVerdict:
    """Phase 3 training-time helper: scan a batch for poisoning."""
    report = poisoning_scan(
        x=x,
        labels=labels,
        reference_labels=reference_labels,
        x_ref=x_ref,
        batch_id=batch_id,
    )
    return poisoning_to_verdict(report, trend_id=trend_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _boundary_confidence(score: float) -> float:
    """Confidence peaks at 0 and 1, dips near the block boundary.

    Calibrated so a clean URL (score=0.0) has confidence ~1.0, a blocked URL
    (score=1.0) has confidence ~1.0, and an ambiguous URL near 0.5 has
    confidence ~0.0.
    """
    s = max(0.0, min(1.0, float(score)))
    # distance from the nearest extreme (0 or 1), scaled to [0,1]:
    # s=0 → 1.0, s=1 → 1.0, s=0.5 → 0.0.
    return float(1.0 - 2.0 * min(s, 1.0 - s))


__all__ = [
    "honeypot_to_verdict",
    "poisoning_to_verdict",
    "screen_dom_element",
    "screen_inference",
    "screen_training_batch",
    "screen_url",
    "smoothing_to_verdict",
]
