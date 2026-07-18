"""Tests for the M5 FinanceOp (unit economics / PnL / capital fit / runway)."""

from __future__ import annotations

from typing import Any

import pytest

from aegis.mentor.operators import FinanceOp, OperatorContext, OperatorResult
from aegis.mentor.packs.d2c_india import D2CIndiaPack
from aegis.mentor.schemas import Intent, UserProfile


def _profile(**kw: Any) -> UserProfile:
    base = {"sector": "d2c_india", "sector_raw": "wireless earbuds", "intent": Intent.INCOME}
    base.update(kw)
    return UserProfile(**base)


def _ctx(finance_inputs: dict[str, Any] | None = None, pack: bool = True) -> OperatorContext:
    extras = {"finance_inputs": finance_inputs} if finance_inputs is not None else {}
    return OperatorContext(
        sector_pack=D2CIndiaPack() if pack else None, depth="deep", extras=extras
    )


class TestFinanceOp:
    @pytest.mark.asyncio
    async def test_silent_without_cost_and_price(self) -> None:
        res = await FinanceOp().run(_profile(), "unit economics", _ctx())
        assert isinstance(res, OperatorResult)
        assert res.confidence == 0.0
        assert res.data["capital_moved"] is False
        assert "grounded-or-silent" in res.reasoning.lower()

    @pytest.mark.asyncio
    async def test_unit_economics_and_breakeven(self) -> None:
        res = await FinanceOp().run(
            _profile(), "p&l",
            _ctx({"unit_cost_usd": 8.0, "unit_price_usd": 20.0,
                  "fixed_costs_usd": 1200.0, "expected_units": 200}),
        )
        assert res.data["margin_per_unit_usd"] == pytest.approx(12.0)
        assert res.data["margin_pct"] == pytest.approx(60.0)
        # 1200 fixed / 12 margin = 100 units to break even.
        assert res.data["breakeven_units"] == 100
        pnl = res.data["pnl"]
        assert pnl["gross_profit_usd"] == pytest.approx(2400.0)
        assert pnl["net_profit_usd"] == pytest.approx(1200.0)

    @pytest.mark.asyncio
    async def test_capital_fit_short_is_flagged(self) -> None:
        # ₹8300 capital ≈ $100 (÷83). 50 units × $8 = $400 upfront → short.
        res = await FinanceOp().run(
            _profile(capital_usd=8300.0, currency="INR"), "capital fit",
            _ctx({"unit_cost_usd": 8.0, "unit_price_usd": 20.0, "expected_units": 50}),
        )
        fit = res.data["capital_fit"]
        assert fit["available_usd"] == pytest.approx(100.0, abs=0.5)
        assert fit["upfront_usd"] == pytest.approx(400.0)
        assert fit["fits"] is False
        assert any("SHORT" in f for f in res.findings)
        assert any("start smaller" in a.lower() for a in res.actions)

    @pytest.mark.asyncio
    async def test_negative_margin_breakeven_impossible(self) -> None:
        res = await FinanceOp().run(
            _profile(), "p&l",
            _ctx({"unit_cost_usd": 20.0, "unit_price_usd": 18.0,
                  "fixed_costs_usd": 500.0, "expected_units": 10}),
        )
        assert res.data["breakeven_units"] is None
        assert any("impossible" in f.lower() for f in res.findings)

    @pytest.mark.asyncio
    async def test_runway_computed_from_burn(self) -> None:
        res = await FinanceOp().run(
            _profile(capital_usd=8300.0, currency="INR"), "runway",
            _ctx({"unit_cost_usd": 8.0, "unit_price_usd": 20.0,
                  "monthly_burn_usd": 25.0}),
        )
        # $100 capital / $25 burn = 4 months.
        assert res.data["runway_months"] == pytest.approx(4.0, abs=0.2)

    @pytest.mark.asyncio
    async def test_below_floor_margin_flagged(self) -> None:
        res = await FinanceOp().run(
            _profile(), "margin",
            _ctx({"unit_cost_usd": 9.0, "unit_price_usd": 10.0}),  # 10% margin
        )
        assert any("below the sector" in f.lower() for f in res.findings)
        assert res.data["capital_moved"] is False

    @pytest.mark.asyncio
    async def test_no_capital_moved_ever(self) -> None:
        res = await FinanceOp().run(
            _profile(), "finance",
            _ctx({"unit_cost_usd": 8.0, "unit_price_usd": 20.0}),
        )
        assert res.data["capital_moved"] is False
        assert any("no capital is moved" in a.lower() for a in res.actions)
