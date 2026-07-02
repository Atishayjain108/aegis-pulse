"""Phase B trust-score tests."""

from __future__ import annotations

import random

from aegis.trust.scores import PRIOR_TRUST, compute_trust


class TestComputeTrust:
    def test_no_outcomes_returns_prior(self) -> None:
        s = compute_trust([], [], entity_kind="model", entity_id="x")
        assert s.trust == PRIOR_TRUST
        assert s.n_outcomes == 0

    def test_constant_confidence_uses_prior_calibration_term(self) -> None:
        # constant 0.5 -> can't calibrate; trust must not be fabricated high
        s = compute_trust([0.5] * 100, [1.0 if i < 55 else 0.0 for i in range(100)],
                          entity_kind="source", entity_id="reddit")
        assert "insufficient confidence variance" in s.notes
        assert 0.0 <= s.trust <= 0.6

    def test_well_calibrated_entity_scores_higher_than_overconfident(self) -> None:
        rng = random.Random(0)
        # calibrated entity
        cp, cy = [], []
        for _ in range(800):
            p = rng.random()
            cp.append(p)
            cy.append(1.0 if rng.random() < p else 0.0)
        good = compute_trust(cp, cy, entity_kind="model", entity_id="good")
        # overconfident entity
        op = [round(rng.uniform(0.85, 1.0), 3) for _ in range(800)]
        oy = [1.0 if rng.random() < 0.4 else 0.0 for _ in range(800)]
        bad = compute_trust(op, oy, entity_kind="model", entity_id="bad")
        assert good.trust > bad.trust
        assert 0.0 <= bad.trust <= 1.0 and 0.0 <= good.trust <= 1.0
