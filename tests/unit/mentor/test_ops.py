"""Tests for the M5 OpsOp (pricing + advisory plan; capital stays gated)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aegis.mentor.operators import OperatorContext, OperatorResult, OpsOp
from aegis.mentor.packs.d2c_india import D2CIndiaPack
from aegis.mentor.schemas import Intent, UserProfile


def _profile(**kw: Any) -> UserProfile:
    base = {"sector": "d2c_india", "sector_raw": "wireless earbuds", "intent": Intent.INCOME}
    base.update(kw)
    return UserProfile(**base)


def _ctx(ops_inputs: dict[str, Any] | None = None, **kw: Any) -> OperatorContext:
    extras = {"ops_inputs": ops_inputs} if ops_inputs is not None else {}
    base: dict[str, Any] = {"sector_pack": D2CIndiaPack(), "depth": "deep", "extras": extras}
    base.update(kw)
    return OperatorContext(**base)


# --- Fakes ----------------------------------------------------------------


class _FakePricer:
    def compute_price(
        self, unit_cost: float, demand: float, inventory: int,
        competitors: list[float] | None,
    ) -> float:
        return unit_cost * 2.5  # deterministic 60% margin


class _FakeEngine:
    """Advisory engine: never executes, always returns an advisory plan."""

    def __init__(self) -> None:
        self.executed = False

    async def create_plan(self, intent: Any) -> Any:
        return SimpleNamespace(
            execution_mode="advisory",
            quantity=10,
            total_capital_usd=80.0,
            estimated_profit_usd=120.0,
            requires_approval=True,
            supplier_name=None,
        )

    async def execute_plan(self, plan: Any) -> Any:  # pragma: no cover - must never run
        self.executed = True
        raise AssertionError("OpsOp must never execute a plan")


# --- Tests ----------------------------------------------------------------


class TestOpsOp:
    @pytest.mark.asyncio
    async def test_silent_without_verified_cost(self) -> None:
        op = OpsOp(pricer=_FakePricer(), engine=_FakeEngine())
        res = await op.run(_profile(), "how much should i charge", _ctx())
        assert isinstance(res, OperatorResult)
        assert res.confidence == 0.0
        assert res.data["pricing"] is None
        assert res.data["capital_gated"] is True
        assert "grounded-or-silent" in res.reasoning.lower()

    @pytest.mark.asyncio
    async def test_prices_off_real_cost(self) -> None:
        op = OpsOp(pricer=_FakePricer(), engine=_FakeEngine())
        res = await op.run(
            _profile(), "pricing",
            _ctx({"unit_cost_usd": 8.0, "demand_units_per_h": 2.0, "expected_units": 10}),
        )
        pricing = res.data["pricing"]
        assert pricing is not None
        assert pricing["price_usd"] == pytest.approx(20.0)
        assert pricing["margin_pct"] == pytest.approx(60.0)
        assert "execute:pricing_strategy" in res.sources
        assert any("Recommended price" in f for f in res.findings)

    @pytest.mark.asyncio
    async def test_advisory_plan_is_gated_and_never_executed(self) -> None:
        engine = _FakeEngine()
        op = OpsOp(pricer=_FakePricer(), engine=engine)
        res = await op.run(
            _profile(), "ops plan",
            _ctx({"unit_cost_usd": 8.0, "expected_units": 10}),
        )
        plan = res.data["plan"]
        assert plan is not None
        assert plan["execution_mode"] == "advisory"
        assert plan["requires_approval"] is True
        assert res.data["executed"] is False
        assert res.data["capital_gated"] is True
        assert engine.executed is False
        assert any("ADVISORY ONLY" in a for a in res.actions)

    @pytest.mark.asyncio
    async def test_margin_below_sector_floor_is_flagged(self) -> None:
        class _ThinPricer:
            def compute_price(self, cost, d, inv, comp) -> float:
                return cost * 1.2  # ~17% margin — below the 40% sector floor

        op = OpsOp(pricer=_ThinPricer(), engine=None)
        res = await op.run(
            _profile(), "pricing", _ctx({"unit_cost_usd": 10.0})
        )
        assert any("BELOW the sector" in f for f in res.findings)

    @pytest.mark.asyncio
    async def test_prices_even_when_plan_sketch_unavailable(self) -> None:
        # Settings explicitly absent → no advisory plan sketch, but pricing still
        # works off the real cost (grounded). Capital stays gated regardless.
        op = OpsOp(pricer=_FakePricer(), engine=None, settings=False)
        res = await op.run(_profile(), "pricing", _ctx({"unit_cost_usd": 5.0}))
        assert res.data["pricing"] is not None
        assert res.data["plan"] is None
        assert res.data["capital_gated"] is True
        assert res.has_signal
