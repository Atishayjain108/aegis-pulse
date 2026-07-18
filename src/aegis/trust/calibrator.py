"""
aegis.trust.calibrator — make the heuristic's confidence truthful (Phase C)
===========================================================================

The forensic audit (CONFIDENCE_AUDIT.md) established two facts on the realized
outcomes:

  * the model's ``p_breakout`` / evidence-volume ``confidence`` do NOT discriminate
    the realized "signal rises" label (negative out-of-sample Brier skill), but
  * ``1 - p_decline`` DOES (discrimination +0.164, the only formulation with
    positive out-of-sample Brier skill).

So the truthful probability the trend rises is ``rise_probability()`` —
``1 - p_decline`` — passed through an isotonic calibration map fitted on realized
outcomes. This module owns that map: fit it, persist it, apply it. No new model,
no neural network — an isotonic map is a monotone lookup table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from aegis.trust.calibration import (
    MIN_DISTINCT_CONFIDENCES,
    MIN_OUTCOMES_FOR_REPORT,
    isotonic_apply,
    isotonic_fit,
)


def rise_probability(p_decline: float) -> float:
    """The audited, discriminating estimate of P(signal rises): 1 - p_decline."""
    return max(0.0, min(1.0, 1.0 - p_decline))


@dataclass(frozen=True)
class Calibrator:
    """A fitted, persistable isotonic calibration map.

    ``knots`` is empty for the identity map (used until enough outcomes exist —
    we never invent calibration out of thin data).
    """

    knots: list[tuple[float, float]]
    n_fit: int = 0
    base_rate: float = 0.5

    @classmethod
    def identity(cls) -> Calibrator:
        return cls(knots=[], n_fit=0, base_rate=0.5)

    @classmethod
    def fit(cls, ps: list[float], ys: list[float]) -> Calibrator:
        """Fit an isotonic map. Falls back to identity when data is too thin or
        lacks confidence variance (Evidence > Confidence — never fabricate)."""
        n = len(ps)
        base = sum(ys) / n if n else 0.5
        if n < MIN_OUTCOMES_FOR_REPORT:
            return cls(knots=[], n_fit=n, base_rate=base)
        if len({round(p, 4) for p in ps}) < MIN_DISTINCT_CONFIDENCES:
            return cls(knots=[], n_fit=n, base_rate=base)
        return cls(knots=isotonic_fit(ps, ys), n_fit=n, base_rate=base)

    def apply(self, p: float) -> float:
        """Map a raw probability to its calibrated value.

        Identity map shrinks toward the base rate by 50% so a thin-data model is
        honestly hedged rather than passing raw over/under-confidence through.
        """
        if not self.knots:
            return self.base_rate + 0.5 * (p - self.base_rate)
        return isotonic_apply(self.knots, p)

    def to_json(self) -> str:
        return json.dumps(
            {"knots": self.knots, "n_fit": self.n_fit, "base_rate": self.base_rate}
        )

    @classmethod
    def from_json(cls, raw: str | bytes | dict) -> Calibrator:
        # knots_json is a JSONB column: asyncpg auto-decodes it to a dict.
        # This method was only ever fed strings by tests — the first REAL
        # production load (2026-07-18, after set_shared_pool wiring) crashed
        # with "the JSON object must be str, ... not dict". Accept both.
        d = raw if isinstance(raw, dict) else json.loads(raw)
        return cls(
            knots=[(float(a), float(b)) for a, b in d.get("knots", [])],
            n_fit=int(d.get("n_fit", 0)),
            base_rate=float(d.get("base_rate", 0.5)),
        )
