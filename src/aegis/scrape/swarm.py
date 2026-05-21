"""SwarmOrchestrator — multi-wave parallel scraping across 30+ adapters.

Architecture::

    SwarmOrchestrator
      ├── SwarmAgentPool     (swarm_agents.py — health-tracked adapter runners)
      ├── ConcurrencyGovernor (governor.py — rate limiting + jitter)


      ├── _SwarmAnalyzer     (theme + category extraction; heuristic, no LLM)
      ├── _SwarmPersistence  (DB swarm_results insert + Redis publish)
      └── _SwarmSynthesizer  (builds SwarmResult with conclusion template)

Waves run sequentially so later waves can benefit from early signals,
but within each wave all agents run concurrently under the governor.
"""
from __future__ import annotations

import contextlib
import importlib
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import structlog

from aegis.scrape.governor import ConcurrencyGovernor
from aegis.scrape.normalizer import normalize_swarm_batch
from aegis.scrape.result import AdapterCapabilities, AdapterRun, AgentHealth
from aegis.scrape.schema_guard import validate_batch
from aegis.scrape.swarm_agents import ScraperAgent, SwarmAgentPool
from aegis.scrape.swarm_result import SwarmResult, WaveStats

_log = structlog.get_logger("aegis.scrape.swarm")

# ---------------------------------------------------------------------------
# Wave definitions
# ---------------------------------------------------------------------------

WAVE_1_SOCIAL: list[str] = [
    "reddit_finance", "reddit_ecommerce", "devto", "producthunt",
    "google_trends_india", "youtube_rss", "medium",
]
WAVE_2_NEWS: list[str] = [
    "techcrunch", "wired", "bbc_news", "reuters", "ndtv_profit",
    "mint", "business_standard", "economic_times", "moneycontrol",
    "yahoo_finance", "investing_com",
]
WAVE_3_ECOMMERCE: list[str] = [
    "flipkart", "meesho", "myntra", "indiamart", "ajio",
    "nykaa", "snapdeal", "amazon_in", "amazon",
    "nse_bse", "screener_in",
]
WAVE_4_TECH: list[str] = [
    "github_public", "npm_trends", "hacker-news",
    "github-trending", "google-news", "bing-news",
]

ALL_WAVE_NAMES = WAVE_1_SOCIAL + WAVE_2_NEWS + WAVE_3_ECOMMERCE + WAVE_4_TECH

# ---------------------------------------------------------------------------
# Adapter registry
# name → (module_path, adapter_cls_name, config_cls_name | None, tier, anti_bot_risk)
# config_cls_name=None means the adapter uses RSSAdapterConfig from _rss_base.
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, tuple[str, str, str | None, str, str]] = {
    # Wave 1 — Social / Trends
    "reddit_finance":      ("aegis.scrape.sources.reddit_finance",        "RedditFinanceAdapter",        "RedditFinanceConfig",        "T4_cultural", "low"),
    "reddit_ecommerce":    ("aegis.scrape.sources.reddit_ecommerce",      "RedditEcommerceAdapter",      "RedditEcommerceConfig",      "T4_cultural", "low"),
    "devto":               ("aegis.scrape.sources.devto",                 "DevToAdapter",                "DevToConfig",                "T4_cultural", "low"),
    "producthunt":         ("aegis.scrape.sources.producthunt",           "ProductHuntAdapter",          "ProductHuntConfig",          "T4_cultural", "medium"),
    "google_trends_india": ("aegis.scrape.sources.google_trends_india",   "GoogleTrendsIndiaAdapter",    "GoogleTrendsIndiaConfig",    "T3_search",   "low"),
    "youtube_rss":         ("aegis.scrape.sources.youtube_rss",           "YouTubeRSSAdapter",           "YouTubeRSSConfig",           "T4_cultural", "low"),
    "medium":              ("aegis.scrape.sources.medium_rss",            "MediumRSSAdapter",            None,                         "T4_cultural", "low"),
    # Wave 2 — News
    "techcrunch":          ("aegis.scrape.sources.techcrunch_rss",        "TechCrunchRSSAdapter",        None,                         "T3_search",   "low"),
    "wired":               ("aegis.scrape.sources.wired_rss",             "WiredRSSAdapter",             None,                         "T3_search",   "low"),
    "bbc_news":            ("aegis.scrape.sources.bbc_business",          "BBCBusinessAdapter",          None,                         "T3_search",   "low"),
    "reuters":             ("aegis.scrape.sources.reuters_rss",           "ReutersRSSAdapter",           None,                         "T3_search",   "low"),
    "ndtv_profit":         ("aegis.scrape.sources.ndtv_profit",           "NDTVProfitAdapter",           None,                         "T3_search",   "low"),
    "mint":                ("aegis.scrape.sources.mint_rss",              "MintRSSAdapter",              None,                         "T3_search",   "low"),
    "business_standard":   ("aegis.scrape.sources.business_standard_rss", "BusinessStandardRSSAdapter",  None,                         "T3_search",   "low"),
    "economic_times":      ("aegis.scrape.sources.economic_times_markets","EconomicTimesMarketsAdapter",  None,                         "T3_search",   "low"),
    "moneycontrol":        ("aegis.scrape.sources.moneycontrol",          "MoneycontrolAdapter",         "MoneycontrolConfig",         "T3_search",   "medium"),
    "yahoo_finance":       ("aegis.scrape.sources.yahoo_finance_rss",     "YahooFinanceRSSAdapter",      None,                         "T3_search",   "low"),
    "investing_com":       ("aegis.scrape.sources.investing_com_rss",     "InvestingComRSSAdapter",      None,                         "T3_search",   "low"),
    # Wave 3 — E-commerce
    "flipkart":            ("aegis.scrape.sources.flipkart",              "FlipkartAdapter",             "FlipkartConfig",             "T2_commerce", "high"),
    "meesho":              ("aegis.scrape.sources.meesho",                "MeeshoAdapter",               "MeeshoConfig",               "T2_commerce", "medium"),
    "myntra":              ("aegis.scrape.sources.myntra",                "MyntraAdapter",               "MyntraConfig",               "T2_commerce", "high"),
    "indiamart":           ("aegis.scrape.sources.indiamart",             "IndiaMartAdapter",            "IndiaMartConfig",            "T2_commerce", "medium"),
    "ajio":                ("aegis.scrape.sources.ajio",                  "AjioAdapter",                 "AjioConfig",                 "T2_commerce", "medium"),
    "nykaa":               ("aegis.scrape.sources.nykaa",                 "NykaaAdapter",                "NykaaConfig",                "T2_commerce", "medium"),
    "snapdeal":            ("aegis.scrape.sources.snapdeal",              "SnapdealAdapter",             "SnapdealConfig",             "T2_commerce", "medium"),
    "amazon_in":           ("aegis.scrape.sources.amazon_in",             "AmazonINAdapter",             "AmazonINConfig",             "T2_commerce", "high"),
    "amazon":              ("aegis.scrape.sources.amazon",                "AmazonAdapter",               "AmazonConfig",               "T3_search",   "medium"),
    "nse_bse":             ("aegis.scrape.sources.nse_bse",               "NSEBSEAdapter",               "NSEBSEConfig",               "T3_search",   "low"),
    "screener_in":         ("aegis.scrape.sources.screener_in",           "ScreenerInAdapter",           "ScreenerInConfig",           "T3_search",   "medium"),
    # Wave 4 — Tech
    "github_public":       ("aegis.scrape.sources.github_public",         "GitHubPublicAdapter",         "GitHubPublicConfig",         "T4_cultural", "low"),
    "npm_trends":          ("aegis.scrape.sources.npm_trends",            "NPMTrendsAdapter",            "NPMTrendsConfig",            "T4_cultural", "low"),
    "hacker-news":         ("aegis.scrape.sources.hacker_news",           "HackerNewsAdapter",           "HackerNewsConfig",           "T4_cultural", "low"),
    "github-trending":     ("aegis.scrape.sources.github_trending",       "GitHubTrendingAdapter",       "GitHubTrendingConfig",       "T4_cultural", "low"),
    "google-news":         ("aegis.scrape.sources.google_news_rss",       "GoogleNewsRSSAdapter",        "GoogleNewsRSSConfig",        "T3_search",   "low"),
    "bing-news":           ("aegis.scrape.sources.bing_news_rss",         "BingNewsRSSAdapter",          "BingNewsRSSConfig",          "T3_search",   "low"),
}


# ---------------------------------------------------------------------------
# Adapter function factory
# ---------------------------------------------------------------------------

def _make_adapter_fn(
    module_path: str,
    adapter_cls_name: str,
    config_cls_name: str | None,
) -> Any:
    """Return an async callable: (settings, http, limit) -> list[dict]."""

    async def _fn(_settings: Any, _http: Any, limit: int) -> list[dict[str, Any]]:
        from aegis.scrape.base import ScrapeContext

        try:
            mod = importlib.import_module(module_path)
            adapter_cls = getattr(mod, adapter_cls_name)
        except (ImportError, AttributeError):
            _log.debug("adapter_fn_import_error", module=module_path, adapter=adapter_cls_name)
            return []

        try:
            if config_cls_name is not None:
                cfg_cls = getattr(mod, config_cls_name)
                adapter = adapter_cls(cfg_cls())
            else:
                from aegis.scrape.sources._rss_base import RSSAdapterConfig
                adapter = adapter_cls(RSSAdapterConfig())
        except Exception:
            return []

        ctx = ScrapeContext()
        signals: list[dict[str, Any]] = []
        try:
            await adapter.setup(ctx)
            async for sig in adapter.run(limit=limit):
                if hasattr(sig, "model_dump"):
                    d: dict[str, Any] = sig.model_dump(mode="json")
                    # scraped_at lives in ScrapeProvenance, not at the signal root.
                    # schema_guard.REQUIRED_SIGNAL_FIELDS expects it top-level, so
                    # promote it — falling back to now() when provenance is absent.
                    if not d.get("scraped_at"):
                        prov = d.get("provenance") or {} # type: ignore
                        d["scraped_at"] = prov.get("scraped_at") or datetime.now(UTC).isoformat()
                    signals.append(d)
                elif isinstance(sig, dict):
                    signals.append(sig)  # type: ignore
        except Exception as exc:
            _log.debug("adapter_fn_error", adapter=adapter_cls_name, error=str(exc))
        finally:
            with contextlib.suppress(Exception):
                await adapter.teardown(ctx)
        return signals

    return _fn


def _build_all_agents() -> list[ScraperAgent]:
    """Construct one ScraperAgent for every registered adapter."""
    agents: list[ScraperAgent] = []
    for name, (mod, cls_name, cfg_name, tier, risk) in _REGISTRY.items():
        platform = name.replace("-", "_")
        caps = AdapterCapabilities(
            platform=platform,
            tier=tier,
            anti_bot_risk=risk,
            requires_flaresolverr=(risk == "high"),
        )
        agents.append(
            ScraperAgent(
                name=name,
                platform=platform,
                adapter_fn=_make_adapter_fn(mod, cls_name, cfg_name),
                capabilities=caps,
                health=AgentHealth.UNKNOWN,
            )
        )
    return agents


# ---------------------------------------------------------------------------
# Pulse classifier
# ---------------------------------------------------------------------------

def classify_pulse(signals: list[dict[str, Any]]) -> str:
    """Heuristic market pulse from signal sentiments. No LLM required."""
    if not signals:
        return "neutral"
    sentiments = [float(s["sentiment"]) for s in signals if s.get("sentiment") is not None]
    if not sentiments:
        return "neutral"
    avg = sum(sentiments) / len(sentiments)
    spread = max(sentiments) - min(sentiments) if len(sentiments) > 1 else 0.0
    if spread > 0.6 and len(signals) > 20:
        return "mixed"
    if avg > 0.3:
        return "bullish"
    if avg < -0.3:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Inner helpers (module-level for clean typing)
# ---------------------------------------------------------------------------

class _SwarmAnalyzer:
    """Extract cross-platform themes and hot categories from raw signals."""

    _STOPWORDS = frozenset([
        "the", "a", "an", "in", "on", "at", "for", "of", "and", "or",
        "to", "is", "are", "was", "be", "by", "with", "this", "that",
        "from", "as", "it", "its", "i", "we", "you", "he", "she", "they",
        "not", "no", "new", "up", "can", "will", "has", "have", "how",
        "what", "why", "when", "which", "who",
    ])

    def cross_platform_themes(
        self, signals: list[dict[str, Any]], min_platforms: int = 3
    ) -> list[str]:
        """Return words that appear across signals from at least min_platforms distinct platforms."""
        word_platforms: dict[str, set[str]] = {}
        for sig in signals:
            platform = sig.get("platform", "unknown")
            title = sig.get("title") or ""
            for word in title.lower().split():
                word = word.strip(".,!?;:\"'()")
                if len(word) > 3 and word not in self._STOPWORDS:
                    word_platforms.setdefault(word, set()).add(platform)

        cross = {w: len(ps) for w, ps in word_platforms.items() if len(ps) >= min_platforms}
        return [w for w, _ in sorted(cross.items(), key=lambda x: x[1], reverse=True)][:10]

    def hot_categories(self, signals: list[dict[str, Any]], top_n: int = 5) -> list[str]:
        """Top platforms by signal count (proxy for velocity)."""
        counts: Counter[str] = Counter(sig.get("platform", "unknown") for sig in signals)
        return [plat for plat, _ in counts.most_common(top_n)]


class _SwarmPersistence:
    def __init__(self, pg_pool: Any, redis: Any, settings: Any) -> None:
        self._pool = pg_pool
        self._redis = redis
        self._settings = settings

    async def save(self, result: SwarmResult, tenant_id: str) -> None:
        if not self._pool:
            return
        import json
        import uuid as _uuid
        try:
            async with self._pool.acquire(tenant_id=_uuid.UUID(tenant_id)) as conn:
                await conn.execute(
                    """
                    INSERT INTO swarm_results (
                        run_id, tenant_id, started_at, finished_at,
                        total_signals, unique_signals, dedup_removed,
                        by_platform, by_tier, batch_confidence,
                        market_pulse, conclusion,
                        cross_platform_themes, hot_categories, wave_stats
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7,
                        $8, $9, $10, $11, $12, $13, $14, $15
                    ) ON CONFLICT DO NOTHING
                    """,
                    _uuid.UUID(result.run_id),
                    _uuid.UUID(tenant_id),
                    result.started_at,
                    result.finished_at,
                    result.total_signals,
                    result.unique_signals,
                    result.dedup_removed,
                    json.dumps(result.by_platform),
                    json.dumps(result.by_tier),
                    result.batch_confidence,
                    result.market_pulse,
                    result.conclusion,
                    json.dumps(result.cross_platform_themes),
                    json.dumps(result.hot_categories),
                    json.dumps([ws.model_dump() for ws in result.wave_stats]),
                )
        except Exception as exc:
            _log.error("swarm.persist_failed", error=str(exc))

    async def publish_redis(self, result: SwarmResult) -> None:
        if not self._redis:
            return
        try:
            publish = getattr(getattr(self._settings, "scrape", self._settings), "swarm_publish_redis", True)
            if not publish:
                return
            await self._redis.xadd(
                "aegis:swarm:results",
                {"body": result.model_dump_json()},
            )
        except Exception as exc:
            _log.warning("swarm.redis_publish_failed", error=str(exc))


class _SwarmSynthesizer:
    def __init__(self) -> None:
        self._analyzer = _SwarmAnalyzer()

    def build(
        self,
        signals: list[dict[str, Any]],
        wave_stats: list[WaveStats],
        started_at: datetime,
        tier_map: dict[str, str] | None = None,
    ) -> SwarmResult:
        finished_at = datetime.now(UTC)

        by_platform: dict[str, int] = {}
        by_tier: dict[str, int] = {}
        for sig in signals:
            p = sig.get("platform", "unknown")
            by_platform[p] = by_platform.get(p, 0) + 1
            t = sig.get("_tier", tier_map.get(p, "T3_search") if tier_map else "T3_search")
            by_tier[t] = by_tier.get(t, 0) + 1

        themes = self._analyzer.cross_platform_themes(signals)
        hot = self._analyzer.hot_categories(signals)
        pulse = classify_pulse(signals)

        # Count signals by category bucket for the conclusion
        ecommerce_platforms = {
            "flipkart", "meesho", "myntra", "indiamart", "ajio",
            "nykaa", "snapdeal", "amazon_in", "amazon",
        }
        finance_platforms = {
            "moneycontrol", "economic_times", "nse_bse", "yahoo_finance",
            "reddit_finance", "screener_in", "investing_com",
        }
        news_platforms = {
            "techcrunch", "wired", "bbc_news", "reuters", "ndtv_profit",
            "mint", "business_standard",
        }
        n_ecommerce = sum(v for k, v in by_platform.items() if k in ecommerce_platforms)
        n_finance = sum(v for k, v in by_platform.items() if k in finance_platforms)
        n_news = sum(v for k, v in by_platform.items() if k in news_platforms)

        n_platforms = len(by_platform)
        top_platform = max(by_platform, key=lambda k: by_platform[k]) if by_platform else "n/a"
        theme_str = ", ".join(themes[:5]) if themes else "none detected"

        conclusion = (
            f"Market intelligence across {n_platforms} platforms shows {pulse} sentiment. "
            f"Top emerging themes: {theme_str}. Highest signal velocity on {top_platform}. "
            f"{n_ecommerce} e-commerce + {n_finance} finance + {n_news} news signals processed."
        )

        total = len(signals)
        return SwarmResult(
            started_at=started_at,
            finished_at=finished_at,
            total_signals=total,
            unique_signals=total,
            dedup_removed=0,
            by_platform=by_platform,
            by_tier=by_tier,
            wave_stats=wave_stats,
            cross_platform_themes=themes,
            hot_categories=hot,
            market_pulse=pulse,
            conclusion=conclusion,
        )


# ---------------------------------------------------------------------------
# SwarmOrchestrator
# ---------------------------------------------------------------------------

class SwarmOrchestrator:
    """Coordinate all waves, normalization, persistence, and result synthesis."""

    def __init__(
        self,
        settings: Any = None,
        pool: SwarmAgentPool | None = None,
        pg_pool: Any = None,
        redis: Any = None,
    ) -> None:
        if settings is None:
            from aegis.config import settings as _get_settings
            settings = _get_settings()
        self._settings = settings
        self.http: Any = None

        if pool is not None:
            self.pool = pool
        else:
            _scrape_cfg = getattr(settings, "scrape", settings)
            governor = ConcurrencyGovernor(
                max_concurrent=getattr(_scrape_cfg, "swarm_max_concurrent", 5),
                max_flaresolverr=getattr(_scrape_cfg, "swarm_flaresolverr_max_concurrent", 2),
                jitter_max_ms=getattr(_scrape_cfg, "swarm_jitter_max_ms", 500),
            )
            self.pool = SwarmAgentPool(
                agents=_build_all_agents(),
                governor=governor,
            )
        self._persistence = _SwarmPersistence(pg_pool, redis, settings)
        self._synthesizer = _SwarmSynthesizer()
        # Build platform → tier lookup for synthesis
        self._tier_map: dict[str, str] = {
            name.replace("-", "_"): entry[3]
            for name, entry in _REGISTRY.items()
        }

    async def run_all_waves(
        self,
        limit: int = 50,
        dry_run: bool = False,
    ) -> SwarmResult:
        """Run all four waves sequentially, collect signals, normalize, synthesize."""
        started_at = datetime.now(UTC)
        all_signals: list[dict[str, Any]] = []
        wave_stats: list[WaveStats] = []
        seen_urls: set[str] = set()

        waves = [WAVE_1_SOCIAL, WAVE_2_NEWS, WAVE_3_ECOMMERCE, WAVE_4_TECH]
        for wave_num, agent_names in enumerate(waves, start=1):
            wave_start = time.monotonic()
            runs: list[AdapterRun] = []
            try:
                runs = await self.pool.run_wave(agent_names, self._settings, self.http, limit)
            except Exception as exc:
                _log.error("swarm.wave_error", wave=wave_num, error=str(exc))

            new_signals: list[dict[str, Any]] = []
            failures = sum(1 for r in runs if not r.success)

            for run in runs:
                if not run.signals:
                    continue
                validated = validate_batch(run.signals, run.platform)
                tier = self._tier_map.get(run.platform, "T3_search")
                for sig in validated:
                    url = str(sig.get("url") or "")
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    enriched = dict(sig)
                    enriched["_tier"] = tier
                    new_signals.append(enriched)

            all_signals.extend(new_signals)
            wave_stats.append(
                WaveStats(
                    wave_number=wave_num,
                    agents_run=len(runs),
                    signals_collected=len(new_signals),
                    duration_ms=(time.monotonic() - wave_start) * 1000,
                    failures=failures,
                )
            )
            _log.info(
                "swarm.wave_done",
                wave=wave_num,
                agents=len(runs),
                new_signals=len(new_signals),
                failures=failures,
            )

        # Normalize scores across platforms
        all_signals = normalize_swarm_batch(all_signals)

        result = self._synthesizer.build(all_signals, wave_stats, started_at, self._tier_map)

        if not dry_run:
            cfg = self._settings
            tenant_id = getattr(cfg, "default_tenant_id", "00000000-0000-0000-0000-000000000001")
            await self._persistence.save(result, tenant_id)
            await self._persistence.publish_redis(result)

        _log.info(
            "swarm.complete",
            run_id=result.run_id,
            total_signals=result.total_signals,
            platforms=len(result.by_platform),
            pulse=result.market_pulse,
            duration_s=round((result.finished_at - result.started_at).total_seconds(), 1),
        )
        return result


__all__ = [
    "WAVE_1_SOCIAL",
    "WAVE_2_NEWS",
    "WAVE_3_ECOMMERCE",
    "WAVE_4_TECH",
    "ALL_WAVE_NAMES",
    "classify_pulse",
    "SwarmOrchestrator",
]
