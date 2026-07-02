"""
aegis.trust — PROJECT OMEGA Phase B (Trust Reconstruction)
==========================================================

Transforms model *confidence* into statistically-meaningful *trust*.

Pure, interpretable, no new predictive models and no neural networks. The
calibration primitives (ECE, MCE, Brier, isotonic / Platt maps) are deterministic
functions over ``(p, y)`` pairs; trust scores are aggregations of realized
calibration per entity (model / source / agent).

Doctrine (Evidence > Confidence): every metric here refuses to produce a number
when the input lacks the variance needed to make it meaningful. A reliability
diagram over a single confidence value returns ``insufficient_variance`` — never
a fake ECE. See :func:`calibration.calibration_report`.
"""

from __future__ import annotations

from aegis.trust.calibration import (
    brier_score,
    brier_skill_score,
    calibration_report,
    ece,
    isotonic_fit,
    mce,
    reliability_bins,
)
from aegis.trust.calibrator import Calibrator, rise_probability
from aegis.trust.schemas import (
    CalibrationReport,
    ReliabilityBin,
    TrustScore,
)

__version__ = "16.0.0"  # Phase B

__all__ = [
    "CalibrationReport",
    "Calibrator",
    "ReliabilityBin",
    "TrustScore",
    "brier_score",
    "rise_probability",
    "brier_skill_score",
    "calibration_report",
    "ece",
    "isotonic_fit",
    "mce",
    "reliability_bins",
]
