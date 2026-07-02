"""Phase C — Calibrator + rise_probability + the class-prob >1.0 bug fix."""

from __future__ import annotations

import random

from aegis.predict import FEATURE_NAMES  # noqa: F401  (ensures predict importable)
from aegis.trust.calibrator import Calibrator, rise_probability


class TestRiseProbability:
    def test_is_one_minus_p_decline(self) -> None:
        assert rise_probability(0.0) == 1.0
        assert rise_probability(1.0) == 0.0
        assert abs(rise_probability(0.3) - 0.7) < 1e-9

    def test_clamped(self) -> None:
        assert rise_probability(1.5) == 0.0
        assert rise_probability(-0.5) == 1.0


class TestCalibrator:
    def test_identity_shrinks_toward_base_rate(self) -> None:
        c = Calibrator.identity()
        # identity hedges 50% toward base_rate (0.5) — never passes raw through
        assert c.apply(1.0) == 0.75
        assert c.apply(0.0) == 0.25

    def test_thin_data_returns_identity(self) -> None:
        c = Calibrator.fit([0.6, 0.4], [1, 0])  # < MIN_OUTCOMES
        assert c.knots == []
        assert c.n_fit == 2

    def test_constant_confidence_returns_identity(self) -> None:
        c = Calibrator.fit([0.5] * 100, [1.0 if i < 55 else 0.0 for i in range(100)])
        assert c.knots == []  # insufficient variance -> no fabricated map

    def test_fitted_map_calibrates_overconfident_stream(self) -> None:
        rng = random.Random(0)
        ps = [round(rng.uniform(0.8, 1.0), 3) for _ in range(500)]
        ys = [1.0 if rng.random() < 0.5 else 0.0 for _ in range(500)]
        c = Calibrator.fit(ps, ys)
        assert c.knots  # real map fitted
        # calibrated value for a high raw p should land near the true ~0.5 rate
        assert c.apply(0.95) < 0.7

    def test_roundtrip_json(self) -> None:
        rng = random.Random(1)
        ps = [rng.random() for _ in range(60)]
        ys = [1.0 if rng.random() < p else 0.0 for p in ps]
        c = Calibrator.fit(ps, ys)
        c2 = Calibrator.from_json(c.to_json())
        assert c2.knots == c.knots
        assert c2.base_rate == c.base_rate


class TestClassProbBugfix:
    def test_no_stage_confidence_combo_exceeds_one(self) -> None:
        from aegis.predict.constants import HEURISTIC_CONFIDENCE_CEILING
        from aegis.predict.models.heuristic import _stage_to_class_probs
        from aegis.predict.schemas import TrendStage

        for stage in TrendStage:
            for conf in (0.0, 0.25, 0.5, HEURISTIC_CONFIDENCE_CEILING, 1.0):
                pb, pp, pd = _stage_to_class_probs(stage, confidence=conf)
                total = pb + pp + pd
                assert total <= 1.0001, f"{stage} @ conf={conf} sums to {total}"
                assert all(0.0 <= x <= 1.0 for x in (pb, pp, pd))
