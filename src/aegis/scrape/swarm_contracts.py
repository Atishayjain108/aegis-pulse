"""
=============================================================================
SWARM INFRASTRUCTURE CONTRACTS — PHASE 0 AUDIT SUMMARY
=============================================================================

REPO AUDIT: performed 2026-05-19 against commit c40cc15

---- 1. ADAPTER scrape() SIGNATURE ------------------------------------------------
Adapters do NOT expose a free scrape() function. The pattern is class-based:

    class SomeAdapter(SourceAdapter[RawT]):
        async def fetch_raw(self, ctx: ScrapeContext, **params: Any) -> AsyncIterator[RawT]
        def parse(self, raw: RawT, ctx: ScrapeContext) -> ProductSignal | None
        async def setup(self, ctx: ScrapeContext) -> None          # optional
        async def teardown(self, ctx: ScrapeContext) -> None       # optional

    async for signal in adapter.run(**params):   # ← public entry point
        ...

    run() signature: async def run(self, **params: Any) -> AsyncIterator[ProductSignal]

SwarmAgentPool wraps the class-based adapters via a thin adapter_fn callable:
    adapter_fn(settings, http, limit) -> list[dict]
Phase 2 work (swarm_orchestrator.py) will bridge SourceAdapter.run() into that
callable interface. The contracts in this file are intentionally decoupled.

---- 2. DB SESSION PATTERN ---------------------------------------------------------
Module: aegis.db.pool.PgPool
Pattern: asynccontextmanager injection — NOT a global session object.

    async with pool.acquire(tenant_id=...) as conn:
        rows = await conn.fetch(...)

Module-level convenience: get_shared_pool() / set_shared_pool() — optional.
No SQLAlchemy, no ORM. Raw asyncpg throughout.

---- 3. REDIS CLIENT PATTERN -------------------------------------------------------
redis.asyncio (aioredis) — passed as dependency, not module-global.
Dashboard: aioredis.from_url(settings().redis_url_str, decode_responses=True)
Agents messaging: aegis.agents.messaging.RedisStreamsMessageBus
Swarm health persistence: hset / hgetall / expire on key aegis:swarm:agent_health

---- 4. DEDUP / PATTERNS / CONFIDENCE EXISTENCE ------------------------------------
deduplicate_batch(signals, *, threshold=0.82) → tuple[list[ProductSignal], int]
    Module: aegis.db.dedup
    Status: EXISTS

detect_patterns(signals, *, min_cluster_size=2, similarity_threshold=0.28,
                max_clusters=12) → list[PatternCluster]
    Module: aegis.scrape.patterns
    Status: EXISTS (includes Phase 5 velocity_slope + is_high_priority)

score_batch(signals, *, threshold=DEFAULT_CONFIDENCE_THRESHOLD) → ConfidenceResult
    Module: aegis.scrape.confidence
    Status: EXISTS (5-dimension gate; default threshold 0.85)

---- 5. TIER CONSTANTS (exact values from aegis.schemas.enums.SourceTier) ---------
TIER_1_INTENT     = "T1_intent"
TIER_2_COMMERCE   = "T2_commerce"
TIER_3_SEARCH     = "T3_search"
TIER_4_CULTURAL   = "T4_cultural"
TIER_5_ALTERNATIVE= "T5_alternative"

NOTE: The spec templates used "TIER_1_SOCIAL" / "TIER_2_COMMERCE" / "TIER_3_SEARCH"
as conceptual labels; the actual enum strings differ.  All normalizer/swarm code
in this phase uses the actual enum values above.

---- 6. PYTHON VERSION -------------------------------------------------------------
requires-python = ">=3.12,<3.13"   (hard constraint per pyproject.toml)

=============================================================================
"""
