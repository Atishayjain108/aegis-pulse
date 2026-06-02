"""Named constants for the Phase 8 compliance engine.

Every magic number lives here with a one-line rationale. Thresholds are
overridable via ``ComplySettings`` (env prefix ``AEGIS_COMPLY_``).
"""

from __future__ import annotations

from typing import Final

# --- Verdict thresholds ------------------------------------------------------
# Aggregate risk (0..1) at or above this -> FLAG (human review). Below -> CLEAR.
# 0.35 chosen so a single WARN-severity hit (0.55 category score) on a weighted
# category trips review without a single INFO hit doing so.
FLAG_RISK_THRESHOLD: Final[float] = 0.35

# Aggregate risk at or above this hard-blocks even without a BLOCK-severity hit.
BLOCK_RISK_THRESHOLD: Final[float] = 0.80

# --- Severity -> category score mapping --------------------------------------
SEVERITY_SCORE_INFO: Final[float] = 0.20
SEVERITY_SCORE_WARN: Final[float] = 0.55
SEVERITY_SCORE_BLOCK: Final[float] = 1.00

# --- Category weights (normalised at runtime) --------------------------------
# IP and counterfeit weighted highest: they carry the largest legal + takedown
# exposure for an arbitrage seller. Platform/DSA lowest: mostly procedural.
DEFAULT_CATEGORY_WEIGHTS: Final[dict[str, float]] = {
    "trademark": 0.20,
    "counterfeit": 0.20,
    "advertising": 0.20,
    "privacy": 0.15,
    "product_safety": 0.15,
    "platform": 0.10,
}

# --- Trademark screening -----------------------------------------------------
# difflib ratio at/above this counts as a name match. 0.82 catches deliberate
# typosquats ("Guuci" ~ "Gucci" = 0.83) without flagging unrelated words.
TRADEMARK_SIMILARITY_THRESHOLD: Final[float] = 0.82
# A near-but-not-exact match (typosquat band) is treated as intentional evasion.
TRADEMARK_TYPOSQUAT_LOW: Final[float] = 0.82
TRADEMARK_TYPOSQUAT_HIGH: Final[float] = 0.995

# --- Counterfeit detection ---------------------------------------------------
# Price this many std-devs below a protected-brand category baseline is a strong
# "too cheap to be genuine" signal.
COUNTERFEIT_PRICE_ZSCORE_FLOOR: Final[float] = -2.0
# Default per-category (mean, std) price baselines in USD. Used only when no
# baseline is supplied. Generic fallback for unknown categories.
DEFAULT_PRICE_BASELINES_USD: Final[dict[str, tuple[float, float]]] = {
    "general": (40.0, 30.0),
    "apparel": (60.0, 45.0),
    "footwear": (110.0, 60.0),
    "watches": (250.0, 200.0),
    "handbags": (300.0, 250.0),
    "electronics": (150.0, 120.0),
    "cosmetics": (35.0, 25.0),
    "supplements": (30.0, 20.0),
    "toys": (25.0, 18.0),
}

# --- Confidence --------------------------------------------------------------
# Deterministic (heuristic-only) confidence floor and ceiling.
CONFIDENCE_BASE: Final[float] = 0.70
CONFIDENCE_LIVE_SOURCE_BONUS: Final[float] = 0.10  # added when a live TM lookup corroborates
CONFIDENCE_MAX: Final[float] = 0.95
CONFIDENCE_MIN: Final[float] = 0.30

# --- HTTP (live trademark lookups; opt-in only) ------------------------------
LIVE_LOOKUP_TIMEOUT_S: Final[float] = 6.0
LIVE_LOOKUP_CIRCUIT_THRESHOLD: Final[int] = 5  # consecutive failures -> open
LIVE_LOOKUP_CIRCUIT_RECOVERY_S: Final[float] = 60.0
LIVE_LOOKUP_RETRY_BASE_S: Final[float] = 0.5
LIVE_LOOKUP_RETRY_MAX_S: Final[float] = 8.0

# --- Caching -----------------------------------------------------------------
TRADEMARK_CACHE_TTL_S: Final[int] = 7 * 24 * 3600  # 7 days

# --- Identifiers -------------------------------------------------------------
DEFAULT_TENANT_ID: Final[str] = "00000000-0000-0000-0000-000000000001"

# Redis stream Phase 4 expects compliance escalations on (duck-typed publisher).
COMPLIANCE_ESCALATION_STREAM: Final[str] = "aegis:phase8:compliance_escalations"
STREAM_MAXLEN: Final[int] = 10_000  # match Phase 2/4 cap to bound Redis memory
