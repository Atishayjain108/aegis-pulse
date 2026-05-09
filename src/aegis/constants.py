"""Project-wide named constants.

Per the project charter, every magic number becomes a named constant here with
a rationale comment. If a constant is only used in one module, it may stay
there; but if two or more modules touch the same value, it migrates to this
file.

Author: AEGIS Pulse Team
Relationship: foundation — imported by ~every module in ``src/aegis/``.
"""

from __future__ import annotations

from typing import Final

# =============================================================================
# Project identity
# =============================================================================

PROJECT_NAME: Final[str] = "aegis-pulse"
SCHEMA_VERSION: Final[str] = "1.0"
"""Bumped only on BREAKING changes to the ``ProductSignal`` schema.
Consumers read this and may refuse to deserialize incompatible payloads."""

# =============================================================================
# Networking timeouts — keep conservative, increase per-call if a source needs it
# =============================================================================

HTTP_CONNECT_TIMEOUT_SECONDS: Final[float] = 10.0
"""Max time to establish a TCP+TLS handshake. Past this we assume the
endpoint is hostile/overloaded and the circuit breaker should trip."""

HTTP_READ_TIMEOUT_SECONDS: Final[float] = 30.0
"""Max time to receive a single response. Longer than CONNECT because
scraping targets occasionally stall after headers (especially Cloudflare)."""

HTTP_TOTAL_TIMEOUT_SECONDS: Final[float] = 60.0
"""Absolute cap on a single request (connect + TLS + read combined).
Phase 1 KPI: end-to-end pipeline < 2 min, so no single HTTP call may exceed 1 min."""

# =============================================================================
# Retry / backoff (decorrelated jitter — AWS recipe)
# =============================================================================

RETRY_MAX_ATTEMPTS_DEFAULT: Final[int] = 3
"""Default retries for idempotent reads. Writes use 1 (at-most-once)."""

RETRY_BASE_DELAY_SECONDS: Final[float] = 1.0
RETRY_MAX_DELAY_SECONDS: Final[float] = 64.0
"""Caps exponential backoff so a stuck worker does not wait minutes between tries."""

# =============================================================================
# Circuit breaker
# =============================================================================

CIRCUIT_BREAKER_FAIL_THRESHOLD: Final[int] = 5
"""Consecutive failures after which the breaker trips."""

CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS: Final[float] = 120.0
"""Time in OPEN state before a single probe request is allowed (HALF_OPEN)."""

CIRCUIT_BREAKER_ERROR_RATE_WINDOW_SECONDS: Final[float] = 60.0
"""Sliding window for the error-rate trigger (alongside consecutive count)."""

CIRCUIT_BREAKER_ERROR_RATE_THRESHOLD: Final[float] = 0.30
"""Trip if > 30% of requests in the last WINDOW seconds failed (prompt-specified)."""

# =============================================================================
# Cache — Redis TTLs per data category
# =============================================================================

CACHE_TTL_HOT_SECONDS: Final[int] = 60
"""Signal metadata that changes minute-to-minute (live counts, etc.)."""

CACHE_TTL_WARM_SECONDS: Final[int] = 600
"""Content that changes hourly (hashtag growth, trend scoring inputs)."""

CACHE_TTL_COLD_SECONDS: Final[int] = 86_400
"""Relatively static content (author bio, product descriptor)."""

CACHE_KEY_MAX_LEN: Final[int] = 512
"""Safety ceiling — Redis accepts larger keys but they destroy hit rate."""

# =============================================================================
# Priority queue — Redis sorted set score bands
# =============================================================================

PRIORITY_P0_BOUNDARY: Final[float] = 0.0
PRIORITY_P1_BOUNDARY: Final[float] = 1_000.0
PRIORITY_P2_BOUNDARY: Final[float] = 2_000.0
PRIORITY_P3_BOUNDARY: Final[float] = 3_000.0
"""Lower score = higher priority. Bands keep the same sorted-set mechanic
but let downstream workers filter by priority tier cheaply using ZRANGEBYSCORE."""

# =============================================================================
# Feature engineering
# =============================================================================

VELOCITY_WINDOWS_HOURS: Final[tuple[int, ...]] = (1, 6, 24, 72)
"""Rolling windows over which velocity + acceleration are computed.
Choice rationale: 1h catches flash spikes, 6h confirms, 24h dedupes noise,
72h separates real trends from weekly periodicity."""

TEMPORAL_DECAY_HALF_LIFE_HOURS: Final[float] = 24.0
"""Signals older than this contribute less than half weight. Prompt rule:
'stale data is dead weight'."""

EMBEDDING_DIM_TEXT: Final[int] = 1024
"""BGE-M3 dense embedding dimension. Fixed across model versions for HNSW
index stability; if you switch models, rebuild the index."""

EMBEDDING_DIM_IMAGE: Final[int] = 768
"""CLIP ViT-L/14 embedding dimension."""

EMBEDDING_DIM_CROSS_MODAL: Final[int] = 512
"""Projection dimension for cross-modal coherence (both text + image mapped here)."""

CROSS_MODAL_COHERENCE_MIN: Final[float] = -1.0
CROSS_MODAL_COHERENCE_MAX: Final[float] = 1.0
"""Cosine similarity range (post-normalisation)."""

# =============================================================================
# Creator tier classifier — follower-count boundaries
# =============================================================================

CREATOR_TIER_NANO_MAX_FOLLOWERS: Final[int] = 10_000
CREATOR_TIER_MICRO_MAX_FOLLOWERS: Final[int] = 100_000
CREATOR_TIER_MID_MAX_FOLLOWERS: Final[int] = 500_000
CREATOR_TIER_MACRO_MAX_FOLLOWERS: Final[int] = 1_000_000
# Above MACRO_MAX = MEGA
"""Boundaries per creator-economy industry consensus (2024).
Accounts below NANO_MAX with engagement/follower ratio > 0.5 are flagged as potential bots."""

BOT_ENGAGEMENT_RATIO_FLOOR: Final[float] = 0.005
"""Account with engagement rate below this is bot-suspect (human baseline ≈ 1–4%)."""

BOT_ENGAGEMENT_RATIO_CEILING: Final[float] = 0.50
"""Account with engagement rate above this is inauthentic-suspect
(typically coordinated inauthentic behaviour or paid engagement pods)."""

# =============================================================================
# Tenancy
# =============================================================================

DEFAULT_TENANT_UUID: Final[str] = "00000000-0000-0000-0000-000000000001"
"""Default tenant UUID for single-tenant deployments. Overridden by AEGIS_DEFAULT_TENANT_ID."""

# =============================================================================
# Database
# =============================================================================

DB_POOL_MIN_SIZE: Final[int] = 2
DB_POOL_MAX_SIZE: Final[int] = 16
"""Async pool bounds. MAX is tuned for a 16-core laptop; production overrides via env."""

DB_STATEMENT_TIMEOUT_MS: Final[int] = 30_000
"""server_settings statement_timeout. Kills run-away queries (e.g. missing index scans)."""

DB_COMMAND_TIMEOUT_SECONDS: Final[float] = 60.0
"""asyncpg command_timeout — client-side abort if a single query exceeds this."""

# =============================================================================
# Signal ingestion
# =============================================================================

SIGNAL_INGEST_BATCH_SIZE: Final[int] = 200
"""Number of ProductSignals flushed to DB in one COPY. Tuned against insert
throughput on a 16 GB laptop + TimescaleDB."""

SIGNAL_CONTENT_HASH_ALGO: Final[str] = "blake2b"
"""Why not SHA-256? BLAKE2b is faster on CPU and has the same collision
properties for our non-adversarial use case (deduplication, not security).
64-bit digest is enough for deduplication within a 90-day window."""

SIGNAL_CONTENT_HASH_DIGEST_BYTES: Final[int] = 16
"""128-bit — birthday bound of ~2^64 for a single dedup window. Ample."""

# =============================================================================
# Rate limiting — protect ourselves from being banned
# =============================================================================

PER_SOURCE_RPS_DEFAULT: Final[float] = 1.0
"""Conservative default. Each source adapter MUST set its own."""

PER_SOURCE_CONCURRENCY_DEFAULT: Final[int] = 4
"""asyncio.Semaphore width per source. Per-source override required."""

# =============================================================================
# Proxy pool
# =============================================================================

PROXY_HEALTH_EMA_ALPHA: Final[float] = 0.2
"""Exponential moving-average smoothing for proxy latency/success-rate.
Lower = slower to trust newly-bad proxies, but also slower to re-promote
recovered ones. 0.2 gives a ~5-sample half-life."""

PROXY_BAN_QUARANTINE_SECONDS: Final[int] = 1_800
"""Proxy flagged as banned sits out this long before re-eligibility (30 min)."""

PROXY_HEALTH_MIN_SAMPLES: Final[int] = 10
"""Don't score a proxy until we've seen at least this many requests through it —
prevents a single network flake from permanently banning a good proxy."""

# =============================================================================
# Security / PII
# =============================================================================

PII_SCRUB_PLACEHOLDER: Final[str] = "[PII]"
"""Drop-in replacement in text where NER/regex detected PII before persistence."""

HMAC_SIGNATURE_ALGO: Final[str] = "sha256"
"""Used for inter-agent message integrity (Phase 2) and signal provenance seal."""

# =============================================================================
# Observability
# =============================================================================

METRICS_HISTOGRAM_BUCKETS_SECONDS: Final[tuple[float, ...]] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0,
)
"""Wide latency range so a single histogram covers both fast (cached) paths and
slow (Cloudflare-challenged) paths without losing resolution in the middle."""

TRACE_SAMPLE_RATE_DEFAULT: Final[float] = 1.0
"""100% sampling in dev. Production override via env; recommended 0.05–0.25."""
