"""Unit tests for aegis.geo.tariffs — WTO MFN duty estimation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from aegis.geo.config import Region
from aegis.geo.tariffs import TariffEstimator, hs_code_for_region_pair


class TestTariffEstimator:
    @pytest.fixture()
    def estimator(self) -> TariffEstimator:
        return TariffEstimator(use_comtrade_fallback=False)

    async def test_known_hs_returns_correct_rate(self, estimator: TariffEstimator) -> None:
        result = await estimator.estimate(
            hs_code="610910",  # cotton T-shirts
            origin="US",
            destination="IN",
            value_usd=Decimal("100"),
        )
        assert result.duty_rate == Decimal("0.200")  # India 20% BCD
        assert result.duty_usd == Decimal("20.00")
        assert result.source == "wto_mfn_2024"

    async def test_smartphone_zero_duty_us(self, estimator: TariffEstimator) -> None:
        result = await estimator.estimate(
            hs_code="851712",
            origin="IN",
            destination="US",
            value_usd=Decimal("200"),
        )
        assert result.duty_rate == Decimal("0")
        assert result.duty_usd == Decimal("0.00")

    async def test_books_zero_everywhere(self, estimator: TariffEstimator) -> None:
        for dest in ["US", "IN", "EU", "UK"]:
            result = await estimator.estimate(
                hs_code="490100",
                origin="US",
                destination=dest,
                value_usd=Decimal("50"),
            )
            assert result.duty_rate == Decimal("0"), f"Books should be 0% duty in {dest}"
            assert result.duty_usd == Decimal("0.00")

    async def test_india_has_higher_apparel_duty_than_eu(self, estimator: TariffEstimator) -> None:
        in_result = await estimator.estimate(
            hs_code="610910", origin="US", destination="IN", value_usd=Decimal("100")
        )
        eu_result = await estimator.estimate(
            hs_code="610910", origin="US", destination="EU", value_usd=Decimal("100")
        )
        assert in_result.duty_rate > eu_result.duty_rate

    async def test_unknown_hs_code_uses_wto_avg(self, estimator: TariffEstimator) -> None:
        result = await estimator.estimate(
            hs_code="999999",  # unknown
            origin="US",
            destination="IN",
            value_usd=Decimal("100"),
        )
        assert result.duty_usd > 0
        assert "wto_avg" in result.source

    async def test_duty_scales_with_value(self, estimator: TariffEstimator) -> None:
        r1 = await estimator.estimate(
            hs_code="610910", origin="US", destination="IN", value_usd=Decimal("100")
        )
        r2 = await estimator.estimate(
            hs_code="610910", origin="US", destination="IN", value_usd=Decimal("200")
        )
        assert r2.duty_usd == r1.duty_usd * 2

    def test_hs_code_for_category_returns_valid_code(self, estimator: TariffEstimator) -> None:
        hs = estimator.hs_code_for_category("apparel")
        from aegis.geo.config import HS_TARIFF_SCHEDULE
        assert hs in HS_TARIFF_SCHEDULE

    def test_hs_code_for_unknown_category_uses_general(self, estimator: TariffEstimator) -> None:
        hs = estimator.hs_code_for_category("some_random_category_xyz")
        from aegis.geo.config import CATEGORY_HS_MAP
        assert hs == CATEGORY_HS_MAP["general"]

    def test_static_duty_rate_for(self) -> None:
        rate = TariffEstimator.duty_rate_for("610910", "IN")
        assert rate == Decimal("0.200")

    def test_static_duty_rate_for_unknown_hs_uses_wto_avg(self) -> None:
        # Unknown HS code → falls back to WTO sector average
        rate = TariffEstimator.duty_rate_for("999999", "IN")
        from aegis.geo.tariffs import _WTO_AVG_MFN_BY_SECTOR
        assert rate == _WTO_AVG_MFN_BY_SECTOR.get("in", Decimal("0.08"))

    def test_hs_code_for_region_pair_utility(self) -> None:
        rate = hs_code_for_region_pair("610910", Region.IN)
        assert rate == Decimal("0.200")

    async def test_result_fields_populated(self, estimator: TariffEstimator) -> None:
        result = await estimator.estimate(
            hs_code="640411", origin="CN", destination="US", value_usd=Decimal("50")
        )
        assert result.hs_code == "640411"
        assert result.origin == "CN"
        assert result.destination == "US"
        assert 0 < float(result.duty_rate) <= 1.0
        assert result.duty_usd >= 0


class TestTariffScheduleCompleteness:
    def test_all_major_categories_covered(self) -> None:
        from aegis.geo.config import CATEGORY_HS_MAP, HS_TARIFF_SCHEDULE
        covered = set(CATEGORY_HS_MAP.values())
        for hs in covered:
            assert hs in HS_TARIFF_SCHEDULE

    def test_each_entry_covers_key_regions(self) -> None:
        from aegis.geo.config import HS_TARIFF_SCHEDULE
        required_regions = {"us", "in", "eu", "uk"}
        for hs, entry in HS_TARIFF_SCHEDULE.items():
            covered = set(entry.rates.keys())
            missing = required_regions - covered
            assert not missing, f"HS {hs} missing rates for {missing}"
