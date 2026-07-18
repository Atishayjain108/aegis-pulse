"""
Geospatial Intelligence configuration.

All numeric constants are sourced from:
  - World Bank Open Data 2024 (GDP, population, e-commerce penetration)
  - WTO MFN Applied Tariff Rates 2024 (USITC, CBIC, EC Combined Nomenclature)
  - EMS/India Post & DHL published rate cards 2024 (0.5 kg light parcel)
  - UN Comtrade HS Code schedule (Harmonized System 2022)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Region(str, Enum):
    US = "US"
    IN = "IN"
    EU = "EU"
    UK = "UK"
    JP = "JP"
    CN = "CN"
    AU = "AU"
    BR = "BR"


@dataclass(frozen=True)
class RegionConfig:
    code: Region
    name: str
    currency: str
    primary_platform: str
    seller_fee_pct: Decimal
    vat_gst_pct: Decimal
    # World Bank 2024: e-commerce market USD billions
    ecommerce_market_bn_usd: float
    # Composite score 0–1 (geometric mean of market share × purchasing-power weight)
    market_size_score: float
    # Platform names whose signals map to this region
    signal_platforms: tuple[str, ...]


REGION_CONFIGS: dict[Region, RegionConfig] = {
    Region.US: RegionConfig(
        code=Region.US,
        name="United States",
        currency="USD",
        primary_platform="amazon.com",
        seller_fee_pct=Decimal("0.15"),
        vat_gst_pct=Decimal("0.00"),   # no federal VAT; state tax varies
        ecommerce_market_bn_usd=1100.0,
        market_size_score=0.92,
        signal_platforms=("amazon", "amazon_com", "ebay"),
    ),
    Region.IN: RegionConfig(
        code=Region.IN,
        name="India",
        currency="INR",
        primary_platform="amazon.in",
        seller_fee_pct=Decimal("0.18"),
        vat_gst_pct=Decimal("0.18"),   # GST 18% standard rate
        ecommerce_market_bn_usd=70.0,
        market_size_score=0.58,
        signal_platforms=(
            "amazon_in", "flipkart", "meesho", "myntra",
            "nykaa", "ajio", "snapdeal", "indiamart",
            "nse_bse", "screener_in",
        ),
    ),
    Region.EU: RegionConfig(
        code=Region.EU,
        name="European Union",
        currency="EUR",
        primary_platform="amazon.de",
        seller_fee_pct=Decimal("0.15"),
        vat_gst_pct=Decimal("0.20"),   # EU standard VAT ~20% avg
        ecommerce_market_bn_usd=550.0,
        market_size_score=0.82,
        signal_platforms=("amazon_de", "amazon_fr", "zalando"),
    ),
    Region.UK: RegionConfig(
        code=Region.UK,
        name="United Kingdom",
        currency="GBP",
        primary_platform="amazon.co.uk",
        seller_fee_pct=Decimal("0.15"),
        vat_gst_pct=Decimal("0.20"),   # UK VAT 20%
        ecommerce_market_bn_usd=130.0,
        market_size_score=0.72,
        signal_platforms=("amazon_uk", "ebay_uk"),
    ),
    Region.JP: RegionConfig(
        code=Region.JP,
        name="Japan",
        currency="JPY",
        primary_platform="amazon.co.jp",
        seller_fee_pct=Decimal("0.15"),
        vat_gst_pct=Decimal("0.10"),   # JCT 10%
        ecommerce_market_bn_usd=141.0,
        market_size_score=0.65,
        signal_platforms=("amazon_jp", "rakuten"),
    ),
    Region.CN: RegionConfig(
        code=Region.CN,
        name="China",
        currency="CNY",
        primary_platform="taobao.com",
        seller_fee_pct=Decimal("0.05"),
        vat_gst_pct=Decimal("0.13"),   # VAT 13% standard
        ecommerce_market_bn_usd=2800.0,
        market_size_score=0.95,        # huge but access-restricted from outside
        signal_platforms=("taobao", "jd", "tmall"),
    ),
    Region.AU: RegionConfig(
        code=Region.AU,
        name="Australia",
        currency="AUD",
        primary_platform="amazon.com.au",
        seller_fee_pct=Decimal("0.15"),
        vat_gst_pct=Decimal("0.10"),   # GST 10%
        ecommerce_market_bn_usd=34.0,
        market_size_score=0.52,
        signal_platforms=("amazon_au",),
    ),
    Region.BR: RegionConfig(
        code=Region.BR,
        name="Brazil",
        currency="BRL",
        primary_platform="amazon.com.br",
        seller_fee_pct=Decimal("0.15"),
        vat_gst_pct=Decimal("0.17"),   # ICMS avg ~17%
        ecommerce_market_bn_usd=32.0,
        market_size_score=0.40,
        signal_platforms=("amazon_br", "mercadolivre"),
    ),
}

# ---------------------------------------------------------------------------
# WTO MFN Applied Tariff Rates 2024
# Sources:
#   US: USITC Harmonized Tariff Schedule 2024
#   IN: CBIC Customs Tariff (BCD + IGST effective rate)
#   EU: EC TARIC 2024 / Combined Nomenclature
#   UK: HMRC UK Global Trade Tariff 2024
#   JP: Japan Customs Tariff Schedule 2024
#   AU: Australian Border Force Schedule 2024
#   BR: CAMEX Resolution (TEC) 2024
#   CN: China Customs Tariff 2024
#
# Structure: hs_code → region_code (lowercase) → duty_rate (Decimal fraction)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HSCodeEntry:
    code: str
    description: str
    # duty rates by region (fraction, not percent)
    rates: dict[str, Decimal] = field(default_factory=dict)


# HS 6-digit code → entry
HS_TARIFF_SCHEDULE: dict[str, HSCodeEntry] = {
    # --- Apparel & Clothing ---
    "610910": HSCodeEntry(  # Cotton T-shirts / singlets
        code="610910",
        description="Cotton T-shirts, singlets and other vests",
        rates={
            "us": Decimal("0.165"),   # 16.5%
            "in": Decimal("0.200"),   # 20% BCD
            "eu": Decimal("0.120"),   # 12%
            "uk": Decimal("0.120"),   # 12%
            "jp": Decimal("0.108"),   # 10.8%
            "au": Decimal("0.050"),   # 5%
            "br": Decimal("0.350"),   # 35%
            "cn": Decimal("0.145"),   # 14.5%
        },
    ),
    "610990": HSCodeEntry(  # Other knit T-shirts (polyester etc.)
        code="610990",
        description="T-shirts of other textile materials",
        rates={
            "us": Decimal("0.320"),
            "in": Decimal("0.200"),
            "eu": Decimal("0.120"),
            "uk": Decimal("0.120"),
            "jp": Decimal("0.108"),
            "au": Decimal("0.050"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.145"),
        },
    ),
    "620462": HSCodeEntry(  # Women's woven cotton trousers/jeans
        code="620462",
        description="Women's trousers and breeches of cotton",
        rates={
            "us": Decimal("0.169"),   # 16.9%
            "in": Decimal("0.200"),
            "eu": Decimal("0.120"),
            "uk": Decimal("0.120"),
            "jp": Decimal("0.108"),
            "au": Decimal("0.050"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.145"),
        },
    ),
    "620342": HSCodeEntry(  # Men's cotton trousers/jeans
        code="620342",
        description="Men's trousers and breeches of cotton",
        rates={
            "us": Decimal("0.168"),   # 16.8%
            "in": Decimal("0.200"),
            "eu": Decimal("0.120"),
            "uk": Decimal("0.120"),
            "jp": Decimal("0.108"),
            "au": Decimal("0.050"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.145"),
        },
    ),
    "611020": HSCodeEntry(  # Cotton jerseys / pullovers / hoodies
        code="611020",
        description="Jerseys, pullovers, sweatshirts of cotton",
        rates={
            "us": Decimal("0.180"),   # 18%
            "in": Decimal("0.200"),
            "eu": Decimal("0.120"),
            "uk": Decimal("0.120"),
            "jp": Decimal("0.108"),
            "au": Decimal("0.050"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.145"),
        },
    ),
    # --- Footwear ---
    "640411": HSCodeEntry(  # Sports footwear (textile upper)
        code="640411",
        description="Sports footwear with textile uppers",
        rates={
            "us": Decimal("0.480"),   # 48% — highest US tariff category
            "in": Decimal("0.250"),   # 25%
            "eu": Decimal("0.170"),   # 17%
            "uk": Decimal("0.170"),   # 17%
            "jp": Decimal("0.277"),   # 27.7%
            "au": Decimal("0.050"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.245"),
        },
    ),
    "640391": HSCodeEntry(  # Leather shoes covering ankle
        code="640391",
        description="Footwear with leather uppers, covering ankle",
        rates={
            "us": Decimal("0.100"),   # 10%
            "in": Decimal("0.350"),   # 35%
            "eu": Decimal("0.080"),   # 8%
            "uk": Decimal("0.080"),   # 8%
            "jp": Decimal("0.300"),   # 30%
            "au": Decimal("0.050"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.245"),
        },
    ),
    # --- Electronics ---
    "851712": HSCodeEntry(  # Smartphones / mobile phones
        code="851712",
        description="Telephones for cellular networks (smartphones)",
        rates={
            "us": Decimal("0.000"),   # ITA zero-rated
            "in": Decimal("0.200"),   # 20% BCD (incl. IGST components)
            "eu": Decimal("0.000"),   # ITA zero-rated
            "uk": Decimal("0.000"),   # ITA zero-rated
            "jp": Decimal("0.000"),   # ITA zero-rated
            "au": Decimal("0.000"),
            "br": Decimal("0.160"),   # 16%
            "cn": Decimal("0.000"),
        },
    ),
    "847130": HSCodeEntry(  # Laptops / portable computers
        code="847130",
        description="Portable automatic data processing machines",
        rates={
            "us": Decimal("0.000"),   # ITA
            "in": Decimal("0.000"),   # ITA; but IGST applies
            "eu": Decimal("0.000"),   # ITA
            "uk": Decimal("0.000"),   # ITA
            "jp": Decimal("0.000"),   # ITA
            "au": Decimal("0.000"),
            "br": Decimal("0.160"),
            "cn": Decimal("0.000"),
        },
    ),
    "851830": HSCodeEntry(  # Headphones & earphones
        code="851830",
        description="Headphones and earphones",
        rates={
            "us": Decimal("0.035"),   # 3.5%
            "in": Decimal("0.150"),   # 15%
            "eu": Decimal("0.035"),   # 3.5%
            "uk": Decimal("0.000"),   # 0%
            "jp": Decimal("0.000"),
            "au": Decimal("0.000"),
            "br": Decimal("0.160"),
            "cn": Decimal("0.060"),
        },
    ),
    "851650": HSCodeEntry(  # Microwave ovens (kitchen appliances)
        code="851650",
        description="Microwave ovens",
        rates={
            "us": Decimal("0.020"),   # 2%
            "in": Decimal("0.100"),   # 10%
            "eu": Decimal("0.027"),   # 2.7%
            "uk": Decimal("0.000"),
            "jp": Decimal("0.000"),
            "au": Decimal("0.000"),
            "br": Decimal("0.200"),
            "cn": Decimal("0.080"),
        },
    ),
    # --- Beauty & Personal Care ---
    "330499": HSCodeEntry(  # Beauty / make-up preparations
        code="330499",
        description="Beauty and make-up preparations",
        rates={
            "us": Decimal("0.055"),   # 5.5%
            "in": Decimal("0.200"),   # 20%
            "eu": Decimal("0.065"),   # 6.5%
            "uk": Decimal("0.000"),   # 0%
            "jp": Decimal("0.056"),
            "au": Decimal("0.000"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.065"),
        },
    ),
    "330510": HSCodeEntry(  # Shampoos
        code="330510",
        description="Shampoos",
        rates={
            "us": Decimal("0.025"),
            "in": Decimal("0.200"),
            "eu": Decimal("0.065"),
            "uk": Decimal("0.000"),
            "jp": Decimal("0.028"),
            "au": Decimal("0.000"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.065"),
        },
    ),
    # --- Toys & Games ---
    "950300": HSCodeEntry(  # Toys, games and sports requisites
        code="950300",
        description="Tricycles, scooters, pedal cars and similar wheeled toys",
        rates={
            "us": Decimal("0.000"),   # 0%
            "in": Decimal("0.120"),   # 12%
            "eu": Decimal("0.047"),   # 4.7%
            "uk": Decimal("0.047"),   # 4.7%
            "jp": Decimal("0.000"),
            "au": Decimal("0.000"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.100"),
        },
    ),
    "950490": HSCodeEntry(  # Video game hardware/accessories
        code="950490",
        description="Articles for funfair, table or parlour games",
        rates={
            "us": Decimal("0.000"),
            "in": Decimal("0.120"),
            "eu": Decimal("0.047"),
            "uk": Decimal("0.000"),
            "jp": Decimal("0.000"),
            "au": Decimal("0.000"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.100"),
        },
    ),
    # --- Home & Furniture ---
    "940360": HSCodeEntry(  # Wooden furniture for domestic use
        code="940360",
        description="Wooden furniture for domestic purposes",
        rates={
            "us": Decimal("0.000"),   # 0% (but anti-dumping duties may apply)
            "in": Decimal("0.250"),   # 25%
            "eu": Decimal("0.027"),   # 2.7%
            "uk": Decimal("0.027"),   # 2.7%
            "jp": Decimal("0.000"),
            "au": Decimal("0.050"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.000"),
        },
    ),
    # --- Health & Nutrition ---
    "210690": HSCodeEntry(  # Food preparations (vitamins/supplements)
        code="210690",
        description="Food preparations not elsewhere specified",
        rates={
            "us": Decimal("0.033"),   # 3.3%
            "in": Decimal("0.300"),   # 30%
            "eu": Decimal("0.064"),   # 6.4%
            "uk": Decimal("0.000"),   # 0%
            "jp": Decimal("0.054"),
            "au": Decimal("0.000"),
            "br": Decimal("0.200"),
            "cn": Decimal("0.100"),
        },
    ),
    # --- Sports & Outdoors ---
    "950699": HSCodeEntry(  # Sports equipment (general)
        code="950699",
        description="Articles and equipment for general physical exercise",
        rates={
            "us": Decimal("0.044"),   # 4.4%
            "in": Decimal("0.100"),   # 10%
            "eu": Decimal("0.027"),   # 2.7%
            "uk": Decimal("0.027"),   # 2.7%
            "jp": Decimal("0.000"),
            "au": Decimal("0.000"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.100"),
        },
    ),
    # --- Jewelry & Accessories ---
    "711319": HSCodeEntry(  # Jewellery of precious metal
        code="711319",
        description="Jewellery and parts thereof of other precious metal",
        rates={
            "us": Decimal("0.065"),   # 6.5%
            "in": Decimal("0.250"),   # 25%
            "eu": Decimal("0.025"),   # 2.5%
            "uk": Decimal("0.025"),   # 2.5%
            "jp": Decimal("0.056"),
            "au": Decimal("0.050"),
            "br": Decimal("0.180"),
            "cn": Decimal("0.130"),
        },
    ),
    # --- Books & Media ---
    "490100": HSCodeEntry(  # Printed books, brochures
        code="490100",
        description="Printed books, brochures, leaflets and similar",
        rates={
            "us": Decimal("0.000"),
            "in": Decimal("0.000"),
            "eu": Decimal("0.000"),
            "uk": Decimal("0.000"),
            "jp": Decimal("0.000"),
            "au": Decimal("0.000"),
            "br": Decimal("0.000"),
            "cn": Decimal("0.000"),
        },
    ),
    # --- Pet Products ---
    "630790": HSCodeEntry(  # Other made-up textile articles (pet products)
        code="630790",
        description="Other made-up textile articles",
        rates={
            "us": Decimal("0.070"),   # 7%
            "in": Decimal("0.200"),   # 20%
            "eu": Decimal("0.063"),   # 6.3%
            "uk": Decimal("0.040"),   # 4%
            "jp": Decimal("0.056"),
            "au": Decimal("0.050"),
            "br": Decimal("0.350"),
            "cn": Decimal("0.100"),
        },
    ),
}

# Category-to-HS-code mapping for automatic classification
CATEGORY_HS_MAP: dict[str, str] = {
    "apparel": "610910",
    "t-shirts": "610910",
    "shirts": "620462",
    "jeans": "620342",
    "hoodies": "611020",
    "sweatshirts": "611020",
    "sneakers": "640411",
    "shoes": "640391",
    "footwear": "640411",
    "smartphones": "851712",
    "phones": "851712",
    "laptops": "847130",
    "computers": "847130",
    "headphones": "851830",
    "earphones": "851830",
    "electronics": "851830",
    "appliances": "851650",
    "beauty": "330499",
    "cosmetics": "330499",
    "makeup": "330499",
    "skincare": "330499",
    "shampoo": "330510",
    "haircare": "330510",
    "toys": "950300",
    "games": "950490",
    "gaming": "950490",
    "furniture": "940360",
    "home": "940360",
    "supplements": "210690",
    "vitamins": "210690",
    "health": "210690",
    "sports": "950699",
    "fitness": "950699",
    "jewelry": "711319",
    "accessories": "711319",
    "books": "490100",
    "media": "490100",
    "pet": "630790",
    "pet supplies": "630790",
    "general": "610910",  # apparel as default
}

# ---------------------------------------------------------------------------
# Shipping Cost Matrix — economy tracked parcel (0.5 kg / ~20cm³)
# Sources:
#   EMS International (India Post, USPS EMS, DHL Parcel Connect)
#   DHL Express published zone rates 2024
# We use economy tier (EMS/postal) as baseline — realistic for POD/dropship.
# ---------------------------------------------------------------------------

# (origin Region, destination Region) → shipping cost in USD
SHIPPING_MATRIX_USD: dict[tuple[Region, Region], Decimal] = {
    (Region.US, Region.IN): Decimal("8.50"),
    (Region.US, Region.EU): Decimal("12.00"),
    (Region.US, Region.UK): Decimal("10.00"),
    (Region.US, Region.JP): Decimal("14.00"),
    (Region.US, Region.CN): Decimal("9.00"),
    (Region.US, Region.AU): Decimal("13.00"),
    (Region.US, Region.BR): Decimal("15.00"),
    (Region.IN, Region.US): Decimal("6.50"),
    (Region.IN, Region.EU): Decimal("8.00"),
    (Region.IN, Region.UK): Decimal("7.50"),
    (Region.IN, Region.JP): Decimal("7.00"),
    (Region.IN, Region.CN): Decimal("5.50"),
    (Region.IN, Region.AU): Decimal("8.50"),
    (Region.IN, Region.BR): Decimal("14.00"),
    (Region.EU, Region.US): Decimal("14.00"),
    (Region.EU, Region.IN): Decimal("10.00"),
    (Region.EU, Region.UK): Decimal("5.00"),
    (Region.EU, Region.JP): Decimal("12.00"),
    (Region.EU, Region.CN): Decimal("9.00"),
    (Region.EU, Region.AU): Decimal("14.00"),
    (Region.EU, Region.BR): Decimal("15.00"),
    (Region.UK, Region.US): Decimal("12.00"),
    (Region.UK, Region.IN): Decimal("9.00"),
    (Region.UK, Region.EU): Decimal("5.50"),
    (Region.UK, Region.JP): Decimal("13.00"),
    (Region.UK, Region.CN): Decimal("10.00"),
    (Region.UK, Region.AU): Decimal("13.50"),
    (Region.UK, Region.BR): Decimal("16.00"),
    (Region.JP, Region.US): Decimal("11.00"),
    (Region.JP, Region.IN): Decimal("8.00"),
    (Region.JP, Region.EU): Decimal("12.00"),
    (Region.JP, Region.UK): Decimal("11.50"),
    (Region.JP, Region.CN): Decimal("5.00"),
    (Region.JP, Region.AU): Decimal("10.00"),
    (Region.JP, Region.BR): Decimal("18.00"),
    (Region.CN, Region.US): Decimal("3.50"),   # ePacket / China Post
    (Region.CN, Region.IN): Decimal("4.50"),
    (Region.CN, Region.EU): Decimal("5.00"),
    (Region.CN, Region.UK): Decimal("4.50"),
    (Region.CN, Region.JP): Decimal("4.00"),
    (Region.CN, Region.AU): Decimal("5.50"),
    (Region.CN, Region.BR): Decimal("8.00"),
    (Region.AU, Region.US): Decimal("14.00"),
    (Region.AU, Region.IN): Decimal("10.00"),
    (Region.AU, Region.EU): Decimal("14.50"),
    (Region.AU, Region.UK): Decimal("13.50"),
    (Region.AU, Region.JP): Decimal("10.00"),
    (Region.AU, Region.CN): Decimal("9.00"),
    (Region.AU, Region.BR): Decimal("20.00"),
    (Region.BR, Region.US): Decimal("16.00"),
    (Region.BR, Region.IN): Decimal("18.00"),
    (Region.BR, Region.EU): Decimal("18.00"),
    (Region.BR, Region.UK): Decimal("17.00"),
    (Region.BR, Region.JP): Decimal("20.00"),
    (Region.BR, Region.CN): Decimal("18.00"),
    (Region.BR, Region.AU): Decimal("22.00"),
}

# Default fallback if pair not in matrix
SHIPPING_DEFAULT_USD = Decimal("12.00")

# ---------------------------------------------------------------------------
# Real median price benchmarks (USD) by category/region
# Source: Amazon/marketplace category median 2024 (public price research)
# Used only when no scraped price data is available for a SKU.
# ---------------------------------------------------------------------------
CATEGORY_MEDIAN_PRICES_USD: dict[str, dict[str, float]] = {
    "apparel": {"us": 24.99, "in": 9.50, "eu": 29.99, "uk": 22.00, "jp": 22.00, "cn": 8.00, "au": 28.00, "br": 20.00},
    "sneakers": {"us": 59.99, "in": 22.00, "eu": 69.99, "uk": 55.00, "jp": 65.00, "cn": 18.00, "au": 65.00, "br": 45.00},
    "shoes": {"us": 49.99, "in": 18.00, "eu": 59.99, "uk": 48.00, "jp": 55.00, "cn": 15.00, "au": 55.00, "br": 38.00},
    "smartphones": {"us": 449.00, "in": 180.00, "eu": 499.00, "uk": 420.00, "jp": 420.00, "cn": 160.00, "au": 480.00, "br": 350.00},
    "laptops": {"us": 699.00, "in": 320.00, "eu": 799.00, "uk": 650.00, "jp": 680.00, "cn": 300.00, "au": 750.00, "br": 550.00},
    "headphones": {"us": 49.99, "in": 18.00, "eu": 54.99, "uk": 44.00, "jp": 46.00, "cn": 12.00, "au": 52.00, "br": 38.00},
    "beauty": {"us": 18.99, "in": 6.50, "eu": 22.00, "uk": 17.00, "jp": 20.00, "cn": 5.00, "au": 21.00, "br": 14.00},
    "toys": {"us": 24.99, "in": 8.50, "eu": 27.99, "uk": 22.00, "jp": 24.00, "cn": 7.00, "au": 26.00, "br": 18.00},
    "supplements": {"us": 29.99, "in": 10.00, "eu": 34.99, "uk": 26.00, "jp": 30.00, "cn": 8.00, "au": 32.00, "br": 22.00},
    "sports": {"us": 34.99, "in": 12.00, "eu": 39.99, "uk": 30.00, "jp": 34.00, "cn": 10.00, "au": 37.00, "br": 25.00},
    "jewelry": {"us": 39.99, "in": 14.00, "eu": 44.99, "uk": 35.00, "jp": 38.00, "cn": 11.00, "au": 42.00, "br": 28.00},
    "books": {"us": 14.99, "in": 4.00, "eu": 16.99, "uk": 12.00, "jp": 13.00, "cn": 3.50, "au": 16.00, "br": 10.00},
    "general": {"us": 24.99, "in": 9.00, "eu": 29.99, "uk": 22.00, "jp": 24.00, "cn": 8.00, "au": 27.00, "br": 18.00},
}
