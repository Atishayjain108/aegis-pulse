"""Unit tests for aegis.geo.config — static data integrity."""

from decimal import Decimal

from aegis.geo.config import (
    CATEGORY_HS_MAP,
    CATEGORY_MEDIAN_PRICES_USD,
    HS_TARIFF_SCHEDULE,
    REGION_CONFIGS,
    SHIPPING_MATRIX_USD,
    Region,
)


class TestRegionConfigs:
    def test_all_regions_present(self) -> None:
        for region in Region:
            assert region in REGION_CONFIGS

    def test_market_size_scores_bounded(self) -> None:
        for cfg in REGION_CONFIGS.values():
            assert 0.0 <= cfg.market_size_score <= 1.0

    def test_fee_rates_are_reasonable(self) -> None:
        for cfg in REGION_CONFIGS.values():
            assert Decimal("0") <= cfg.seller_fee_pct <= Decimal("0.30")

    def test_vat_rates_are_reasonable(self) -> None:
        for cfg in REGION_CONFIGS.values():
            assert Decimal("0") <= cfg.vat_gst_pct <= Decimal("0.30")

    def test_ecommerce_markets_positive(self) -> None:
        for cfg in REGION_CONFIGS.values():
            assert cfg.ecommerce_market_bn_usd > 0

    def test_currency_codes_valid(self) -> None:
        known_currencies = {"USD", "INR", "EUR", "GBP", "JPY", "CNY", "AUD", "BRL"}
        for cfg in REGION_CONFIGS.values():
            assert cfg.currency in known_currencies

    def test_us_is_reference_market(self) -> None:
        us = REGION_CONFIGS[Region.US]
        assert us.currency == "USD"
        assert us.market_size_score >= 0.85

    def test_india_has_higher_vat_than_us(self) -> None:
        assert REGION_CONFIGS[Region.IN].vat_gst_pct > REGION_CONFIGS[Region.US].vat_gst_pct


class TestHSTariffSchedule:
    def test_schedule_is_non_empty(self) -> None:
        assert len(HS_TARIFF_SCHEDULE) >= 15

    def test_all_rates_are_fractions(self) -> None:
        for hs, entry in HS_TARIFF_SCHEDULE.items():
            for region, rate in entry.rates.items():
                assert Decimal("0") <= rate <= Decimal("1.0"), (
                    f"HS {hs} region {region}: rate {rate} out of [0,1]"
                )

    def test_apparel_has_higher_us_rate_than_electronics(self) -> None:
        apparel = HS_TARIFF_SCHEDULE["610910"].rates["us"]
        electronics = HS_TARIFF_SCHEDULE["851712"].rates["us"]
        assert apparel > electronics, "Cotton apparel should have higher US duty than phones"

    def test_india_has_higher_duty_than_eu_for_smartphones(self) -> None:
        phone = HS_TARIFF_SCHEDULE["851712"]
        assert phone.rates["in"] > phone.rates["eu"]

    def test_us_zero_rates_electronics(self) -> None:
        # ITA agreement — phones and laptops: 0% in US
        assert HS_TARIFF_SCHEDULE["851712"].rates["us"] == Decimal("0")
        assert HS_TARIFF_SCHEDULE["847130"].rates["us"] == Decimal("0")

    def test_books_are_zero_worldwide(self) -> None:
        books = HS_TARIFF_SCHEDULE["490100"]
        for rate in books.rates.values():
            assert rate == Decimal("0")


class TestCategoryHSMap:
    def test_all_mapped_hs_codes_exist(self) -> None:
        for cat, hs in CATEGORY_HS_MAP.items():
            assert hs in HS_TARIFF_SCHEDULE, f"Category {cat!r} maps to {hs} which is not in schedule"

    def test_general_fallback_present(self) -> None:
        assert "general" in CATEGORY_HS_MAP

    def test_electronics_maps_to_headphones_code(self) -> None:
        assert CATEGORY_HS_MAP["electronics"] == "851830"

    def test_apparel_maps_to_cotton_tshirt(self) -> None:
        assert CATEGORY_HS_MAP["apparel"] == "610910"


class TestShippingMatrix:
    def test_major_routes_covered(self) -> None:
        required = [
            (Region.US, Region.IN),
            (Region.US, Region.EU),
            (Region.IN, Region.US),
            (Region.EU, Region.US),
            (Region.CN, Region.US),
        ]
        for pair in required:
            assert pair in SHIPPING_MATRIX_USD, f"Missing route {pair}"

    def test_all_costs_positive(self) -> None:
        for pair, cost in SHIPPING_MATRIX_USD.items():
            assert cost > 0, f"Zero/negative cost for {pair}"

    def test_china_to_us_cheapest_among_major(self) -> None:
        cn_us = SHIPPING_MATRIX_USD[(Region.CN, Region.US)]
        in_us = SHIPPING_MATRIX_USD[(Region.IN, Region.US)]
        eu_us = SHIPPING_MATRIX_USD[(Region.EU, Region.US)]
        assert cn_us < in_us
        assert cn_us < eu_us

    def test_no_self_routes(self) -> None:
        for origin, dest in SHIPPING_MATRIX_USD:
            assert origin != dest


class TestCategoryMedianPrices:
    def test_all_categories_have_us_price(self) -> None:
        for cat, prices in CATEGORY_MEDIAN_PRICES_USD.items():
            assert "us" in prices, f"Category {cat!r} missing US price"

    def test_prices_positive(self) -> None:
        for cat, prices in CATEGORY_MEDIAN_PRICES_USD.items():
            for region, price in prices.items():
                assert price > 0, f"{cat}/{region}: price {price} ≤ 0"

    def test_smartphones_more_expensive_than_apparel_in_us(self) -> None:
        assert CATEGORY_MEDIAN_PRICES_USD["smartphones"]["us"] > CATEGORY_MEDIAN_PRICES_USD["apparel"]["us"]

    def test_india_cheaper_than_us_for_apparel(self) -> None:
        assert CATEGORY_MEDIAN_PRICES_USD["apparel"]["in"] < CATEGORY_MEDIAN_PRICES_USD["apparel"]["us"]
