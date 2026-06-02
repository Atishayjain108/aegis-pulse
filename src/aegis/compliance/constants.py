"""Phase 8 constants — risk weights, country lists, API endpoints, patterns."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Error codes AEGIS-COMPLY-0001..0099
# ---------------------------------------------------------------------------
ERR_TRADEMARK_BLOCK = "AEGIS-COMPLY-0001"
ERR_PATENT_BLOCK = "AEGIS-COMPLY-0002"
ERR_FDA_BLOCK = "AEGIS-COMPLY-0003"
ERR_COUNTERFEIT_BLOCK = "AEGIS-COMPLY-0004"
ERR_FTC_BLOCK = "AEGIS-COMPLY-0005"
ERR_PRIVACY_BLOCK = "AEGIS-COMPLY-0006"
ERR_AML_BLOCK = "AEGIS-COMPLY-0007"
ERR_SANCTION_BLOCK = "AEGIS-COMPLY-0008"
ERR_ASSESSMENT_FAILED = "AEGIS-COMPLY-0099"

# ---------------------------------------------------------------------------
# Risk weight matrix (must sum to 1.0)
# ---------------------------------------------------------------------------
RISK_WEIGHTS: dict[str, float] = {
    "trademark": 0.22,
    "patent": 0.13,
    "fda": 0.20,
    "counterfeit": 0.15,
    "ftc": 0.10,
    "privacy": 0.10,
    "aml": 0.10,
}

# ---------------------------------------------------------------------------
# Real API endpoints (all free / no-key-required for basic use)
# ---------------------------------------------------------------------------
PATENTSVIEW_API = "https://api.patentsview.org/patents/query"
EUIPO_TMVIEW_API = "https://www.tmdn.org/tmview/api/trademark/search"
FDA_API_BASE = "https://api.fda.gov"
OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
TRADE_GOV_CSL_URL = "https://api.trade.gov/gateway/v1/consolidated_screening_list/search"

# ---------------------------------------------------------------------------
# OFAC — current sanctioned country codes (ISO 3166-1 alpha-2)
# Source: OFAC Country Programs as of 2024
# ---------------------------------------------------------------------------
OFAC_SANCTIONED_COUNTRIES: frozenset[str] = frozenset({
    "CU",   # Cuba
    "IR",   # Iran
    "KP",   # North Korea (DPRK)
    "SY",   # Syria
    "RU",   # Russia (comprehensive)
    "BY",   # Belarus
    "VE",   # Venezuela (sectoral)
    "MM",   # Myanmar/Burma
    "YE",   # Yemen (Houthis)
    "SD",   # Sudan
    "SS",   # South Sudan (arms)
    "CD",   # DR Congo (arms)
    "CF",   # Central African Republic
    "LY",   # Libya
    "SO",   # Somalia (arms)
    "ZW",   # Zimbabwe
    "XK",   # Kosovo (specific programs)
})

# ---------------------------------------------------------------------------
# FATF — high-risk / grey-listed jurisdictions (2024)
# Source: FATF Public Statement, October 2024
# ---------------------------------------------------------------------------
FATF_HIGH_RISK: frozenset[str] = frozenset({
    "AF",   # Afghanistan
    "AL",   # Albania
    "AZ",   # Azerbaijan (monitoring)
    "BB",   # Barbados
    "BF",   # Burkina Faso
    "CM",   # Cameroon
    "HT",   # Haiti
    "JM",   # Jamaica
    "JO",   # Jordan (monitoring)
    "ML",   # Mali
    "MN",   # Mongolia (monitoring)
    "MZ",   # Mozambique
    "MM",   # Myanmar (black list)
    "NG",   # Nigeria
    "PH",   # Philippines
    "SN",   # Senegal
    "SS",   # South Sudan
    "SY",   # Syria (black list)
    "TZ",   # Tanzania
    "TT",   # Trinidad and Tobago
    "UG",   # Uganda
    "VN",   # Vietnam
    "YE",   # Yemen (black list)
})

# ---------------------------------------------------------------------------
# GDPR jurisdictions (EU member states + EEA)
# ---------------------------------------------------------------------------
GDPR_COUNTRIES: frozenset[str] = frozenset({
    "EU", "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES",
    "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV",
    "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
    # EEA
    "IS", "LI", "NO",
    # GDPR-adequate
    "CH", "UK", "GB",
})

# ---------------------------------------------------------------------------
# India DPDP (Digital Personal Data Protection Act 2023)
# ---------------------------------------------------------------------------
DPDP_COUNTRIES: frozenset[str] = frozenset({"IN"})

# ---------------------------------------------------------------------------
# EU DSA (Digital Services Act) / GPSR (General Product Safety Regulation)
# Same as GDPR EU members
# ---------------------------------------------------------------------------
DSA_GPSR_COUNTRIES: frozenset[str] = GDPR_COUNTRIES

# ---------------------------------------------------------------------------
# Data-collecting product categories (trigger privacy risk elevation)
# ---------------------------------------------------------------------------
DATA_COLLECTING_CATEGORIES: frozenset[str] = frozenset({
    "iot", "smart_device", "wearable", "app", "software", "tracker",
    "camera", "health_monitor", "fitness", "children", "kids", "toy",
})

# ---------------------------------------------------------------------------
# Known registered trademark brand names (luxury + high-profile)
# Used for fast text-based counterfeit detection BEFORE live API call.
# Source: WIPO / USPTO / EUIPO registered marks 2024.
# ---------------------------------------------------------------------------
KNOWN_TRADEMARK_BRANDS: list[tuple[str, str, str]] = [
    # (brand_name, owner, class)
    ("louis vuitton", "LVMH Moët Hennessy Louis Vuitton", "leather goods"),
    ("lv", "LVMH Moët Hennessy Louis Vuitton", "leather goods"),
    ("gucci", "Guccio Gucci S.p.A.", "luxury fashion"),
    ("chanel", "Chanel S.A.", "luxury fashion"),
    ("hermès", "Hermès International", "luxury fashion"),
    ("hermes", "Hermès International", "luxury fashion"),
    ("prada", "Prada S.p.A.", "luxury fashion"),
    ("rolex", "Rolex SA", "watches"),
    ("cartier", "Cartier International AG", "jewellery"),
    ("tiffany", "Tiffany & Co.", "jewellery"),
    ("tiffany & co", "Tiffany & Co.", "jewellery"),
    ("burberry", "Burberry Limited", "fashion"),
    ("versace", "Gianni Versace S.r.l.", "fashion"),
    ("armani", "Giorgio Armani S.p.A.", "fashion"),
    ("fendi", "Fendi S.r.l.", "fashion"),
    ("balenciaga", "Balenciaga", "fashion"),
    ("off-white", "Off-White LLC", "streetwear"),
    ("supreme", "Supreme New York", "streetwear"),
    ("nike", "Nike Inc.", "sportswear"),
    ("adidas", "adidas AG", "sportswear"),
    ("yeezy", "adidas AG / Ye", "footwear"),
    ("jordan", "Nike Inc. / Air Jordan", "footwear"),
    ("apple", "Apple Inc.", "electronics"),
    ("iphone", "Apple Inc.", "electronics"),
    ("airpods", "Apple Inc.", "electronics"),
    ("samsung", "Samsung Electronics Co., Ltd.", "electronics"),
    ("dyson", "Dyson Limited", "appliances"),
    ("bose", "Bose Corporation", "audio"),
    ("sony", "Sony Corporation", "electronics"),
    ("microsoft", "Microsoft Corporation", "software/hardware"),
    ("xbox", "Microsoft Corporation", "gaming"),
    ("playstation", "Sony Interactive Entertainment", "gaming"),
    ("lego", "LEGO Group", "toys"),
    ("pokemon", "Nintendo / Game Freak / Creatures", "entertainment"),
    ("disney", "The Walt Disney Company", "entertainment"),
    ("ugg", "Deckers Outdoor Corporation", "footwear"),
    ("timberland", "VF Corporation", "footwear"),
    ("north face", "The North Face / VF Corporation", "outdoor"),
    ("patagonia", "Patagonia Inc.", "outdoor"),
    ("ray-ban", "Luxottica Group / EssilorLuxottica", "eyewear"),
    ("oakley", "Luxottica Group / EssilorLuxottica", "eyewear"),
]

# ---------------------------------------------------------------------------
# Counterfeit-prone categories (elevate base risk)
# ---------------------------------------------------------------------------
COUNTERFEIT_PRONE_CATEGORIES: frozenset[str] = frozenset({
    "luxury", "designer", "apparel", "footwear", "watches", "jewellery",
    "jewelry", "electronics", "pharmaceuticals", "cosmetics", "perfume",
    "fragrance", "handbags", "bags", "sunglasses", "eyewear",
})

# ---------------------------------------------------------------------------
# FDA — product categories that are routinely screened
# ---------------------------------------------------------------------------
FDA_HIGH_RISK_CATEGORIES: frozenset[str] = frozenset({
    "pharmaceuticals", "drugs", "medication", "supplement", "nutraceutical",
    "cosmetics", "beauty", "food", "beverage", "medical_device", "device",
    "dietary_supplement", "herbal", "essential_oil",
})

# FDA keywords that strongly indicate banned / restricted products
FDA_BANNED_KEYWORDS: list[str] = [
    "fake medication", "counterfeit drug", "unauthorized pharmaceutical",
    "unregistered drug", "illegal supplement", "unapproved drug",
    "unapproved device", "mercury cream", "hydroquinone bleach",
    "chloramphenicol", "clenbuterol", "sildenafil supplement",
    "undeclared drug", "undeclared pharmaceutical",
]

# ---------------------------------------------------------------------------
# FTC advertising violation patterns (compiled regexes)
# ---------------------------------------------------------------------------
FTC_VIOLATION_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    # (pattern, violation_type, severity 0–1)
    (
        re.compile(r"(?i)\b(cures?|treats?|prevents?)\s+(cancer|diabetes|covid|aids|hiv|arthritis|alzheimer)", re.I),
        "unsubstantiated_health_claim",
        0.90,
    ),
    (
        re.compile(r"(?i)fda[\s\-]?approved\s+(supplement|cream|lotion|pill|capsule|powder)", re.I),
        "false_fda_claim",
        0.85,
    ),
    (
        re.compile(r"(?i)doctor[\s\-]?(recommended|endorsed|approved|prescribed)", re.I),
        "unsubstantiated_endorsement",
        0.70,
    ),
    (
        re.compile(r"(?i)clinically[\s\-]?(proven|tested|validated|studied)", re.I),
        "unsubstantiated_clinical_claim",
        0.65,
    ),
    (
        re.compile(r"(?i)guaranteed\s+to\s+(lose|cure|heal|fix|reverse|eliminate)", re.I),
        "deceptive_guarantee",
        0.80,
    ),
    (
        re.compile(r"(?i)(100%|completely)\s+(safe|natural|organic)\s+(and\s+)?(effective|proven|guaranteed)", re.I),
        "absolute_safety_claim",
        0.60,
    ),
    (
        re.compile(r"(?i)guaranteed\s+(annual\s+)?(returns?|profits?|income|earnings?)", re.I),
        "guaranteed_investment_return",
        0.95,
    ),
    (
        re.compile(r"(?i)risk[\s\-]?free\s+investment", re.I),
        "risk_free_investment_claim",
        0.90,
    ),
    (
        re.compile(r"(?i)earn\s+\$[\d,]+\+?\s+per\s+(day|week|hour|month)", re.I),
        "unrealistic_income_claim",
        0.85,
    ),
    (
        re.compile(r"(?i)lose\s+\d+\s+(pounds?|lbs?|kg)\s+(in|within)\s+\d+\s+(days?|weeks?)", re.I),
        "deceptive_weight_loss_claim",
        0.80,
    ),
    (
        re.compile(r"(?i)as\s+(seen|featured)\s+on\s+(tv|cnn|bbc|fox|nbc|abc)", re.I),
        "false_media_endorsement",
        0.65,
    ),
    (
        re.compile(r"(?i)made\s+in\s+usa\b", re.I),
        "made_in_usa_claim",  # requires FTC compliance — not auto-block, flag for review
        0.30,
    ),
]
