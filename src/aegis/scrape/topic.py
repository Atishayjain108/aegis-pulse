"""Topic Intelligence — from one keyword to a full multi-source intelligence harvest.

Public surface::

    expansion = expand_topic("OpenAI")
    result    = await scrape_topic("OpenAI", pool=pool, tenant_id=tenant_id)

``expand_topic`` turns a single keyword into:
- A ranked list of search queries covering news, market, competitive, and temporal angles
- Related entity terms inferred from a built-in knowledge base
- A list of relevant subreddits inferred from the topic category
- Market-angle and competitive-landscape queries

``scrape_topic`` orchestrates ALL working adapters in parallel using those
expanded queries, semantically deduplicates, detects emerging patterns,
persists to the DB, and returns a rich ``TopicScrapeResult`` summary.

No LLM or external ML is required — all expansion is deterministic so the
command works with zero API keys.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from aegis.scrape.base import ScrapeContext

if TYPE_CHECKING:
    from aegis.db.pool import PgPool
    from aegis.schemas.signal import ProductSignal

_log = structlog.get_logger("aegis.scrape.topic")

# ---------------------------------------------------------------------------
# Entity knowledge base — maps known entities to related search terms
# When a topic matches a key, its related terms are added to the search plan.
# ---------------------------------------------------------------------------

_KNOWN_ENTITIES: dict[str, list[str]] = {
    # ── AI companies ──────────────────────────────────────────────────────
    "openai": ["chatgpt", "gpt-4o", "gpt-5", "sam altman", "openai o3"],
    "anthropic": ["claude ai", "claude sonnet", "claude opus", "dario amodei"],
    "chatgpt": ["openai", "gpt-4o", "chatgpt enterprise", "chatgpt plus"],
    "claude": ["anthropic", "claude 3.5", "claude haiku", "claude sonnet"],
    "gemini": ["google gemini", "gemini ultra", "google bard", "google deepmind"],
    "llama": ["meta llama", "llama 3", "meta ai", "open source llm"],
    "mistral": ["mistral ai", "mixtral", "mistral large", "european ai"],
    "perplexity": ["perplexity ai", "ai search engine", "sonar model"],
    "cursor": ["cursor ai", "ai code editor", "agentic coding", "vs code ai"],
    "deepseek": ["deepseek r1", "deepseek v3", "chinese ai", "open weights llm"],
    "grok": ["xai grok", "elon musk ai", "grok 3", "x ai"],
    "copilot": ["github copilot", "microsoft copilot", "azure openai", "copilot plus"],
    "stable diffusion": ["image generation", "comfyui", "midjourney alternative", "text to image"],
    "midjourney": ["ai art", "image generation", "dall-e", "stable diffusion"],

    # ── Chips & Hardware ─────────────────────────────────────────────────
    "nvidia": ["h100 gpu", "blackwell architecture", "jensen huang", "cuda ai", "nvidia ai"],
    "amd": ["mi300x", "radeon rx 9000", "lisa su", "amd vs nvidia", "amd ai chip"],
    "intel": ["intel gaudi 3", "intel foundry", "pat gelsinger", "lunar lake"],
    "tsmc": ["tsmc 2nm", "taiwan semiconductor", "chip foundry", "advanced packaging"],
    "arm": ["arm neoverse", "qualcomm snapdragon", "apple silicon", "risc-v vs arm"],
    "apple silicon": ["m4 chip", "apple m4", "neural engine", "macbook pro 2025"],

    # ── Big Tech ─────────────────────────────────────────────────────────
    "apple": ["iphone 17", "apple intelligence", "m4 pro", "vision pro", "tim cook"],
    "microsoft": ["azure ai", "github copilot", "bing ai", "satya nadella", "teams ai"],
    "google": ["google search ai", "waymo", "google cloud", "alphabet earnings", "sundar pichai"],
    "meta": ["meta ai assistant", "llama 4", "ray-ban smart glasses", "zuckerberg"],
    "amazon": ["aws bedrock", "amazon q", "alexa plus", "andy jassy"],
    "tesla": ["full self driving", "fsd v13", "cybertruck", "robotaxi", "elon musk"],
    "spacex": ["starship launch", "starlink", "falcon 9", "elon musk space"],
    "samsung": ["samsung galaxy s25", "one ui", "exynos", "samsung foldable"],
    "netflix": ["streaming war", "netflix password sharing", "netflix ad tier", "content spend"],
    "uber": ["uber eats", "autonomous taxi", "waymo vs uber", "ride share revenue"],

    # ── Finance & Markets ────────────────────────────────────────────────
    "federal reserve": ["fed rate cut", "fomc meeting", "jerome powell", "interest rate"],
    "inflation": ["cpi data", "core pce", "fed inflation target", "consumer prices"],
    "recession": ["gdp contraction", "unemployment surge", "credit tightening", "soft landing"],
    "ipo": ["ipo 2026 pipeline", "unicorn ipo", "direct listing", "spac merger"],
    "private equity": ["pe buyout", "leveraged buyout", "portfolio company", "pe fund returns"],
    "hedge fund": ["hedge fund performance", "short squeeze", "quant fund", "systematic trading"],

    # ── Crypto & Web3 ────────────────────────────────────────────────────
    "bitcoin": ["btc price today", "bitcoin etf inflows", "bitcoin halving", "digital gold"],
    "ethereum": ["eth price", "ethereum pectra upgrade", "staking yield", "vitalik buterin"],
    "solana": ["sol price", "solana meme coin", "solana dex volume", "solana nft"],
    "crypto": ["crypto market cap", "crypto regulation 2026", "digital asset", "exchange inflow"],
    "defi": ["total value locked", "yield farming apy", "liquidity pool", "dex aggregator"],
    "nft": ["nft market recovery", "digital collectible", "gaming nft", "ordinals"],
    "stablecoin": ["usdc regulation", "tether reserves", "cbdc", "stablecoin bill"],

    # ── Consumer & E-commerce ────────────────────────────────────────────
    "shopify": ["shopify merchant growth", "shopify plus", "shop pay", "ecommerce platform"],
    "tiktok shop": ["tiktok live shopping", "tiktok affiliate marketing", "social commerce"],
    "amazon fba": ["fulfillment by amazon", "private label seller", "amazon listing"],
    "dropshipping": ["winning dropshipping product", "alibaba supplier", "shopify dropship"],
    "print on demand": ["printful", "printify", "pod business", "merch on demand"],

    # ── Energy & Climate ─────────────────────────────────────────────────
    "solar": ["solar panel price drop", "rooftop solar installation", "utility solar farm"],
    "ev": ["electric vehicle sales", "ev charging infrastructure", "ev battery cost", "range anxiety"],
    "battery": ["solid state battery", "lithium iron phosphate", "battery energy storage"],
    "hydrogen": ["green hydrogen electrolyzer", "hydrogen fuel cell", "h2 economy"],
    "wind": ["offshore wind", "wind turbine", "wind energy capacity"],
    "nuclear": ["smr reactor", "small modular reactor", "nuclear fusion", "nuclear revival"],

    # ── Health & Pharma ─────────────────────────────────────────────────
    "ozempic": ["semaglutide", "wegovy", "glp-1 agonist", "weight loss drug", "novo nordisk"],
    "ai drug discovery": ["alphafold 3", "drug discovery ai", "generative biology", "protein folding"],
    "longevity": ["anti-aging", "rapamycin", "epigenetic clock", "lifespan extension"],

    # ── Gaming ───────────────────────────────────────────────────────────
    "gta 6": ["grand theft auto 6", "rockstar games release", "gta online"],
    "nvidia gaming": ["rtx 5090", "dlss 4", "gaming gpu 2026", "frame generation"],
    "steam": ["steam deck", "valve gaming", "steam sale", "indie game"],
}

# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "tech": [
        "ai", "ml", "llm", "gpt", "robot", "gpu", "chip", "semiconductor", "software",
        "saas", "cloud", "server", "cpu", "neural", "deeplearning", "pytorch", "python",
        "javascript", "startup", "vc", "venture", "developer", "code", "open source",
        "blockchain", "web3", "metaverse", "ar", "vr", "drone", "iot", "cybersecurity",
        "hacking", "quantum", "biotech", "nanotech", "automation", "programming",
    ],
    "finance": [
        "stock", "share", "equity", "bond", "etf", "fund", "hedge", "forex", "currency",
        "dollar", "euro", "yen", "trade", "market cap", "ipo", "spac", "valuation",
        "dividend", "interest rate", "fed", "central bank", "recession", "inflation",
        "gdp", "earnings", "revenue", "profit", "loss", "balance sheet", "analyst",
    ],
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "defi", "nft", "token", "wallet",
        "blockchain", "dao", "web3", "altcoin", "stablecoin", "yield", "staking",
        "mining", "halving", "exchange", "dex", "uniswap", "solana", "cardano",
    ],
    "consumer": [
        "product", "brand", "fashion", "clothing", "shoes", "sneaker", "watch", "bag",
        "beauty", "skincare", "makeup", "hair", "fitness", "gym", "yoga", "supplement",
        "vitamin", "food", "recipe", "restaurant", "travel", "hotel", "flight",
        "gadget", "headphone", "phone", "laptop", "gaming", "toy", "pet",
    ],
    "health": [
        "health", "medicine", "drug", "pharma", "vaccine", "disease", "cancer",
        "diabetes", "obesity", "mental health", "depression", "anxiety", "sleep",
        "diet", "nutrition", "exercise", "wellness", "therapy", "hospital", "doctor",
        "clinical", "fda", "ema", "trial", "genomics", "protein", "antibiotic",
    ],
    "gaming": [
        "game", "gaming", "esport", "steam", "nintendo", "playstation", "xbox",
        "fps", "rpg", "mmo", "indie", "loot", "battle royale", "moba", "streaming",
        "twitch", "youtube gaming", "vr game", "mobile game",
    ],
    "energy": [
        "solar", "wind", "battery", "ev", "electric vehicle", "nuclear", "oil", "gas",
        "lng", "coal", "renewable", "energy", "power grid", "hydrogen", "carbon",
        "emission", "climate", "sustainability", "green", "opec",
    ],
    "ecommerce": [
        "ecommerce", "shopify", "dropship", "amazon", "wholesale", "supplier", "alibaba",
        "retail", "d2c", "brand", "marketplace", "seller", "listing", "fulfillment",
        "logistics", "warehouse", "shipping", "seo", "ads", "conversion",
    ],
}

_CATEGORY_SUBREDDITS: dict[str, list[str]] = {
    "tech": [
        "technology", "MachineLearning", "artificial", "programming", "hardware",
        "LocalLLaMA", "ChatGPT", "OpenAI", "Futurology", "gadgets",
    ],
    "finance": [
        "investing", "stocks", "wallstreetbets", "SecurityAnalysis", "options",
        "personalfinance", "financialindependence", "Economics",
    ],
    "crypto": [
        "CryptoCurrency", "Bitcoin", "ethereum", "CryptoMarkets", "DeFi",
        "altcoin", "solana", "NFT",
    ],
    "consumer": [
        "BuyItForLife", "frugalmalefashion", "femalefashionadvice",
        "malefashionadvice", "Fitness", "beauty", "AskWomen",
    ],
    "health": [
        "Health", "nutrition", "loseit", "Fitness", "medicine",
        "Nootropics", "supplements", "mentalhealth",
    ],
    "gaming": [
        "gaming", "pcgaming", "GameDeals", "patientgamers",
        "truegaming", "indiegaming", "gamedev",
    ],
    "energy": [
        "energy", "solar", "electricvehicles", "teslamotors",
        "climate", "Renewables", "sustainability",
    ],
    "ecommerce": [
        "ecommerce", "Entrepreneur", "dropship", "shopify",
        "smallbusiness", "AmazonSeller", "FulfillmentByAmazon",
    ],
}

_DEFAULT_SUBREDDITS = ["news", "worldnews", "technology", "business", "Entrepreneur"]

# ---------------------------------------------------------------------------
# Market / angle expansion — by category
# ---------------------------------------------------------------------------

_MARKET_ANGLES: dict[str, list[str]] = {
    "tech": ["funding round", "valuation", "acquisition", "product launch", "IPO", "layoffs"],
    "finance": ["earnings report", "price target", "analyst upgrade", "quarterly results"],
    "crypto": ["price prediction", "regulation", "whale activity", "exchange listing"],
    "consumer": ["viral trend", "bestseller", "demand surge", "sold out", "review"],
    "health": ["clinical trial", "FDA approval", "side effects", "new study"],
    "gaming": ["release date", "gameplay reveal", "sales milestone", "review score"],
    "energy": ["production capacity", "cost per watt", "subsidy policy", "grid storage"],
    "ecommerce": ["profit margin", "ad strategy", "supplier deal", "platform fee"],
    "general": ["market opportunity", "growth trend", "investment", "partnership"],
}

_COMPETITIVE_ANGLES: dict[str, list[str]] = {
    "tech": ["vs competitor", "alternative", "market share"],
    "finance": ["comparison", "peer analysis"],
    "crypto": ["vs bitcoin", "market cap rank"],
    "consumer": ["vs brand", "comparison", "alternative"],
    "health": ["vs treatment", "comparison"],
    "gaming": ["vs game", "review comparison"],
    "energy": ["vs fossil fuel", "cost comparison"],
    "ecommerce": ["vs platform", "alternative"],
    "general": ["competitor", "alternative"],
}

_MAX_SEARCH_TERMS = 20
_MAX_SUBREDDITS = 8


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TopicExpansion:
    """Full expanded search plan for a single raw topic keyword."""

    base_topic: str
    category: str
    search_terms: list[str]          # queries for HN + Google News + Bing News
    reddit_subreddits: list[str]     # hot-feed subreddits to scrape
    related_entities: list[str]      # known entities related to this topic
    market_angle_queries: list[str]  # e.g. "NVIDIA valuation", "NVIDIA acquisition"
    competitor_queries: list[str]    # e.g. "NVIDIA vs AMD"
    temporal_queries: list[str]      # e.g. "NVIDIA 2026", "NVIDIA Q2 2026"


@dataclass
class TopicScrapeResult:
    """Rich summary returned by ``scrape_topic``."""

    topic: str
    expansion: TopicExpansion
    total_fetched: int = 0
    total_unique: int = 0
    total_inserted: int = 0
    duplicates_dropped: int = 0
    sources_hit: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    signals: list[Any] = field(default_factory=list)
    patterns: list[Any] = field(default_factory=list)  # list[PatternCluster]
    realtime_patterns: list[Any] = field(default_factory=list)  # PASS11: RealTimePattern
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    batch_confidence: float = 1.0
    """Overall data-quality confidence score [0, 1] from the Phase 5 confidence gate."""
    topic_type: str | None = None
    """PASS4: TopicType detected by the adapter-routing classifier (None if routing failed)."""
    routed_adapters: list[str] = field(default_factory=list)
    """PASS4: adapter names selected by the AdapterRouter (or the explicit override list)."""

    @property
    def duration_s(self) -> float:
        end = self.finished_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()


# ---------------------------------------------------------------------------
# Topic expansion
# ---------------------------------------------------------------------------


def _detect_category(topic: str) -> str:
    """Return the best-fit category slug for *topic*, or ``'general'``.

    Uses whole-word matching to avoid "ar" in "bar" false positives.
    """
    import re as _re

    lower = topic.lower()
    # Split topic into whole words for accurate keyword matching
    topic_words = set(_re.findall(r"[a-z0-9]+", lower))

    best_cat = "general"
    best_score = 0
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        score = 0
        for kw in keywords:
            kw_words = set(kw.lower().split())
            # Multi-word keyword: check if all words appear in topic
            # Single-word keyword: check for whole-word match
            if kw_words.issubset(topic_words):
                score += 1
        if score > best_score:
            best_score = score
            best_cat = cat

    return best_cat


def _find_related_entities(topic: str) -> list[str]:
    """Return known related search terms if the topic matches an entity."""
    lower = topic.lower().strip()
    # Exact match first
    if lower in _KNOWN_ENTITIES:
        return _KNOWN_ENTITIES[lower][:6]
    # Substring match — topic is contained in a key or a key is in topic
    for key, expansions in _KNOWN_ENTITIES.items():
        if key in lower or (len(lower) >= 4 and lower in key):
            return expansions[:6]
    return []


def _current_temporal_terms() -> list[str]:
    """Return temporal context strings for the current period."""
    now = datetime.now(UTC)
    year = now.year
    quarter = (now.month - 1) // 3 + 1
    month_name = now.strftime("%B")
    return [str(year), f"Q{quarter} {year}", f"{month_name} {year}", "latest", "2026"]


def expand_topic(topic: str) -> TopicExpansion:
    """Build the full search expansion for a single topic keyword.

    Generates diverse search queries covering:
    - The raw topic
    - Related entities from the knowledge base (e.g. "openai" → also "chatgpt", "gpt-4o")
    - Market intelligence angles (funding, acquisition, IPO, etc.)
    - Competitive landscape ("vs", "alternative", "competitor")
    - Temporal context (current year, quarter, month)
    - News / update angles
    """
    topic = topic.strip()
    category = _detect_category(topic)
    related = _find_related_entities(topic)
    topic_lower = topic.lower()

    # ── Core search terms ──────────────────────────────────────────────
    search_terms: list[str] = [topic]

    # Related entities (add them as standalone terms — they expand coverage)
    for entity in related[:4]:
        if entity.lower() not in topic_lower and len(search_terms) < _MAX_SEARCH_TERMS:
            search_terms.append(entity)

    # News + update suffixes
    for suffix in ["news", "latest", "update", "analysis"]:
        if suffix not in topic_lower and len(search_terms) < _MAX_SEARCH_TERMS:
            search_terms.append(f"{topic} {suffix}")

    # ── Market angles ──────────────────────────────────────────────────
    market_angles = _MARKET_ANGLES.get(category, _MARKET_ANGLES["general"])
    market_angle_queries: list[str] = []
    for angle in market_angles[:4]:
        q = f"{topic} {angle}"
        if len(search_terms) < _MAX_SEARCH_TERMS:
            search_terms.append(q)
        market_angle_queries.append(q)

    # ── Competitive angles ─────────────────────────────────────────────
    competitor_angles = _COMPETITIVE_ANGLES.get(category, _COMPETITIVE_ANGLES["general"])
    competitor_queries: list[str] = []
    for angle in competitor_angles[:2]:
        q = f"{topic} {angle}"
        if len(search_terms) < _MAX_SEARCH_TERMS:
            search_terms.append(q)
        competitor_queries.append(q)

    # ── Temporal angles ────────────────────────────────────────────────
    temporal_terms = _current_temporal_terms()
    temporal_queries: list[str] = []
    for t in temporal_terms[:3]:
        if t.lower() not in topic_lower and len(search_terms) < _MAX_SEARCH_TERMS:
            q = f"{topic} {t}"
            search_terms.append(q)
            temporal_queries.append(q)

    # ── Reddit search (all-of-Reddit via Google site: filter) ──────────
    # Add a site:reddit.com query so Google News returns Reddit discussions
    reddit_search_q = f"site:reddit.com {topic}"
    if len(search_terms) < _MAX_SEARCH_TERMS:
        search_terms.append(reddit_search_q)

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for t in search_terms:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    subreddits = _CATEGORY_SUBREDDITS.get(category, _DEFAULT_SUBREDDITS)[:_MAX_SUBREDDITS]

    _log.info(
        "topic.expanded",
        topic=topic,
        category=category,
        terms=len(deduped),
        related_entities=len(related),
        subreddits=len(subreddits),
    )
    return TopicExpansion(
        base_topic=topic,
        category=category,
        search_terms=deduped,
        reddit_subreddits=subreddits,
        related_entities=related,
        market_angle_queries=market_angle_queries,
        competitor_queries=competitor_queries,
        temporal_queries=temporal_queries,
    )


# ---------------------------------------------------------------------------
# Parallel scraping
# ---------------------------------------------------------------------------


_HTTP_MAX_CONCURRENCY: int = 8
"""Maximum number of adapter HTTP tasks that may run concurrently inside scrape_topic().
Keeps the outbound connection pool bounded; prevents thundering-herd timeouts."""

_ADAPTER_BUDGET_S: float = float(os.getenv("AEGIS_SCRAPE_ADAPTER_BUDGET_S", "20"))
"""Hard wall-clock budget for a single adapter run inside scrape_topic().
A blocked/slow source (e.g. a WAF 403 retry loop over many categories) returns
its partial results at this deadline instead of stalling the whole harvest."""


async def _semaphore_guarded(
    sem: asyncio.Semaphore,
    source_name: str,
    adapter: Any,
    run_kwargs: dict[str, Any],
) -> tuple[str, list[ProductSignal], str | None]:
    """Acquire *sem* before delegating to _scrape_source so that at most
    _HTTP_MAX_CONCURRENCY adapter tasks issue HTTP requests simultaneously."""
    async with sem:
        return await _scrape_source(source_name, adapter, run_kwargs)


async def _scrape_source(
    source_name: str,
    adapter: Any,
    run_kwargs: dict[str, Any],
) -> tuple[str, list[ProductSignal], str | None]:
    """Run one adapter and collect signals. Returns (name, signals, error|None).

    Never raises — any exception is captured and returned as the error string
    so the parallel gather loop can log and continue without interruption.
    """
    import contextlib

    signals: list[ProductSignal] = []
    ctx = ScrapeContext()
    error: str | None = None

    async def _drain() -> None:
        await adapter.setup(ctx)
        async for signal in adapter.run(**run_kwargs):
            signals.append(signal)

    try:
        # Hard per-adapter wall clock so one slow/blocked source (e.g. a WAF
        # 403 loop over many categories) can never stall the parallel harvest.
        await asyncio.wait_for(_drain(), timeout=_ADAPTER_BUDGET_S)
        _log.debug(
            "topic.source_ok",
            source=source_name,
            signals_collected=len(signals),
        )
    except (TimeoutError, asyncio.TimeoutError) as exc:  # noqa: UP041
        error = f"adapter timed out after {_ADAPTER_BUDGET_S:.0f}s"
        _log.warning(
            "topic.source_timeout",
            source=source_name,
            signals_collected=len(signals),
            budget_s=_ADAPTER_BUDGET_S,
            action="returning_partial_continuing",
        )
        del exc
    except Exception as exc:
        error = str(exc)
        _log.warning(
            "topic.source_failed",
            source=source_name,
            signals_collected=len(signals),
            error=error,
            error_type=type(exc).__name__,
            action="skipping_source_continuing",
        )
    finally:
        with contextlib.suppress(Exception):
            await adapter.teardown(ctx)

    return source_name, signals, error


# ---------------------------------------------------------------------------
# PASS4: Intelligent adapter routing — registry adapter resolution + scheduling
# ---------------------------------------------------------------------------

# Routed/override adapters that need the topic passed as `query` to be useful.
# E-commerce adapters here implement real keyword search (query-relevant products);
# adapters NOT listed only expose trending/offers pages, so we don't pass them a
# query they would silently ignore.
_ROUTED_QUERY_ADAPTERS: frozenset[str] = frozenset({
    "google-news", "bing-news", "hacker-news",
    "snapdeal", "flipkart", "amazon_in", "myntra",
    "ebay", "bestbuy", "etsy",
    # nykaa/ajio/meesho/indiamart removed 2026-06-24 — WAF-gated, no free path.
})


def _routing_extras_enabled() -> bool:
    """Whether routed extra adapters (beyond the core set) may be scheduled.

    Controlled by AEGIS_SCRAPE_TOPIC_ROUTING_EXTRAS (default: enabled). The
    unit-test environment disables it so scrape_topic stays hermetic.
    """
    import os

    raw = os.environ.get("AEGIS_SCRAPE_TOPIC_ROUTING_EXTRAS", "true")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _resolve_registry_entry(name: str, registry: dict[str, Any]) -> Any | None:
    """Resolve a registry entry tolerating hyphen/underscore naming differences.

    The swarm registry mixes conventions: file-derived names use ``_``
    (``reddit_finance``) while some legacy RSS adapters use ``-``
    (``hacker-news``, ``google-news``). Callers (and the router) may pass
    either form, so an exact lookup silently rejects valid adapters. This
    tries the exact key first, then a normalized key where ``-`` and ``_`` are
    treated as equivalent.
    """
    entry = registry.get(name)
    if entry is not None:
        return entry
    norm = name.replace("-", "_")
    for key, val in registry.items():
        if key.replace("-", "_") == norm:
            return val
    return None


def _build_registry_adapter(name: str) -> Any | None:
    """Instantiate a swarm-registry adapter by name.

    Returns None (never raises) when the name is unknown or the adapter
    module/config cannot be constructed — the caller logs and skips it.
    """
    import importlib

    try:
        from aegis.scrape.swarm import _REGISTRY

        entry = _resolve_registry_entry(name, _REGISTRY)
        if entry is None:
            return None
        module_path, cls_name, cfg_name, _tier, _risk = entry
        mod = importlib.import_module(module_path)
        adapter_cls = getattr(mod, cls_name)
        if cfg_name is not None:
            cfg_cls = getattr(mod, cfg_name)
            return adapter_cls(cfg_cls())
        from aegis.scrape.sources._rss_base import RSSAdapterConfig

        return adapter_cls(RSSAdapterConfig())
    except Exception as exc:
        _log.debug("topic.registry_adapter_unavailable", adapter=name, error=str(exc))
        return None


def _schedule_routed_adapters(
    names: list[str],
    *,
    sem: asyncio.Semaphore,
    tasks: list[asyncio.Task[tuple[str, list[ProductSignal], str | None]]],
    topic: str,
    limit_per_source: int,
) -> list[str]:
    """Schedule swarm-registry adapters by name; return the names scheduled."""
    scheduled: list[str] = []
    for name in names:
        adapter = _build_registry_adapter(name)
        if adapter is None:
            _log.debug("topic.routed_adapter_skipped", adapter=name)
            continue
        run_kwargs: dict[str, Any] = {"limit": limit_per_source}
        if name in _ROUTED_QUERY_ADAPTERS:
            run_kwargs["query"] = topic
        tasks.append(
            asyncio.create_task(
                _semaphore_guarded(sem, f"{name}:routed", adapter, run_kwargs)
            )
        )
        scheduled.append(name)
    return scheduled


def _schedule_core_adapters(
    expansion: TopicExpansion,
    *,
    sem: asyncio.Semaphore,
    tasks: list[asyncio.Task[tuple[str, list[ProductSignal], str | None]]],
    topic: str,
    limit_per_source: int,
) -> set[str]:
    """Schedule the always-on core adapters (HN, news RSS, Reddit, GitHub, Amazon).

    Returns the set of swarm-registry names this covers, so the routed-extras
    path does not double-schedule the same platforms.
    """
    covered: set[str] = {"hacker-news", "google-news", "bing-news"}

    # ── HackerNews: search top terms ──────────────────────────────────
    from aegis.scrape.sources.hacker_news import HackerNewsAdapter, HackerNewsConfig

    hn_terms = [t for t in expansion.search_terms if "site:reddit.com" not in t][:7]
    for term in hn_terms:
        adapter_hn = HackerNewsAdapter(HackerNewsConfig(search_by_date=True))
        tasks.append(
            asyncio.create_task(
                _semaphore_guarded(
                    sem,
                    f"hacker_news:{term[:30]}",
                    adapter_hn,
                    {"query": term, "limit": limit_per_source},
                )
            )
        )

    # ── Google News RSS: search all terms ────────────────────────────
    from aegis.scrape.sources.google_news_rss import GoogleNewsRSSAdapter, GoogleNewsRSSConfig

    gn_terms = expansion.search_terms[:8]  # includes site:reddit.com term
    for term in gn_terms:
        adapter_gn = GoogleNewsRSSAdapter(GoogleNewsRSSConfig())
        tasks.append(
            asyncio.create_task(
                _semaphore_guarded(
                    sem,
                    f"google_news:{term[:30]}",
                    adapter_gn,
                    {"query": term, "limit": limit_per_source},
                )
            )
        )

    # ── Bing News RSS: independent news coverage ──────────────────────
    from aegis.scrape.sources.bing_news_rss import BingNewsRSSAdapter, BingNewsRSSConfig

    bing_terms = [t for t in expansion.search_terms if "site:reddit.com" not in t][:6]
    for term in bing_terms:
        adapter_bn = BingNewsRSSAdapter(BingNewsRSSConfig())
        tasks.append(
            asyncio.create_task(
                _semaphore_guarded(
                    sem,
                    f"bing_news:{term[:30]}",
                    adapter_bn,
                    {"query": term, "limit": limit_per_source},
                )
            )
        )

    # ── Reddit RSS: hot feeds from category subreddits ────────────────
    from aegis.scrape.sources.reddit_rss import RedditRSSAdapter, RedditRSSConfig

    for sub in expansion.reddit_subreddits[:5]:
        adapter_r = RedditRSSAdapter(RedditRSSConfig(subreddits=(sub,), listing="hot"))
        tasks.append(
            asyncio.create_task(
                _semaphore_guarded(
                    sem,
                    f"reddit:{sub}",
                    adapter_r,
                    {"subreddit": sub, "limit": limit_per_source},
                )
            )
        )
    # Also scrape r/all new for real-time coverage
    adapter_r_all = RedditRSSAdapter(RedditRSSConfig(subreddits=("all",), listing="new"))
    tasks.append(
        asyncio.create_task(
            _scrape_source(
                "reddit:all_new",
                adapter_r_all,
                {"subreddit": "all", "limit": limit_per_source // 2},
            )
        )
    )

    # ── GitHub Trending: always relevant for tech; also ecommerce ────
    if expansion.category in ("tech", "gaming", "ecommerce", "general"):
        from aegis.scrape.sources.github_trending import (
            GitHubTrendingAdapter,
            GitHubTrendingConfig,
        )

        covered.add("github-trending")
        adapter_gh = GitHubTrendingAdapter(GitHubTrendingConfig())
        tasks.append(
            asyncio.create_task(
                _semaphore_guarded(
                    sem,
                    "github_trending",
                    adapter_gh,
                    {"limit": limit_per_source},
                )
            )
        )

    # ── Amazon Bestsellers: consumer / ecommerce / health / gaming ───
    if expansion.category in ("consumer", "ecommerce", "health", "gaming"):
        from aegis.scrape.sources.amazon import AmazonAdapter, AmazonConfig

        covered.add("amazon")
        adapter_amz = AmazonAdapter(AmazonConfig())
        tasks.append(
            asyncio.create_task(
                _semaphore_guarded(
                    sem,
                    "amazon",
                    adapter_amz,
                    {"query": topic, "limit": limit_per_source},
                )
            )
        )

    return covered


async def scrape_topic(
    topic: str,
    *,
    pool: PgPool | None = None,
    tenant_id: UUID | None = None,
    limit_per_source: int = 30,
    dry_run: bool = False,
    dedup_threshold: float = 0.82,
    dedup_lookback_hours: int = 72,
    detect_patterns: bool = True,
    stream_client: Any | None = None,
    adapter_override: list[str] | None = None,
    max_adapters: int = 12,
) -> TopicScrapeResult:
    """The single entry-point for topic-based intelligence scraping.

    Runs the core no-auth adapters in parallel across all expanded queries
    (HackerNews · Google News · Bing News · Reddit · GitHub Trending ·
    Amazon Bestsellers), then — PASS4 — uses the intelligent AdapterRouter
    to schedule additional swarm-registry adapters matched to the topic
    type (e.g. nse_bse + moneycontrol for finance queries, flipkart +
    amazon_in for e-commerce queries).

    Semantically deduplicates results and runs pattern detection before
    persisting to the DB.

    Args:
        topic: A single keyword or short phrase (e.g. "AI chips").
        pool: A live PgPool. If None, signals are collected but not stored.
        tenant_id: Tenant UUID for DB writes.
        limit_per_source: Max signals per individual adapter run.
        dry_run: Collect and deduplicate but do not write to DB.
        dedup_threshold: Similarity threshold for near-duplicate detection.
        dedup_lookback_hours: Hours back to check DB for semantic duplicates.
        detect_patterns: Whether to cluster signals into emerging themes.
        stream_client: Optional Redis client for the real-time stream bridge.
        adapter_override: PASS4 — run EXACTLY these swarm-registry adapter
            names instead of the core set + routing (for explicit user
            requests or testing). Unknown names are skipped with a log.
        max_adapters: PASS4 — cap on routed adapter recommendations.
    """
    expansion = expand_topic(topic)
    result = TopicScrapeResult(topic=topic, expansion=expansion)

    # Bind a time-seeded HardenShim for this session so all parallel adapter
    # tasks share one advancing RNG sequence — avoids every task repeating
    # the same default-seed fingerprint pattern.
    _shim_token = None
    try:
        import time as _time

        from aegis.scrape.harden_shim import HardenShim as _HardenShim
        from aegis.scrape.harden_shim import set_session_shim as _set_session_shim

        _shim_token = _set_session_shim(
            _HardenShim(rng_seed=_time.time_ns() & 0xFFFF_FFFF)
        )
    except Exception:
        pass

    _sem = asyncio.Semaphore(_HTTP_MAX_CONCURRENCY)
    tasks: list[asyncio.Task[tuple[str, list[ProductSignal], str | None]]] = []

    # ── PASS4: adapter selection — explicit override or intelligent routing ──
    routed_names: list[str] = []
    if adapter_override is not None:
        result.routed_adapters = list(adapter_override)
        _log.info("topic.explicit_adapters", topic=topic, adapters=adapter_override)
        scheduled = _schedule_routed_adapters(
            list(adapter_override),
            sem=_sem,
            tasks=tasks,
            topic=topic,
            limit_per_source=limit_per_source,
        )
        for missing in set(adapter_override) - set(scheduled):
            result.errors.append(f"adapter_override: unknown adapter '{missing}'")
    else:
        try:
            from aegis.scrape.adapter_router import AdapterRouter

            recs = AdapterRouter().route(topic, top_n=max_adapters)
            result.topic_type = recs[0].topic_type.value if recs else None
            routed_names = [
                r.adapter_name
                for r in recs
                if r.credentials_available or not r.requires_credentials
            ]
            result.routed_adapters = routed_names
            _log.info(
                "topic.routed",
                topic=topic,
                topic_type=result.topic_type,
                adapters=routed_names[:5],
                total=len(routed_names),
            )
        except Exception as exc:
            _log.warning(
                "topic.routing_failed", error=str(exc), fallback="default_adapters"
            )

        covered = _schedule_core_adapters(
            expansion,
            sem=_sem,
            tasks=tasks,
            topic=topic,
            limit_per_source=limit_per_source,
        )

        # Routed extras: registry adapters the core set does not already cover.
        # Gated so unit tests / constrained envs can keep the core-only path.
        if routed_names and _routing_extras_enabled():
            extras = [n for n in routed_names if n not in covered]
            scheduled = _schedule_routed_adapters(
                extras,
                sem=_sem,
                tasks=tasks,
                topic=topic,
                limit_per_source=limit_per_source,
            )
            if scheduled:
                _log.info(
                    "topic.routed_extras_scheduled", topic=topic, adapters=scheduled
                )

    # ── Gather all in parallel ─────────────────────────────────────────
    all_signals: list[ProductSignal] = []
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for outcome in completed:
        if isinstance(outcome, BaseException):
            result.errors.append(str(outcome))
            continue
        src_name, signals, error = outcome
        if error:
            result.errors.append(f"{src_name}: {error}")
        if signals:
            all_signals.extend(signals)
            base = src_name.split(":")[0]
            if base not in result.sources_hit:
                result.sources_hit.append(base)

    result.total_fetched = len(all_signals)

    # ── Phase 5: Data confidence gate ────────────────────────────────────
    # Score the raw batch before dedup. A low score means the data may be
    # poisoned, layout-broken, or hallucinated — and triggers structured
    # remediation hints in the logs. The pipeline continues regardless
    # (graceful degradation), but the score is surfaced in the result so
    # the dashboard and Executive Agents can weight it accordingly.
    try:
        from aegis.config import settings
        from aegis.scrape.confidence import score_batch

        _threshold = settings().scrape.confidence_threshold
        # PASS2-2C: prefer the outcome-adapted gate when one has been
        # published; falls back to the static setting on any failure.
        try:
            from aegis.core.dynamic_thresholds import get_thresholds

            _dyn = await get_thresholds()
            _threshold = await _dyn.get_confidence_gate(fallback=_threshold)
            # BRAIN-3: publish the adaptive gate to the confidence module's
            # injection point so any sync caller picks up the same value.
            from aegis.scrape.confidence import set_confidence_threshold

            set_confidence_threshold(_threshold)
        except Exception:
            pass
        conf = await asyncio.to_thread(score_batch, all_signals, threshold=_threshold)
        result.batch_confidence = conf.overall_score
        if not conf.passed:
            _log.warning(
                "topic.confidence_below_threshold",
                topic=topic,
                overall_score=conf.overall_score,
                threshold=conf.threshold,
                dimensions=conf.dimensions,
                remediation_hints=conf.remediation_hints,
                sources_hit=result.sources_hit,
                sources_errored=len(result.errors),
            )
    except Exception as exc:
        _log.warning("topic.confidence_gate_error", error=str(exc))

    # ── Semantic deduplication ────────────────────────────────────────
    if pool is not None and tenant_id is not None and not dry_run:
        from aegis.db.dedup import deduplicate_signals

        unique_signals, dropped = await deduplicate_signals(
            pool,
            tenant_id,
            all_signals,
            threshold=dedup_threshold,
            lookback_hours=dedup_lookback_hours,
        )
    else:
        from aegis.db.dedup import deduplicate_batch

        unique_signals, dropped = await asyncio.to_thread(
            deduplicate_batch, all_signals, threshold=dedup_threshold
        )

    result.total_unique = len(unique_signals)
    result.duplicates_dropped = dropped + (result.total_fetched - len(all_signals))
    result.signals = unique_signals

    # ── Pattern detection ─────────────────────────────────────────────
    if detect_patterns and unique_signals:
        try:
            import importlib

            # PASS2-2C: inject the adaptive high-priority slope before the
            # sync clustering runs in a worker thread (it cannot await).
            try:
                from aegis.core.dynamic_thresholds import get_thresholds
                from aegis.scrape.analytics import set_high_priority_slope

                _dyn = await get_thresholds()
                set_high_priority_slope(await _dyn.get_velocity_slope())
            except Exception:
                pass

            _pat_mod = importlib.import_module("aegis.scrape.patterns")
            clusters: list[Any] = await asyncio.to_thread(
                _pat_mod.detect_patterns, unique_signals, min_cluster_size=2
            )
            result.patterns = clusters
            _log.info(
                "topic.patterns_detected",
                topic=topic,
                clusters=len(result.patterns),
            )
        except Exception as exc:
            _log.warning("topic.pattern_detection_failed", error=str(exc))

        # PASS11: real-time first-pass pattern recognition (breakout detection).
        # Runs alongside the batch clusterer; never blocks the harvest result.
        try:
            from aegis.scrape.pattern_engine import PatternEngine

            rt_patterns = await asyncio.to_thread(
                PatternEngine(min_cluster_size=2).detect, unique_signals
            )
            result.realtime_patterns = rt_patterns
            breakouts = sum(1 for p in rt_patterns if p.is_breakout)
            _log.info(
                "topic.realtime_patterns_detected",
                topic=topic,
                patterns=len(rt_patterns),
                breakouts=breakouts,
            )
        except Exception as exc:
            _log.warning("topic.realtime_pattern_failed", error=str(exc))

    # ── Persist unique signals ────────────────────────────────────────
    if pool is not None and tenant_id is not None and not dry_run and unique_signals:
        from aegis.db.signals import insert_signals

        try:
            inserted = await insert_signals(pool, unique_signals, tenant_id=tenant_id)
            result.total_inserted = inserted
        except Exception as exc:
            result.errors.append(f"db_insert: {exc}")
            _log.error("topic.insert_failed", error=str(exc))
    elif dry_run:
        result.total_inserted = 0

    # ── Real-time stream bridge (best-effort, non-blocking) ──────────
    if stream_client is not None and unique_signals:
        from aegis.scrape.stream_bridge import emit_to_stream

        await emit_to_stream(
            stream_client,
            [s.model_dump(mode="json") for s in unique_signals],
            topic=topic,
            tenant_id=str(tenant_id) if tenant_id is not None else "default",
        )

    result.finished_at = datetime.now(UTC)
    _log.info(
        "topic.scrape_complete",
        topic=topic,
        category=expansion.category,
        fetched=result.total_fetched,
        unique=result.total_unique,
        inserted=result.total_inserted,
        dropped=result.duplicates_dropped,
        patterns=len(result.patterns),
        duration_s=round(result.duration_s, 1),
        sources=result.sources_hit,
        errors=len(result.errors),
        batch_confidence=round(result.batch_confidence, 3),
        high_priority_clusters=sum(
            1 for p in result.patterns if getattr(p, "is_high_priority", False)
        ),
    )
    return result


# ---------------------------------------------------------------------------
# Topic intelligence router — keyword → relevant adapter list
# ---------------------------------------------------------------------------

FINANCE_KEYWORDS: frozenset[str] = frozenset([
    "stock", "market", "invest", "fund", "crypto", "bitcoin", "nse", "bse",
    "sensex", "nifty", "ipo", "share", "dividend", "bond", "forex",
])
ECOMMERCE_KEYWORDS: frozenset[str] = frozenset([
    "product", "shop", "buy", "sell", "ecommerce", "fashion", "clothing",
    "beauty", "electronics", "amazon", "flipkart", "meesho", "myntra",
])
TECH_KEYWORDS: frozenset[str] = frozenset([
    "software", "api", "developer", "code", "github", "npm", "saas",
    "startup", "ai", "llm", "machine learning", "framework",
    "kubernetes", "docker", "cloud", "devops", "backend", "frontend",
])

FINANCE_ADAPTERS: list[str] = [
    "moneycontrol", "economic_times", "nse_bse", "yahoo_finance",
    "reddit_finance", "screener_in", "investing_com",
]
ECOMMERCE_ADAPTERS: list[str] = [
    "ebay", "bestbuy", "etsy",  # free real APIs (key-gated, fail-open)
    "flipkart", "amazon_in", "myntra", "reddit_ecommerce", "snapdeal",
    # nykaa/ajio/meesho/indiamart removed 2026-06-24 — WAF-gated, no free path.
]
TECH_ADAPTERS: list[str] = [
    "techcrunch", "devto", "github_public", "npm_trends", "wired",
]
BASE_ADAPTERS: list[str] = [
    "bbc_news", "reuters", "ndtv_profit", "mint", "business_standard",
    "google_trends_india", "medium", "youtube_rss",
]

_MAX_TOPIC_ADAPTERS = 15


def topic_to_relevant_adapters(topic: str) -> list[str]:
    """Map a topic string to the 10–15 most relevant adapter names.

    Uses keyword matching against FINANCE_KEYWORDS, ECOMMERCE_KEYWORDS, and
    TECH_KEYWORDS. BASE_ADAPTERS (news + trends) are always included.
    No LLM required — pure keyword matching.

    Returns adapter names in priority order, capped at _MAX_TOPIC_ADAPTERS.
    """
    topic_lower = topic.lower()
    selected: list[str] = []

    if any(kw in topic_lower for kw in FINANCE_KEYWORDS):
        selected.extend(FINANCE_ADAPTERS)
    if any(kw in topic_lower for kw in ECOMMERCE_KEYWORDS):
        selected.extend(ECOMMERCE_ADAPTERS)
    if any(kw in topic_lower for kw in TECH_KEYWORDS):
        selected.extend(TECH_ADAPTERS)
    selected.extend(BASE_ADAPTERS)

    # Deduplicate preserving order, cap at _MAX_TOPIC_ADAPTERS
    seen: set[str] = set()
    result: list[str] = []
    for adapter in selected:
        if adapter not in seen:
            seen.add(adapter)
            result.append(adapter)
        if len(result) >= _MAX_TOPIC_ADAPTERS:
            break

    return result
