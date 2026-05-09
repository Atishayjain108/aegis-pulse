"""Tests for the deterministic tools."""
from __future__ import annotations

from aegis.agents.tools.compliance_check import check
from aegis.agents.tools.monte_carlo import MarginPriors, simulate
from aegis.agents.tools.velocity import classify


class TestVelocityClassify:
    async def test_flat(self) -> None:
        result = await classify(velocity_1h=0.0, velocity_6h=0.0, velocity_24h=0.0)
        assert result.ok
        assert result.data["class"] == "flat"
        assert result.data["breakout_score"] >= 0.0

    async def test_rising(self) -> None:
        result = await classify(velocity_1h=10.0, velocity_6h=30.0, velocity_24h=80.0)
        assert result.ok
        # 10/h is just above LOW threshold (5).
        assert result.data["class"] in {"rising", "warm"}

    async def test_breakout_requires_sustained(self) -> None:
        # 1h velocity huge, 24h essentially zero → not "breakout"
        # even though magnitude qualifies.
        result = await classify(velocity_1h=600.0, velocity_6h=10.0, velocity_24h=0.0)
        assert result.data["class"] != "breakout"

    async def test_breakout_with_sustain(self) -> None:
        result = await classify(
            velocity_1h=600.0, velocity_6h=2400.0, velocity_24h=8000.0
        )
        assert result.data["class"] == "breakout"

    async def test_score_in_unit_interval(self) -> None:
        result = await classify(
            velocity_1h=10000.0, velocity_6h=50000.0, velocity_24h=150000.0
        )
        assert 0.0 <= result.data["breakout_score"] <= 1.0

    async def test_acceleration_clamped(self) -> None:
        # Pathological case: 1h=100, 6h=0 → infinite-ish acceleration.
        result = await classify(velocity_1h=100.0, velocity_6h=0.0, velocity_24h=200.0)
        assert -1.0 <= result.data["acceleration"] <= 5.0


class TestMonteCarlo:
    async def test_runs_with_defaults(self) -> None:
        result = await simulate(iterations=200, seed=42)
        assert result.ok
        assert result.data["iterations"] == 200
        assert "p10" in result.data
        assert "p50" in result.data
        assert "p90" in result.data
        assert result.data["p10"] <= result.data["p50"] <= result.data["p90"]

    async def test_seed_is_deterministic(self) -> None:
        a = await simulate(iterations=500, seed=12345)
        b = await simulate(iterations=500, seed=12345)
        assert a.data == b.data

    async def test_different_seeds_differ(self) -> None:
        a = await simulate(iterations=500, seed=1)
        b = await simulate(iterations=500, seed=2)
        # Highly unlikely to be equal at 500 iterations.
        assert a.data["p50"] != b.data["p50"] or a.data["mean"] != b.data["mean"]

    async def test_higher_price_lifts_margin(self) -> None:
        cheap = await simulate(iterations=2000, sell_price=15.0, seed=7)
        rich = await simulate(iterations=2000, sell_price=60.0, seed=7)
        assert rich.data["mean"] > cheap.data["mean"]

    async def test_iterations_clamped(self) -> None:
        # Above 100k → clamps to 100k. Below 100 → clamps up to 100.
        small = await simulate(iterations=10, seed=1)
        assert small.data["iterations"] == 100
        # We don't actually run 100k for speed; just verify the clamp
        # happens (we probe the priors path).

    async def test_priors_override(self) -> None:
        priors = MarginPriors(sell_price=100.0, cost_mean=10.0)
        result = await simulate(iterations=1000, priors=priors, seed=11)
        # With $90 gross margin baseline, mean should be solidly positive.
        assert result.data["mean"] > 10.0


class TestComplianceCheck:
    async def test_clean_proceeds(self) -> None:
        result = await check(title="Bamboo notebook", summary="Recycled paper notebook")
        assert result.ok
        assert result.data["verdict"] == "proceed"
        assert result.data["flags"] == []

    async def test_trademark_blocks(self) -> None:
        result = await check(title="Custom Nike sneaker design")
        assert result.data["verdict"] == "block"
        assert any(f.startswith("tm:") for f in result.data["flags"])

    async def test_apple_negative_lookahead(self) -> None:
        # "Apple pie" must NOT trigger trademark.
        result = await check(
            title="Best apple pie recipe", summary="Make great apple pie at home"
        )
        assert result.data["verdict"] != "block"

    async def test_apple_brand_does_block(self) -> None:
        result = await check(title="Knockoff Apple charging cable")
        assert result.data["verdict"] == "block"

    async def test_regulated_health_claim(self) -> None:
        result = await check(
            title="Miracle cream",
            summary="Cures eczema in 7 days, FDA-approved",
        )
        assert result.data["verdict"] == "block"
        flags = " ".join(result.data["flags"])
        assert "ftc" in flags or "fda" in flags

    async def test_due_diligence_holds(self) -> None:
        result = await check(
            title="Premium baby teething ring", summary="Soft silicone for infants"
        )
        assert result.data["verdict"] == "hold"
        assert any("dd:" in f for f in result.data["flags"])

    async def test_counterfeit_risk_flags(self) -> None:
        result = await check(
            title="Genuine Rolex watch", summary="brand new", detected_price=15.0
        )
        assert result.data["counterfeit_risk"] is True

    async def test_counterfeit_risk_not_flagged_at_high_price(self) -> None:
        result = await check(
            title="Genuine Rolex watch", summary="brand new", detected_price=10000.0
        )
        # Trademark still blocks, but counterfeit_risk is False.
        assert result.data["counterfeit_risk"] is False
        assert result.data["verdict"] == "block"

    async def test_cannabis_blocked(self) -> None:
        result = await check(title="THC gummies for relaxation")
        assert result.data["verdict"] == "block"

    async def test_origin_claim_flagged(self) -> None:
        result = await check(title="Made in USA premium soap")
        assert result.data["verdict"] == "block"
