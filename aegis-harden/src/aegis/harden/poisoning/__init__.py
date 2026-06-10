"""
Data-poisoning detectors for the Phase 3 training pipeline.

Three layered detectors, ordered cheapest-first:

  1. `LabelFlipDetector` — given a batch's labels and a reference policy
     (the heuristic floor), measures the fraction of disagreement. A clean
     dataset should disagree with the heuristic on a small, stable fraction
     of samples; sudden spikes indicate label injection.

  2. `FeatureShiftDetector` — per-column z-score of the batch's mean vs a
     held-out reference batch. Any column whose absolute z exceeds
     `POISONING_FEATURE_SHIFT_Z_MAX` flags the batch.

  3. `GradientAnomalyDetector` — computes a per-sample "influence proxy"
     (here: distance to the batch median in standardized feature space) and
     flags the batch if the top-k contains a sample with z >= threshold.
     This is the classic Steinhardt-style defense, kept simple to avoid
     introducing PyTorch as a dependency.

Each detector is a pure function over numpy arrays. The unified `scan()`
runs all three and returns a `PoisoningReport`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from aegis.harden.constants import (
    POISONING_FEATURE_SHIFT_Z_MAX,
    POISONING_LABEL_FLIP_MAX,
    POISONING_MIN_SAMPLE_FLOOR,
)
from aegis.harden.errors import PoisoningDetected, make
from aegis.harden.metrics import M
from aegis.harden.schemas import PoisoningReport, PoisoningSignal

# ---------------------------------------------------------------------------
# Detector dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LabelFlipDetector:
    """Detects elevated disagreement between actual labels and a reference policy.

    `reference_labels` is the labels produced by the deterministic heuristic
    on the same feature batch — i.e., the heuristic floor's verdict. If real
    labels disagree with the heuristic at a much higher rate than expected,
    someone is likely poisoning the labels.
    """

    max_flip_rate: float = POISONING_LABEL_FLIP_MAX

    def signal(self, labels: np.ndarray, reference_labels: np.ndarray) -> PoisoningSignal:
        if labels.shape != reference_labels.shape:
            raise ValueError(f"shape mismatch: {labels.shape} vs {reference_labels.shape}")
        if labels.size == 0:
            return PoisoningSignal(detector="label_flip", flagged=False, severity=0.0)
        flips = float(np.mean(labels != reference_labels))
        flagged = flips > self.max_flip_rate
        severity = min(1.0, flips / max(self.max_flip_rate, 1e-9))
        return PoisoningSignal(
            detector="label_flip",
            flagged=flagged,
            severity=float(severity),
            evidence={"flip_rate": flips, "threshold": self.max_flip_rate},
        )


@dataclass(frozen=True, slots=True)
class FeatureShiftDetector:
    """Detects per-column mean shift between batch and reference."""

    max_z: float = POISONING_FEATURE_SHIFT_Z_MAX

    def signal(self, x: np.ndarray, x_ref: np.ndarray) -> PoisoningSignal:
        if x.ndim != 2 or x_ref.ndim != 2:
            raise ValueError(f"expected 2-D arrays, got {x.shape}, {x_ref.shape}")
        if x.shape[1] != x_ref.shape[1]:
            raise ValueError(f"feature dim mismatch: {x.shape[1]} vs {x_ref.shape[1]}")
        if x.shape[0] == 0 or x_ref.shape[0] == 0:
            return PoisoningSignal(detector="feature_shift", flagged=False, severity=0.0)

        mu_x = x.mean(axis=0)
        mu_ref = x_ref.mean(axis=0)
        sd_ref = x_ref.std(axis=0, ddof=1) if x_ref.shape[0] > 1 else np.ones(x_ref.shape[1])
        # Avoid divide-by-zero for constant columns.
        sd_ref = np.where(sd_ref < 1e-9, 1.0, sd_ref)
        # Standard error of the mean estimate.
        sem = sd_ref / np.sqrt(x.shape[0])
        z = np.abs(mu_x - mu_ref) / np.where(sem < 1e-9, 1.0, sem)
        z_max = float(z.max())
        worst = int(np.argmax(z))
        flagged = z_max > self.max_z
        severity = min(1.0, z_max / max(self.max_z * 2.0, 1e-9))
        return PoisoningSignal(
            detector="feature_shift",
            flagged=flagged,
            severity=float(severity),
            evidence={
                "z_max": z_max,
                "z_threshold": self.max_z,
                "worst_column": float(worst),
            },
        )


@dataclass(frozen=True, slots=True)
class GradientAnomalyDetector:
    """Distance-to-median outlier detector (gradient-influence proxy).

    Standardizes the batch column-wise, then computes the per-sample L2
    distance to the standardized median. Flags the batch if the top-k
    samples are above `z_threshold` standard deviations.
    """

    z_threshold: float = 5.0
    top_k: int = 3

    def signal(self, x: np.ndarray) -> PoisoningSignal:
        if x.ndim != 2:
            raise ValueError(f"expected 2-D array, got {x.shape}")
        if x.shape[0] == 0:
            return PoisoningSignal(detector="gradient_anomaly", flagged=False, severity=0.0)
        med = np.median(x, axis=0)
        sd = x.std(axis=0, ddof=1) if x.shape[0] > 1 else np.ones(x.shape[1])
        sd = np.where(sd < 1e-9, 1.0, sd)
        standardized = (x - med) / sd
        distances = np.linalg.norm(standardized, axis=1)
        if distances.size == 0:
            return PoisoningSignal(detector="gradient_anomaly", flagged=False, severity=0.0)
        # Compare to per-batch distribution: many high distances => poisoning.
        threshold = float(self.z_threshold) * float(np.sqrt(x.shape[1]))
        top = np.sort(distances)[-min(self.top_k, distances.size) :]
        over = int(np.sum(top > threshold))
        flagged = over >= 1
        severity = min(1.0, over / max(self.top_k, 1))
        return PoisoningSignal(
            detector="gradient_anomaly",
            flagged=flagged,
            severity=float(severity),
            evidence={
                "threshold": threshold,
                "top_max_distance": float(top.max()) if top.size else 0.0,
                "over_count": float(over),
            },
        )


# ---------------------------------------------------------------------------
# Unified scan
# ---------------------------------------------------------------------------


def scan(
    *,
    x: np.ndarray,
    labels: np.ndarray | None = None,
    reference_labels: np.ndarray | None = None,
    x_ref: np.ndarray | None = None,
    batch_id: str | None = None,
    detectors: Sequence[LabelFlipDetector | FeatureShiftDetector | GradientAnomalyDetector]
    | None = None,
) -> PoisoningReport:
    """Run all applicable detectors and return a `PoisoningReport`.

    Decision rule:
      * `reject` if any detector flags AND batch size ≥ floor.
      * `warn` if any detector has severity ≥ 0.6 but did not flag.
      * `accept` otherwise.

    Notes
    -----
    * If `x.shape[0]` < `POISONING_MIN_SAMPLE_FLOOR`, all detectors return
      non-flagged signals and the report is `accept` with a low-confidence note.
    * The function never raises on small batches — it just refuses to flag.
    """
    bid = batch_id or uuid.uuid4().hex
    n = int(x.shape[0]) if x.ndim == 2 else 0
    if x.ndim != 2:
        raise ValueError(f"x must be 2-D, got shape {x.shape}")

    too_small = n < POISONING_MIN_SAMPLE_FLOOR

    detectors = detectors or (
        LabelFlipDetector(),
        FeatureShiftDetector(),
        GradientAnomalyDetector(),
    )

    signals: list[PoisoningSignal] = []
    for d in detectors:
        if isinstance(d, LabelFlipDetector):
            if labels is None or reference_labels is None:
                continue
            sig = d.signal(labels, reference_labels)
        elif isinstance(d, FeatureShiftDetector):
            if x_ref is None:
                continue
            sig = d.signal(x, x_ref)
        elif isinstance(d, GradientAnomalyDetector):
            sig = d.signal(x)
        else:  # pragma: no cover — unreachable
            continue
        # Suppress flags when the batch is too small to draw conclusions.
        if too_small and sig.flagged:
            sig = PoisoningSignal(
                detector=sig.detector,
                flagged=False,
                severity=sig.severity,
                evidence={**sig.evidence, "suppressed": 1.0},
            )
        signals.append(sig)

    any_flag = any(s.flagged for s in signals)
    high_sev = any(s.severity >= 0.6 for s in signals)
    decision: str
    if any_flag and not too_small:
        decision = "reject"
    elif high_sev:
        decision = "warn"
    else:
        decision = "accept"

    M.poisoning_reports_total.labels(decision=decision).inc()
    return PoisoningReport(
        batch_id=bid,
        n_samples=n,
        signals=tuple(signals),
        decision=decision,  # type: ignore[arg-type]
    )


def require_clean(report: PoisoningReport) -> None:
    """Raise `PoisoningDetected` if the report says `reject`."""
    if report.decision == "reject":
        raise PoisoningDetected(
            *make(
                "AEGIS-HARDEN-0040",
                batch_id=report.batch_id,
                signals=[s.detector for s in report.signals if s.flagged],
            ),
        )
    if report.n_samples < POISONING_MIN_SAMPLE_FLOOR:
        # Caller may want to know it was suppressed.
        return


__all__ = [
    "FeatureShiftDetector",
    "GradientAnomalyDetector",
    "LabelFlipDetector",
    "require_clean",
    "scan",
]
