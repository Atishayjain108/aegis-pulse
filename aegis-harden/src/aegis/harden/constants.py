"""
Phase 5 constants. Every magic number lives here with rationale.

These values are calibrated for Phase 0-4 production traffic patterns.
Changing them requires updating the corresponding unit tests and
re-running the integration audit.
"""

from __future__ import annotations

from typing import Final

PHASE5_VERSION: Final[str] = "0.5.0"

# ---------------------------------------------------------------------------
# Profile defaults
# ---------------------------------------------------------------------------
# A "profile" is the named bundle of hardening knobs applied to a request.
# "standard" is calibrated for low-friction targets (RSS, public APIs).
# "stealth" is for Cloudflare/Akamai-protected targets.
# "tor" routes through tor exit pool; highest latency, last-resort.
DEFAULT_HARDEN_PROFILE: Final[str] = "standard"
KNOWN_PROFILES: Final[tuple[str, ...]] = ("minimal", "standard", "stealth", "tor")

# ---------------------------------------------------------------------------
# Fingerprint diversity
# ---------------------------------------------------------------------------
# Number of JA3 profiles bundled. Drawn from real-world Chrome/Firefox/Safari
# fingerprints rotated quarterly. <50 lowers entropy; bot-defense systems
# cluster small pools.
JA3_POOL_TARGET_SIZE: Final[int] = 50

# JA4 is the newer Foxio standard. We carry both — JA3 for libcurl-based
# clients, JA4 where the TLS stack supports the richer ordering.
JA4_POOL_TARGET_SIZE: Final[int] = 25

# HTTP/2 SETTINGS frame value bounds (RFC 7540 § 6.5.2)
H2_INITIAL_WINDOW_SIZE_MIN: Final[int] = 65_535
H2_INITIAL_WINDOW_SIZE_MAX: Final[int] = 16_777_216
H2_MAX_FRAME_SIZE_MIN: Final[int] = 16_384
H2_MAX_FRAME_SIZE_MAX: Final[int] = 16_777_215
H2_MAX_HEADER_LIST_SIZE_DEFAULT: Final[int] = 262_144

# ---------------------------------------------------------------------------
# Playbook governance
# ---------------------------------------------------------------------------
# Hard ceilings; any playbook attempting to exceed these is rejected by the
# loader. This prevents an attacker who gains write access to a YAML file
# from disabling rate limits or pinning a single weak fingerprint.
PLAYBOOK_MAX_RATE_PER_MIN: Final[int] = 600  # 10 req/sec ceiling
PLAYBOOK_MIN_DELAY_MS: Final[int] = 50  # under this is human-impossible
PLAYBOOK_MAX_DELAY_MS: Final[int] = 60_000  # 1 minute upper bound
PLAYBOOK_MAX_RETRIES: Final[int] = 8
PLAYBOOK_REQUIRED_KEYS: Final[tuple[str, ...]] = (
    "name",
    "version",
    "match",
    "delay_ms",
    "retries",
    "profile",
)

# ---------------------------------------------------------------------------
# Honeypot detection
# ---------------------------------------------------------------------------
# A score in [0.0, 1.0]. Anything ≥ HONEYPOT_BLOCK is dropped pre-click.
HONEYPOT_BLOCK_THRESHOLD: Final[float] = 0.7
HONEYPOT_WARN_THRESHOLD: Final[float] = 0.4

# Known honeypot class/id/attribute substrings. Lowercased substring match.
HONEYPOT_CLASS_TOKENS: Final[tuple[str, ...]] = (
    "donotclick",
    "do-not-click",
    "honeypot",
    "honey-pot",
    "trap-link",
    "antibot",
    "anti-bot",
    "noindex-link",
    "hidden-trap",
)
HONEYPOT_ATTR_TOKENS: Final[tuple[str, ...]] = (
    "data-honeypot",
    "data-trap",
    "data-bot-trap",
)

# Suspicious CSS substrings that hide links from humans but not from scrapers.
HONEYPOT_STYLE_PATTERNS: Final[tuple[str, ...]] = (
    "display:none",
    "display: none",
    "visibility:hidden",
    "visibility: hidden",
    "opacity:0",
    "opacity: 0",
    "left:-9999",
    "left: -9999",
    "left:-10000",
    "top:-9999",
    "height:0",
    "width:0",
    "font-size:0",
)

# ---------------------------------------------------------------------------
# Randomized smoothing
# ---------------------------------------------------------------------------
# Number of Monte Carlo samples used to estimate the smoothed prediction.
# 100 is the floor for a stable estimate at α = 0.05; 1000+ for publication.
SMOOTHING_DEFAULT_SAMPLES: Final[int] = 128
SMOOTHING_MIN_SAMPLES: Final[int] = 16
SMOOTHING_MAX_SAMPLES: Final[int] = 4096

# Gaussian σ on the FEATURE_DIM=20 normalized feature vector.
# Calibrated so a typical Phase 3 feature stays within ±2σ of itself.
SMOOTHING_DEFAULT_SIGMA: Final[float] = 0.10
SMOOTHING_MIN_SIGMA: Final[float] = 0.0
SMOOTHING_MAX_SIGMA: Final[float] = 1.0

# Cohen et al. (2019) certified radius computed at this confidence level.
SMOOTHING_DEFAULT_ALPHA: Final[float] = 0.05

# ---------------------------------------------------------------------------
# Poisoning detection
# ---------------------------------------------------------------------------
# Maximum label-flip rate considered acceptable in a training batch.
# Above this the batch is rejected and operators are paged.
POISONING_LABEL_FLIP_MAX: Final[float] = 0.05
# Maximum allowed deviation (z-score) for a feature column's mean shift
# between consecutive training windows.
POISONING_FEATURE_SHIFT_Z_MAX: Final[float] = 4.0
# Minimum samples below which detection becomes statistically meaningless.
POISONING_MIN_SAMPLE_FLOOR: Final[int] = 32

# Build-fail threshold — any nightly poisoning test that shifts model
# precision by more than this is considered a regression.
POISONING_PRECISION_DROP_FAIL: Final[float] = 0.01

# ---------------------------------------------------------------------------
# Stream / bus identifiers
# ---------------------------------------------------------------------------
# Phase 5 publishes verdicts back onto Phase 4's intake pattern so they
# show up alongside other observations.
HARDEN_VERDICT_STREAM: Final[str] = "aegis:phase5:verdicts"
HARDEN_VERDICT_CONSUMER_GROUP: Final[str] = "aegis-harden"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_REQUEST_TIMEOUT_S: Final[float] = 20.0
DEFAULT_RNG_SEED: Final[int] = 0xA5615  # reproducibility default
