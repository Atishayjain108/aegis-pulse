"""Kelly advisor tests."""

from __future__ import annotations

import pytest

from aegis.execute.sizing.kelly import KellyAdvisor, SizingResult


def test_invalid_fraction_raises():
    with pytest.raises(ValueError):
        KellyAdvisor(fraction=0)
    with pytest.raises(ValueError):
        KellyAdvisor(fraction=1.5)


def test_invalid_max_pct_raises():
    with pytest.raises(ValueError):
        KellyAdvisor(max_pct_of_capital=0)
    with pytest.raises(ValueError):
        KellyAdvisor(max_pct_of_capital=1.5)


def test_missing_inputs_returns_zero():
    a = KellyAdvisor()
    r = a.advise(
        expected_margin_usd=None,
        loss_probability=0.2,
        unit_cost_usd=10.0,
    )
    assert r.units == 0
    assert r.kelly_fraction_used == 0


def test_zero_capital_or_cost_returns_zero():
    a = KellyAdvisor()
    r = a.advise(
        expected_margin_usd=5.0,
        loss_probability=0.1,
        unit_cost_usd=0.0,
    )
    assert r.units == 0


def test_positive_kelly_under_max_pct():
    a = KellyAdvisor(fraction=0.25, max_pct_of_capital=0.50)
    # p_win=0.9, win_payoff=2.0, loss_payoff=1.0  ⇒  raw=0.85
    r = a.advise(
        expected_margin_usd=2.0,
        loss_probability=0.1,
        unit_cost_usd=1.0,
        capital_usd=1000.0,
    )
    assert r.units > 0
    assert r.kelly_fraction_raw == pytest.approx(0.85, abs=1e-9)
    # Safe = 0.25 * 0.85 = 0.2125 → under 0.50 cap.
    assert r.kelly_fraction_used == pytest.approx(0.2125, abs=1e-9)


def test_negative_kelly_yields_zero_units():
    a = KellyAdvisor()
    # Low win prob → negative raw Kelly
    r = a.advise(
        expected_margin_usd=1.0,
        loss_probability=0.95,
        unit_cost_usd=10.0,
        capital_usd=1000.0,
    )
    assert r.units == 0
    assert r.kelly_fraction_raw < 0


def test_capped_by_max_pct():
    # Force raw fraction > max
    a = KellyAdvisor(fraction=1.0, max_pct_of_capital=0.05)
    r = a.advise(
        expected_margin_usd=100.0,
        loss_probability=0.01,
        unit_cost_usd=1.0,
        capital_usd=1000.0,
    )
    assert r.kelly_fraction_used == pytest.approx(0.05)
    # 0.05 * 1000 / 1 = 50 units
    assert r.units == 50


def test_result_is_a_sizing_result():
    r = KellyAdvisor().advise(
        expected_margin_usd=2.0,
        loss_probability=0.2,
        unit_cost_usd=1.0,
    )
    assert isinstance(r, SizingResult)
    assert isinstance(r.rationale, str) and len(r.rationale) > 0
