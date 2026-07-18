"""
AEGIS Product Intelligence Engine — autonomous market-operator analysis.

Given any consumer query ("men's trimmer", "wireless earbuds"), this engine:

  1. Routes the query to the right e-commerce + trend adapters (TopicClassifier
     + AdapterRouter) — no fixed default topic, the operator decides per query.
  2. Harvests live product listings across every reachable marketplace.
  3. Extracts structured product records (price, rating, reviews, discount, brand).
  4. Synthesizes a competitive market report:
       - price distribution + budget/mid/premium bands
       - brand / competitor breakdown (share, avg price, avg rating, reviews)
       - top products by rating, popularity, value, and discount
       - cross-platform price spread (arbitrage gaps for the same product)
       - momentum (review-volume + recency proxy)
       - concrete picks: budget / best-value / premium
       - plain-English market summary + recommended actions

It is the difference between "we scraped some titles" and "here is how this
market actually looks and what to do about it." Pure-Python, no heavy deps,
degrades gracefully when fields are missing.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger("aegis.intelligence.product_intel")

# Marketplaces that implement real KEYWORD SEARCH — they return products that
# actually match the query (with price/rating/image). Only these are used for the
# market report, so results stay relevant instead of generic "trending" items.
# Extend this as more adapters gain a query-search path (see snapdeal.py).
_SEARCH_ADAPTERS = (
    "snapdeal", "flipkart", "amazon_in", "ajio", "indiamart",
    "meesho", "myntra", "nykaa",
)
# Trending-only marketplaces (no per-query search yet) — intentionally excluded
# from the market harvest to avoid polluting results with irrelevant listings.
_TRENDING_ONLY = (
    "amazon", "amazon_in", "flipkart", "meesho", "myntra", "nykaa", "ajio", "indiamart",
)

# Keyless demand-momentum sources — free RSS / public JSON that are NOT WAF-gated.
# These read public *interest* in a query (news volume, social chatter, search
# trend) so the analyst has a demand signal even when every marketplace is blocked.
_DEMAND_ADAPTERS = (
    "google-news", "bing-news", "reddit-rss", "youtube_rss", "google_trends_india",
)

# Evidence floors: below these the report is honest about being thin instead of
# projecting confident picks/verdicts from one or two listings.
_MIN_PRODUCTS_FLOOR = 3
_MIN_PRICED_FLOOR = 2

# Display symbol for the base comparison currency (everything is normalized to USD).
_USD = "USD"

# Common stop-words so brand inference doesn't pick generic leading words.
_BRAND_STOP = frozenset({
    "the", "best", "new", "men", "mens", "women", "womens", "for", "with",
    "pro", "max", "plus", "buy", "online", "set", "pack", "premium", "original",
    "combo", "kit", "rechargeable", "cordless", "wireless", "smart", "professional",
    # generic product/category words that must not be mistaken for a brand
    "air", "hair", "beard", "fryer", "trimmer", "shaver", "nose", "ear", "oil",
    "electric", "portable", "mini", "usb", "multi", "super", "deal", "deals",
    "black", "white", "blue", "gold", "silver", "red", "green", "and",
})

# Canonical consumer brands AEGIS sees most across Indian + global marketplaces.
# Used to fold typos / casing / sub-brands into one competitor row. Extend freely.
_KNOWN_BRANDS = frozenset({
    "philips", "nova", "mi", "xiaomi", "syska", "vega", "havells", "wahl",
    "panasonic", "braun", "kemei", "agaro", "bombay shaving company", "beardo",
    "boat", "boult", "noise", "jbl", "sony", "realme", "oneplus", "samsung",
    "apple", "oppo", "vivo", "nothing", "ptron", "zebronics", "portronics",
    "nike", "adidas", "puma", "reebok", "campus", "bata", "redtape", "woodland",
    "lakme", "maybelline", "loreal", "mamaearth", "wow", "nykaa", "plum",
    "lg", "whirlpool", "bosch", "prestige", "bajaj", "usha", "crompton",
    "instant", "inalsa", "pigeon", "morphy richards", "kent",
})
# Hand-mapped aliases → canonical brand.
_BRAND_ALIASES = {
    "xiaomi": "mi", "redmi": "mi", "loreal": "loreal", "l'oreal": "loreal",
    "bombay": "bombay shaving company", "bsc": "bombay shaving company",
}


@dataclass
class ProductRecord:
    """One structured product listing extracted from a harvested signal."""

    platform: str
    title: str
    brand: str
    price: float | None
    currency: str
    rating: float | None
    review_count: int | None
    discount_pct: float | None
    image_url: str | None
    url: str | None
    confidence: float
    price_usd: float | None = None
    """Price converted to USD — the single base currency for ALL comparison /
    arbitrage / band math. ``None`` until FX normalization runs or when the
    rate is unavailable. Cross-platform price math MUST use this, never the raw
    ``price`` (which may be in a different currency per marketplace)."""

    def value_score(self) -> float:
        """Higher = more bang per USD. Needs USD price + rating to rank."""
        p = self.price_usd
        if not p or p <= 0 or self.rating is None:
            return 0.0
        reviews = math.log1p(self.review_count or 0)
        return round((self.rating * (1 + reviews)) / p, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "title": self.title[:160],
            "brand": self.brand,
            "price": self.price,
            "currency": self.currency,
            "price_usd": self.price_usd,
            "rating": self.rating,
            "review_count": self.review_count,
            "discount_pct": self.discount_pct,
            "image_url": self.image_url,
            "url": self.url,
            "value_score": self.value_score(),
        }


@dataclass
class MarketReport:
    """Structured competitive-market view of a single query."""

    query: str
    topic_type: str
    generated_at: datetime
    status: str  # "OK" | "INSUFFICIENT_EVIDENCE" | "NO_DATA"
    status_reason: str
    product_count: int
    platforms: list[str]
    sources_consulted: list[str]
    price_summary: dict[str, Any]
    price_bands: dict[str, Any]
    competitors: list[dict[str, Any]]
    top_rated: list[dict[str, Any]]
    most_popular: list[dict[str, Any]]
    best_value: list[dict[str, Any]]
    biggest_discounts: list[dict[str, Any]]
    arbitrage: list[dict[str, Any]]
    momentum: dict[str, Any]
    picks: dict[str, Any]
    executive_summary: str
    recommended_actions: list[str]
    data_quality: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    """Fact statements each backed by counts, sources and concrete examples."""
    llm_narrative: str = ""
    """LLM-written operator verdict (heuristic numbers stay authoritative)."""
    gated_pick: dict[str, Any] = field(default_factory=dict)
    """Top pick run through compliance + geo + capital advisory gates."""
    demand: dict[str, Any] = field(default_factory=dict)
    """Keyless demand-momentum read (news/reddit/trends/gdelt). Independent of
    marketplace listings, so it survives marketplace WAF blocks."""
    opportunity: dict[str, Any] = field(default_factory=dict)
    """Supply/demand-gap signal: high demand against thin supply / fragmented
    competition = an opening. Strategist-style verdict (advisory)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "topic_type": self.topic_type,
            "generated_at": self.generated_at.isoformat(),
            "status": self.status,
            "status_reason": self.status_reason,
            "product_count": self.product_count,
            "platforms": self.platforms,
            "sources_consulted": self.sources_consulted,
            "price_summary": self.price_summary,
            "price_bands": self.price_bands,
            "competitors": self.competitors,
            "top_rated": self.top_rated,
            "most_popular": self.most_popular,
            "best_value": self.best_value,
            "biggest_discounts": self.biggest_discounts,
            "arbitrage": self.arbitrage,
            "momentum": self.momentum,
            "picks": self.picks,
            "executive_summary": self.executive_summary,
            "recommended_actions": self.recommended_actions,
            "data_quality": self.data_quality,
            "evidence": self.evidence,
            "llm_narrative": self.llm_narrative,
            "gated_pick": self.gated_pick,
            "demand": self.demand,
            "opportunity": self.opportunity,
        }


class ProductIntelligenceEngine:
    """Autonomous product-market analyst.

    Usage::

        engine = ProductIntelligenceEngine(pool=pg_pool, redis=redis_client)
        report = await engine.analyze("men's trimmer", depth="deep")
    """

    def __init__(self, pool: Any | None = None, redis: Any | None = None) -> None:
        self._pool = pool
        self._redis = redis

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def analyze(
        self,
        query: str,
        *,
        depth: str = "standard",  # "surface" | "standard" | "deep"
        max_products: int = 240,
        use_llm: bool = True,
        gate: bool = False,
    ) -> MarketReport:
        _log.info("product_intel.start", query=query, depth=depth)

        from aegis.scrape.adapter_router import AdapterRouter
        from aegis.scrape.topic_classifier import TopicClassifier

        topic_type = TopicClassifier().classify(query)
        adapters = self._select_adapters(query, AdapterRouter(redis=self._redis), depth)
        # Marketplace listings and keyless demand momentum are independent reads;
        # harvest them concurrently so the demand signal survives marketplace WAF
        # blocks and adds zero latency to the critical path.
        signals, demand = await asyncio.gather(
            self._harvest(query, adapters, max_products),
            self._harvest_demand(query),
        )
        products = self._extract_products(signals, query)
        # Normalize every price to USD BEFORE any comparison/arbitrage/band math.
        # Mixed-currency listings (₹ vs $) would otherwise produce false arbitrage.
        await self._normalize_currencies(products)

        report = self._synthesize(query, topic_type.value, signals, products)
        report.demand = demand
        report.opportunity = self._opportunity(report)
        self._apply_status(report)

        # Verdict policy is status-gated. An LLM is only asked to commit to a
        # call when there is enough VERIFIED evidence; otherwise the verdict is a
        # deterministic INSUFFICIENT_EVIDENCE / NO_DATA — never an LLM guess.
        if report.status == "OK":
            if gate or use_llm:
                gate_task = self._gate_pick(report) if gate else self._noop_dict()
                llm_task = self._llm_narrative(report) if use_llm else self._noop_str()
                gated, narrative = await asyncio.gather(gate_task, llm_task)
                report.gated_pick = gated
                report.llm_narrative = narrative
        else:
            # Below the evidence floor / no data — never ask the LLM to commit to a
            # call; emit a deterministic, honest verdict instead of a guess.
            report.llm_narrative = (
                f"RECOMMENDATION: INSUFFICIENT_EVIDENCE\nWHY: {report.status_reason}"
            )

        _log.info(
            "product_intel.complete",
            query=query,
            products=report.product_count,
            platforms=len(report.platforms),
            competitors=len(report.competitors),
            llm=bool(report.llm_narrative),
            gated=bool(report.gated_pick),
        )
        return report

    @staticmethod
    async def _noop_dict() -> dict[str, Any]:
        return {}

    @staticmethod
    async def _noop_str() -> str:
        return ""

    # ------------------------------------------------------------------
    # Currency normalization — convert every price to USD before any math
    # ------------------------------------------------------------------

    async def _normalize_currencies(self, products: list[ProductRecord]) -> None:
        """Set ``price_usd`` on every priced record using live ECB/Frankfurter FX.

        All downstream comparison/arbitrage/band math operates on ``price_usd``
        so listings in different currencies are never compared raw (the old
        false-arbitrage bug). Never raises. When FX is genuinely unreachable AND
        every listing shares one currency, we fall back to the raw price (a
        monotonic transform — rankings and percentage gaps are identical), so
        the analysis still works for the common single-currency case.
        """
        from decimal import Decimal

        priced = [p for p in products if p.price and p.price > 0]
        if not priced:
            return
        currencies = {(p.currency or "INR").upper() for p in priced}
        rates: dict[str, float | None] = {}
        try:
            from aegis.geo.fx import FXRateFetcher

            fx = FXRateFetcher()
            for cur in currencies:
                if cur == _USD:
                    rates[cur] = 1.0
                    continue
                try:
                    rates[cur] = float(await fx.to_usd(Decimal("1"), cur))
                except Exception:  # pragma: no cover - per-currency defensive
                    rates[cur] = None
        except Exception as exc:
            _log.warning("product_intel.fx_unavailable", error=str(exc)[:200])

        single_currency = len(currencies) == 1
        for p in priced:
            cur = (p.currency or "INR").upper()
            rate = rates.get(cur)
            if rate:
                p.price_usd = round(p.price * rate, 4)  # type: ignore[operator]
            elif single_currency:
                # No FX but only one currency — raw price is a valid comparison key.
                p.price_usd = p.price
        converted = sum(1 for p in priced if p.price_usd is not None)
        _log.info(
            "product_intel.fx_normalized",
            currencies=sorted(currencies),
            priced=len(priced),
            converted=converted,
        )

    # ------------------------------------------------------------------
    # Data-status contract — never project confidence from empty/thin data
    # ------------------------------------------------------------------

    def _apply_status(self, report: MarketReport) -> None:
        """Stamp NO_DATA / INSUFFICIENT_EVIDENCE / OK on the report.

        NO_DATA  — no listings AND no demand mentions (all sources blocked/empty).
        INSUFFICIENT_EVIDENCE — some signal but supply read is below the floor.
        OK — enough evidence to stand behind picks and a verdict.
        """
        demand_mentions = int(report.demand.get("mention_count") or 0)
        priced = (
            report.price_summary.get("count", 0)
            if report.price_summary.get("available")
            else 0
        )
        if report.product_count == 0 and demand_mentions == 0:
            report.status = "NO_DATA"
            report.status_reason = (
                f"No marketplace listings and no demand mentions could be read for "
                f"'{report.query}'. Every consulted source returned empty or was "
                f"blocked — no fabricated picks or verdict are produced."
            )
            # Honest, not synthesized: the summary IS the status reason.
            report.executive_summary = report.status_reason
            report.picks = {}
            report.recommended_actions = [
                "Retry later, broaden the query, or enable residential proxies — "
                "the marketplace/news sources were unreachable for this query."
            ]
            return
        if report.product_count < _MIN_PRODUCTS_FLOOR and priced < _MIN_PRICED_FLOOR:
            report.status = "INSUFFICIENT_EVIDENCE"
            report.status_reason = (
                f"Only {report.product_count} listing(s) ({priced} priced) captured — "
                f"below the evidence floor of {_MIN_PRODUCTS_FLOOR} listings / "
                f"{_MIN_PRICED_FLOOR} priced. Demand signal "
                f"({demand_mentions} mentions) is reported, but supply-side picks are "
                f"not statistically meaningful yet."
            )
            return
        report.status = "OK"
        report.status_reason = ""

    # ------------------------------------------------------------------
    # LLM enhancement (heuristic-first: text only, never overrides numbers)
    # ------------------------------------------------------------------

    async def _llm_narrative(self, report: MarketReport) -> str:
        """Ask the LLM gateway (council-aware) for a concise operator verdict.

        Returns ``""`` on any failure so the report is always usable without
        an LLM. The structured numbers are computed deterministically; the LLM
        only phrases the opportunity.
        """
        try:
            from aegis.llm.bridge.agents_bridge import complete_for_agent
        except Exception:
            return ""
        comp = ", ".join(
            f"{c['brand']} ({c['share_pct']}%, ★{c['avg_rating']}, {c['listings']} listings)"
            for c in report.competitors[:5]
        )
        picks = report.picks or {}
        facts = {
            "query": report.query,
            "products": report.product_count,
            "marketplaces": report.platforms,
            "price": report.price_summary,
            "momentum": report.momentum,
            "top_competitors": comp,
            "best_value": (picks.get("best_value") or {}).get("title"),
            "budget_pick": (picks.get("budget") or {}).get("title"),
            "premium_pick": (picks.get("premium") or {}).get("title"),
            "arbitrage_gaps": report.arbitrage[:3],
            "demand_momentum": {
                k: report.demand.get(k)
                for k in ("demand_score", "level", "mention_count", "sources")
            } if report.demand else None,
            "demand_headlines": (report.demand.get("recent_headlines") or [])[:5],
            "opportunity_gap": report.opportunity or None,
            "evidence": [e["claim"] for e in report.evidence],
            "gated_pick": report.gated_pick or None,
        }
        system = (
            "You are AEGIS, a senior e-commerce market operator. You are given "
            "VERIFIED marketplace data and a list of evidence statements. Write a "
            "sharp verdict for a seller deciding whether to enter this market. Rules: "
            "(1) every claim you make must be grounded in the supplied numbers or "
            "evidence — quote the figure. (2) Never invent products, prices, or brands. "
            "(3) If a compliance gate is present, factor it in. (4) 5-7 sentences, then "
            "a line 'RECOMMENDATION: ENTER|WATCH|AVOID' and a one-line 'WHY:' citing the "
            "single strongest piece of evidence."
        )
        user = (
            "Verified market data (JSON):\n"
            f"{json.dumps(facts, default=str)}\n\n"
            "Write the evidence-grounded operator verdict."
        )
        try:
            # Hard cap so a slow/hanging provider (e.g. Ollama) can never blow the
            # request budget — the report stays fully usable without the verdict.
            text = await asyncio.wait_for(
                complete_for_agent(
                    "market_analyst",
                    [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
                    temperature=0.3,
                    max_tokens=400,
                ),
                timeout=45,
            )
            return self._ground_narrative((text or "").strip(), report)
        except Exception as exc:
            _log.debug("product_intel.llm_failed", query=report.query, error=str(exc)[:200])
            return ""

    @staticmethod
    def _ground_narrative(text: str, report: MarketReport) -> str:
        """Flag any figure in the LLM narrative not present in the verified facts.

        The model is instructed to only cite supplied numbers; this is the
        enforcement step. We collect every numeric value the report actually
        knows (counts, prices, shares, ratings, gaps, momentum, demand) and any
        figure >= 10 in the narrative that matches none of them (within 1%) is
        appended as an explicit GROUNDING WARNING rather than silently trusted.
        """
        if not text:
            return text
        allowed: set[float] = set()

        def _add(v: Any) -> None:
            f = ProductIntelligenceEngine._coerce_float(v)
            if f is not None:
                allowed.add(round(f, 2))

        ps = report.price_summary
        if ps.get("available"):
            for k in ("min", "max", "median", "mean", "spread_pct", "count"):
                _add(ps.get(k))
        _add(report.product_count)
        _add(len(report.platforms))
        for c in report.competitors:
            for key in ("share_pct", "avg_rating", "listings", "total_reviews", "avg_price_usd"):
                _add(c.get(key))
        m = report.momentum
        for key in ("score", "total_reviews", "avg_rating"):
            _add(m.get(key))
        for a in report.arbitrage:
            _add(a.get("gap_pct"))
        d = report.demand or {}
        for key in ("demand_score", "mention_count", "source_count"):
            _add(d.get(key))

        nums = re.findall(r"\d+(?:\.\d+)?", text)
        ungrounded = []
        for raw in nums:
            val = float(raw)
            if val < 10:  # ignore small ints (sentence counts, ratings <10, etc.)
                continue
            if not any(abs(val - a) <= max(0.01 * a, 0.01) for a in allowed):
                ungrounded.append(raw)
        if ungrounded:
            _log.warning(
                "product_intel.narrative_ungrounded",
                query=report.query,
                figures=ungrounded[:10],
            )
            text += (
                "\n\n⚠ GROUNDING WARNING: the figures "
                f"{', '.join(dict.fromkeys(ungrounded))} are not in the verified "
                "data and may be hallucinated — disregard them."
            )
        return text

    # ------------------------------------------------------------------
    # Gate the best pick: compliance + geo + capital advisory
    # ------------------------------------------------------------------

    async def _gate_pick(self, report: MarketReport) -> dict[str, Any]:
        picks = report.picks or {}
        pick = picks.get("best_value") or picks.get("budget")
        if not pick:
            return {}
        title = str(pick.get("title") or report.query)
        out: dict[str, Any] = {"product": title, "platform": pick.get("platform")}

        # Compliance gate (Phase 8) — real assessment, best-effort.
        try:
            from aegis.compliance.engine import ComplianceEngine
            from aegis.compliance.schemas import ComplianceRequest

            assessment = await asyncio.wait_for(
                ComplianceEngine().assess(
                    ComplianceRequest(
                        product_sku="MARKET-PICK",
                        product_title=title,
                        category=report.topic_type or "general",
                        origin_country="CN",
                        destination_country="IN",
                        price_usd=pick.get("price"),
                    )
                ),
                timeout=25,
            )
            out["compliance"] = {
                "recommendation": assessment.recommendation.value,
                "risk_score": round(assessment.overall_risk_score, 3),
            }
        except Exception as exc:
            _log.debug("product_intel.gate_compliance_failed", error=str(exc)[:200])
            out["compliance"] = {"recommendation": "UNKNOWN", "error": "gate unavailable"}

        # Geo arbitrage gate (Phase 7) — best route, best-effort (no API key needed).
        try:
            from aegis.geo.arbitrage import CrossMarketAnalyzer

            geo = await asyncio.wait_for(
                CrossMarketAnalyzer().find_opportunities(
                    "MARKET-PICK", title, category=report.topic_type or "general", top_n=1
                ),
                timeout=15,
            )
            ops = getattr(geo, "opportunities", None) or []
            if ops:
                o = ops[0]
                out["geo"] = {
                    "route": f"{getattr(o, 'origin_region', '?')}→{getattr(o, 'destination_region', '?')}",
                    "gross_margin_pct": round(float(getattr(o, "gross_margin_pct", 0)), 1),
                }
        except Exception as exc:
            _log.debug("product_intel.gate_geo_failed", error=str(exc)[:200])

        # Capital advisory (Phase 6 doctrine) — fractional-Kelly sizing, advisory only.
        mo = report.momentum or {}
        edge = float(mo.get("score", 0.0))
        comp_ok = out.get("compliance", {}).get("recommendation") == "PROCEED"
        kelly = round(0.25 * edge, 3)  # 0.25× fractional Kelly, scaled by momentum
        out["capital"] = {
            "mode": "advisory",
            "kelly_fraction": kelly if comp_ok else 0.0,
            "rationale": (
                "Compliance clear — advisory size scales with momentum."
                if comp_ok else
                "Held: compliance not clear or unavailable. Zero capital advised."
            ),
        }
        return out

    # ------------------------------------------------------------------
    # Adapter selection — query-driven, no hardcoded default topic
    # ------------------------------------------------------------------

    def _select_adapters(self, query: str, router: Any, depth: str) -> list[str]:
        # Only keyword-search-capable marketplaces — keeps the harvest relevant to
        # the query. Router suggestions are intersected with that capability so we
        # never fall back to trending/offers pages that ignore the query.
        routed = {
            r.adapter_name
            for r in router.route(query, top_n=14)
            if getattr(r, "credentials_available", True)
            or not getattr(r, "requires_credentials", False)
        }
        # Always run every search-capable marketplace for true cross-platform
        # comparison; surface the router-preferred ones first.
        preferred = [a for a in _SEARCH_ADAPTERS if a in routed]
        rest = [a for a in _SEARCH_ADAPTERS if a not in preferred]
        return preferred + rest

    # ------------------------------------------------------------------
    # Harvest
    # ------------------------------------------------------------------

    async def _harvest(
        self, query: str, adapters: list[str], max_products: int
    ) -> list[Any]:
        from aegis.scrape.topic import scrape_topic

        limit_per_source = max(8, max_products // max(len(adapters), 1))
        harvest = await scrape_topic(
            query,
            pool=self._pool,
            tenant_id=self._default_tenant_id() if self._pool is not None else None,
            limit_per_source=limit_per_source,
            dry_run=self._pool is None,
            adapter_override=adapters or None,
        )
        return list(harvest.signals)

    async def _harvest_demand(self, query: str) -> dict[str, Any]:
        """Read keyless demand momentum (news/social/trends) for the query.

        Runs independently of the marketplace harvest so a fully WAF-blocked
        marketplace set still yields a demand read. Never raises — returns an
        empty snapshot on any failure.
        """
        from aegis.scrape.topic import scrape_topic

        try:
            harvest = await scrape_topic(
                query,
                pool=None,
                tenant_id=None,
                limit_per_source=15,
                dry_run=True,
                adapter_override=list(_DEMAND_ADAPTERS),
            )
            return self._demand_snapshot(query, list(harvest.signals))
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("product_intel.demand_harvest_failed", error=str(exc))
            return {}

    def _demand_snapshot(self, query: str, signals: list[Any]) -> dict[str, Any]:
        """Aggregate demand signals into a compact, evidence-backed snapshot."""
        platforms: dict[str, int] = {}
        headlines: list[str] = []
        tokens = self._query_tokens(query)
        relevant = 0
        for s in signals:
            title = str(getattr(s, "title", "") or (s.get("title") if isinstance(s, dict) else ""))
            plat = (
                self._platform_str(getattr(s, "platform", ""))
                if not isinstance(s, dict)
                else str(s.get("platform", ""))
            )
            if not title:
                continue
            on_topic = not tokens or self._title_matches(title, tokens)
            if not on_topic:
                continue
            relevant += 1
            if plat:
                platforms[plat] = platforms.get(plat, 0) + 1
            if len(headlines) < 12:
                headlines.append(title[:140])
        # Demand score: normalized mention volume × source breadth (0-1).
        volume = min(1.0, relevant / 40.0)
        breadth = min(1.0, len(platforms) / 5.0)
        demand_score = round(0.6 * volume + 0.4 * breadth, 3)
        level = (
            "hot" if demand_score >= 0.66
            else "warm" if demand_score >= 0.33
            else "cool"
        )
        return {
            "demand_score": demand_score,
            "level": level,
            "mention_count": relevant,
            "source_count": len(platforms),
            "sources": sorted(platforms),
            "by_source": platforms,
            "recent_headlines": headlines,
        }

    @staticmethod
    def _default_tenant_id() -> Any:
        from uuid import UUID

        from aegis.config import settings

        return UUID(settings().default_tenant_id)

    # ------------------------------------------------------------------
    # Extraction — signal → ProductRecord
    # ------------------------------------------------------------------

    def _extract_products(
        self, signals: list[Any], query: str = ""
    ) -> list[ProductRecord]:
        tokens = self._query_tokens(query)
        out: list[ProductRecord] = []
        dropped_irrelevant = 0
        for s in signals:
            rec = self._to_product(s)
            # Keep only entries that look like actual products (price or rating).
            if not rec or (rec.price is None and rec.rating is None):
                continue
            # Relevance gate: a blocked/blind search often returns a marketplace's
            # generic best-sellers that ignore the query. Require the listing title
            # to overlap the query so the analyst reasons about the RIGHT product.
            if tokens and not self._title_matches(rec.title, tokens):
                dropped_irrelevant += 1
                continue
            out.append(rec)
        if dropped_irrelevant:
            _log.info(
                "product_intel.relevance_filtered",
                query=query,
                kept=len(out),
                dropped_irrelevant=dropped_irrelevant,
            )
        return out

    def _opportunity(self, report: MarketReport) -> dict[str, Any]:
        """Supply/demand-gap verdict — the strategist read.

        Combines the keyless demand score (interest) with marketplace supply
        depth and competitive concentration. High demand + thin/fragmented
        supply = an opening; high demand + saturated supply = a fight.
        Purely advisory; computed from the verified numbers only.
        """
        demand_score = float(report.demand.get("demand_score") or 0.0)
        n_products = report.product_count
        competitors = report.competitors or []
        # Supply saturation: more listings = harder to stand out (log-scaled).
        supply_saturation = min(1.0, n_products / 60.0)
        # Concentration: top competitor's share. Low share = fragmented = enterable.
        top_share = (competitors[0].get("share_pct", 0) / 100.0) if competitors else 0.0
        fragmentation = 1.0 - min(1.0, top_share)
        # Gap score: demand pulling up, saturation pulling down, fragmentation up.
        gap = demand_score * (0.6 * (1.0 - supply_saturation) + 0.4 * fragmentation)
        gap = round(gap, 3)
        if demand_score < 0.15 and n_products == 0:
            verdict, rationale = "INSUFFICIENT_DATA", (
                "Neither demand signal nor marketplace supply could be read "
                "(likely all sources blocked) — cannot judge the gap."
            )
        elif gap >= 0.45:
            verdict, rationale = "OPENING", (
                f"Demand is {report.demand.get('level', 'present')} "
                f"({demand_score}) while supply is thin/fragmented "
                f"({n_products} listings, top player {int(top_share*100)}% share)."
            )
        elif gap >= 0.22:
            verdict, rationale = "CONTESTED", (
                f"Real demand ({demand_score}) but meaningful existing supply "
                f"({n_products} listings) — differentiation required."
            )
        else:
            verdict, rationale = "SATURATED_OR_COLD", (
                f"Either weak demand ({demand_score}) or heavy supply "
                f"({n_products} listings, top player {int(top_share*100)}%)."
            )
        return {
            "gap_score": gap,
            "verdict": verdict,
            "rationale": rationale,
            "demand_score": demand_score,
            "supply_saturation": round(supply_saturation, 3),
            "competitor_fragmentation": round(fragmentation, 3),
        }

    @staticmethod
    def _query_tokens(query: str) -> set[str]:
        """Significant lowercase query tokens (length > 2, minus filler words)."""
        stop = {"the", "for", "and", "with", "best", "buy", "online", "new", "top"}
        return {
            t for t in re.split(r"\W+", query.lower())
            if len(t) > 2 and t not in stop
        }

    @staticmethod
    def _title_matches(title: str, tokens: set[str]) -> bool:
        """True if any significant query token appears in the listing title.

        Uses a prefix match (>=4 chars) so 'earbuds' matches 'earbud' / 'earbuds'
        without pulling in unrelated listings.
        """
        low = title.lower()
        for tok in tokens:
            if tok in low:
                return True
            if len(tok) >= 5 and tok[:4] in low:
                return True
        return False

    def _to_product(self, s: Any) -> ProductRecord | None:
        if isinstance(s, dict):
            ps = s.get("platform_specific") or s.get("raw_json") or {}
            platform = str(s.get("platform", ""))
            title = str(s.get("title") or "")
            url = s.get("url")
            price = self._coerce_float(s.get("price_amount"))
            conf = self._coerce_float(s.get("source_confidence")) or 0.5
        else:
            ps = getattr(s, "platform_specific", {}) or {}
            platform = self._platform_str(getattr(s, "platform", ""))
            title = str(getattr(s, "title", "") or "")
            url = getattr(s, "url", None)
            price = self._price_from_model(s)
            conf = self._conf_from_model(s)
        if not title:
            return None
        if price is None:
            price = self._coerce_float(ps.get("disc_price") or ps.get("price_inr") or ps.get("price"))
        return ProductRecord(
            platform=platform,
            title=title,
            brand=self._infer_brand(title, ps),
            price=price,
            currency=str(ps.get("currency") or "INR"),
            rating=self._coerce_float(ps.get("rating")),
            review_count=self._coerce_int(ps.get("review_count")),
            discount_pct=self._coerce_float(ps.get("discount_pct")),
            image_url=ps.get("image_url"),
            url=str(url) if url else None,
            confidence=conf,
        )

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    def _synthesize(
        self,
        query: str,
        topic_type: str,
        signals: list[Any],
        products: list[ProductRecord],
    ) -> MarketReport:
        platforms = sorted({p.platform for p in products if p.platform})
        sources = sorted({self._platform_str(getattr(s, "platform", "")) or str(s) for s in signals} - {""})
        # All price math is in USD (price_usd set by _normalize_currencies).
        priced = [p for p in products if p.price_usd and p.price_usd > 0]
        prices = [p.price_usd for p in priced if p.price_usd is not None]

        price_summary = self._price_summary(prices)
        price_bands = self._price_bands(priced)
        competitors = self._competitor_table(products)
        top_rated = self._top_rated(products)
        most_popular = self._most_popular(products)
        best_value = self._best_value(products)
        biggest_discounts = self._biggest_discounts(products)
        arbitrage = self._arbitrage(priced)
        momentum = self._momentum(products)
        picks = self._picks(priced)
        data_quality = self._data_quality(products)

        return MarketReport(
            query=query,
            topic_type=topic_type,
            generated_at=datetime.now(UTC),
            status="OK",
            status_reason="",
            product_count=len(products),
            platforms=platforms,
            sources_consulted=sources,
            price_summary=price_summary,
            price_bands=price_bands,
            competitors=competitors,
            top_rated=top_rated,
            most_popular=most_popular,
            best_value=best_value,
            biggest_discounts=biggest_discounts,
            arbitrage=arbitrage,
            momentum=momentum,
            picks=picks,
            executive_summary=self._summary(query, products, competitors, price_summary, arbitrage),
            recommended_actions=self._actions(products, competitors, arbitrage, momentum),
            data_quality=data_quality,
            evidence=self._build_evidence(
                products, platforms, price_summary, competitors, arbitrage, momentum, best_value
            ),
        )

    # ------------------------------------------------------------------
    # Evidence — every headline fact backed by counts, sources, examples
    # ------------------------------------------------------------------

    def _build_evidence(
        self,
        products: list[ProductRecord],
        platforms: list[str],
        price_summary: dict[str, Any],
        competitors: list[dict[str, Any]],
        arbitrage: list[dict[str, Any]],
        momentum: dict[str, Any],
        best_value: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build fact statements, each tied to the data that proves it.

        Every claim carries a provenance ``band``:
          VERIFIED   — derived directly from scraped marketplace listings.
          ESTIMATED  — a model/proxy computed from verified inputs (e.g. FX-converted
                       prices, momentum heuristics).
          UNVERIFIED — a forward-looking inference (none emitted here yet).
        The LLM verdict is only allowed to cite VERIFIED/ESTIMATED figures.
        """
        ev: list[dict[str, Any]] = []

        ev.append({
            "claim": f"Market coverage: {len(products)} live listings across "
                     f"{len(platforms)} marketplaces.",
            "band": "VERIFIED",
            "basis": f"{len(products)} products from {', '.join(platforms) or 'n/a'}",
            "examples": [
                f"{p.platform}: {p.title[:50]}" for p in products[:3]
            ],
        })

        if price_summary.get("available"):
            ps = price_summary
            # Prices are FX-converted to USD → ESTIMATED (verified raw price,
            # estimated cross-currency normalization).
            ev.append({
                "claim": f"Price spans ${ps['min']:.2f}–${ps['max']:.2f} USD "
                         f"(median ${ps['median']:.2f}), a {ps.get('spread_pct')}% range.",
                "band": "ESTIMATED",
                "basis": f"{ps['count']} priced listings, FX-normalized to USD",
                "examples": [
                    f"{p['platform']}: ${p['price_usd']:.2f} — {p['title'][:40]}"
                    for p in best_value[:3] if p.get("price_usd")
                ],
            })

        if competitors:
            top = competitors[0]
            ev.append({
                "claim": f"'{top['brand']}' leads with {top['listings']} listings "
                         f"({top['share_pct']}% share), avg ★{top['avg_rating']}.",
                "band": "VERIFIED",
                "basis": f"{len(competitors)} distinct brands identified",
                "examples": [
                    f"{c['brand']}: {c['listings']} listings, ★{c['avg_rating']}, "
                    f"{c['total_reviews']} reviews"
                    for c in competitors[:4]
                ],
            })

        if momentum.get("total_reviews"):
            ev.append({
                "claim": f"Demand is {momentum['label'].upper()} — "
                         f"{momentum['total_reviews']:,} cumulative reviews, avg ★{momentum['avg_rating']}.",
                "band": "ESTIMATED",
                "basis": "review volume + rating + marketplace breadth (heuristic)",
                "examples": [f"momentum score {momentum['score']} / 1.0"],
            })

        if arbitrage:
            a = arbitrage[0]
            ev.append({
                "claim": f"Cross-platform gap: '{a['product'][:40]}' is {a['gap_pct']}% cheaper "
                         f"on {a['cheapest']['platform']} than {a['dearest']['platform']}.",
                "band": "ESTIMATED",
                "basis": f"{len(arbitrage)} matched cross-platform pairs (USD-normalized)",
                "examples": [
                    f"{a['cheapest']['platform']} ${a['cheapest']['price_usd']:.2f} → "
                    f"{a['dearest']['platform']} ${a['dearest']['price_usd']:.2f}"
                ],
            })

        return ev

    def _price_summary(self, prices: list[float]) -> dict[str, Any]:
        if not prices:
            return {"available": False, "count": 0}
        return {
            "available": True,
            "currency": _USD,
            "count": len(prices),
            "min": round(min(prices), 2),
            "max": round(max(prices), 2),
            "median": round(statistics.median(prices), 2),
            "mean": round(statistics.fmean(prices), 2),
            "spread_pct": round((max(prices) - min(prices)) / min(prices) * 100, 1)
            if min(prices) > 0 else None,
        }

    def _price_bands(self, priced: list[ProductRecord]) -> dict[str, Any]:
        prices = sorted(p.price_usd for p in priced if p.price_usd)
        if len(prices) < 3:
            return {"available": False}
        q1 = prices[len(prices) // 3]
        q2 = prices[2 * len(prices) // 3]
        bands = {"budget": 0, "mid": 0, "premium": 0}
        for p in prices:
            if p <= q1:
                bands["budget"] += 1
            elif p <= q2:
                bands["mid"] += 1
            else:
                bands["premium"] += 1
        return {
            "available": True,
            "budget_max": round(q1, 2),
            "premium_min": round(q2, 2),
            "counts": bands,
        }

    def _competitor_table(self, products: list[ProductRecord]) -> list[dict[str, Any]]:
        by_brand: dict[str, list[ProductRecord]] = defaultdict(list)
        for p in products:
            by_brand[p.brand or "unbranded"].append(p)
        total = len(products) or 1
        rows: list[dict[str, Any]] = []
        for brand, items in by_brand.items():
            ratings = [i.rating for i in items if i.rating is not None]
            bprices = [i.price_usd for i in items if i.price_usd]
            reviews = sum(i.review_count or 0 for i in items)
            rows.append({
                "brand": brand,
                "listings": len(items),
                "share_pct": round(len(items) / total * 100, 1),
                "avg_price_usd": round(statistics.fmean(bprices), 2) if bprices else None,
                "avg_rating": round(statistics.fmean(ratings), 2) if ratings else None,
                "total_reviews": reviews,
                "platforms": sorted({i.platform for i in items if i.platform}),
            })
        rows.sort(key=lambda r: (r["listings"], r["total_reviews"]), reverse=True)
        return rows[:15]

    def _top_rated(self, products: list[ProductRecord]) -> list[dict[str, Any]]:
        rated = [p for p in products if p.rating is not None and (p.review_count or 0) >= 1]
        rated.sort(key=lambda p: (p.rating or 0, p.review_count or 0), reverse=True)
        return [p.to_dict() for p in rated[:8]]

    def _most_popular(self, products: list[ProductRecord]) -> list[dict[str, Any]]:
        pop = [p for p in products if (p.review_count or 0) > 0]
        pop.sort(key=lambda p: p.review_count or 0, reverse=True)
        return [p.to_dict() for p in pop[:8]]

    def _best_value(self, products: list[ProductRecord]) -> list[dict[str, Any]]:
        valued = [p for p in products if p.value_score() > 0]
        valued.sort(key=lambda p: p.value_score(), reverse=True)
        return [p.to_dict() for p in valued[:8]]

    def _biggest_discounts(self, products: list[ProductRecord]) -> list[dict[str, Any]]:
        disc = [p for p in products if (p.discount_pct or 0) > 0]
        disc.sort(key=lambda p: p.discount_pct or 0, reverse=True)
        return [p.to_dict() for p in disc[:8]]

    def _arbitrage(self, priced: list[ProductRecord]) -> list[dict[str, Any]]:
        """Group near-identical products and report cross-platform price gaps."""
        groups: dict[str, list[ProductRecord]] = defaultdict(list)
        for p in priced:
            groups[self._product_key(p)].append(p)
        out: list[dict[str, Any]] = []
        for items in groups.values():
            # Cheapest listing per platform, so the gap is always cross-platform.
            # Comparison is in USD (price_usd) — never raw cross-currency prices.
            best_per_platform: dict[str, ProductRecord] = {}
            for i in items:
                if not i.price_usd or i.price_usd <= 0:
                    continue
                cur = best_per_platform.get(i.platform)
                if cur is None or (i.price_usd < (cur.price_usd or math.inf)):
                    best_per_platform[i.platform] = i
            if len(best_per_platform) < 2:
                continue
            ranked = sorted(best_per_platform.values(), key=lambda i: i.price_usd or math.inf)
            cheapest, dearest = ranked[0], ranked[-1]
            cp, dp = cheapest.price_usd, dearest.price_usd
            if not cp or not dp or cp <= 0:
                continue
            gap = (dp - cp) / cp * 100
            if gap < 8:
                continue
            out.append({
                "product": cheapest.title[:90],
                "cheapest": {
                    "platform": cheapest.platform, "price_usd": round(cp, 2),
                    "price": cheapest.price, "currency": cheapest.currency,
                    "url": cheapest.url,
                },
                "dearest": {
                    "platform": dearest.platform, "price_usd": round(dp, 2),
                    "price": dearest.price, "currency": dearest.currency,
                },
                "gap_pct": round(gap, 1),
            })
        out.sort(key=lambda r: r["gap_pct"], reverse=True)
        return out[:10]

    def _momentum(self, products: list[ProductRecord]) -> dict[str, Any]:
        reviews = [p.review_count or 0 for p in products]
        total_reviews = sum(reviews)
        rated = [p.rating for p in products if p.rating is not None]
        # Heuristic momentum score: review depth + rating quality + listing breadth.
        depth = min(1.0, total_reviews / 5000)
        quality = (statistics.fmean(rated) / 5) if rated else 0.0
        breadth = min(1.0, len({p.platform for p in products}) / 5)
        score = round(0.4 * depth + 0.35 * quality + 0.25 * breadth, 3)
        label = "hot" if score >= 0.6 else "warming" if score >= 0.35 else "cool"
        return {
            "score": score,
            "label": label,
            "total_reviews": total_reviews,
            "avg_rating": round(statistics.fmean(rated), 2) if rated else None,
        }

    def _picks(self, priced: list[ProductRecord]) -> dict[str, Any]:
        if not priced:
            return {}
        budget = min(priced, key=lambda p: p.price_usd or math.inf)
        premium = max(priced, key=lambda p: p.price_usd or 0)
        valued = [p for p in priced if p.value_score() > 0]
        value = max(valued, key=lambda p: p.value_score()) if valued else None
        return {
            "budget": budget.to_dict(),
            "best_value": value.to_dict() if value else None,
            "premium": premium.to_dict(),
        }

    # ------------------------------------------------------------------
    # Narrative
    # ------------------------------------------------------------------

    def _summary(
        self,
        query: str,
        products: list[ProductRecord],
        competitors: list[dict[str, Any]],
        price_summary: dict[str, Any],
        arbitrage: list[dict[str, Any]],
    ) -> str:
        if not products:
            return (
                f"No product listings found for '{query}'. Marketplaces may be "
                "rate-limited or the query is too narrow — broaden it and retry."
            )
        platforms = len({p.platform for p in products})
        lead = competitors[0]["brand"] if competitors else "unbranded"
        price_part = ""
        if price_summary.get("available"):
            price_part = (
                f" Prices range ${price_summary['min']:.2f}–${price_summary['max']:.2f} USD "
                f"(median ${price_summary['median']:.2f})."
            )
        arb_part = (
            f" {len(arbitrage)} cross-platform price gaps detected."
            if arbitrage else ""
        )
        return (
            f"Analyzed {len(products)} live listings for '{query}' across "
            f"{platforms} marketplaces. '{lead}' leads by listing volume."
            f"{price_part}{arb_part}"
        )

    def _actions(
        self,
        products: list[ProductRecord],
        competitors: list[dict[str, Any]],
        arbitrage: list[dict[str, Any]],
        momentum: dict[str, Any],
    ) -> list[str]:
        actions: list[str] = []
        if not products:
            return ["Broaden the query or retry — no structured listings captured."]
        if arbitrage:
            top = arbitrage[0]
            actions.append(
                f"Arbitrage: source on {top['cheapest']['platform']} and list where it "
                f"sells for {top['gap_pct']}% more ({top['product']})."
            )
        if momentum["label"] == "hot":
            actions.append("Demand is hot — prioritise sourcing and listing now.")
        elif momentum["label"] == "cool":
            actions.append("Demand is cool — monitor before committing capital.")
        if competitors and competitors[0]["share_pct"] > 40:
            actions.append(
                f"'{competitors[0]['brand']}' dominates ({competitors[0]['share_pct']}%) — "
                "differentiate on price or bundle, don't compete head-on."
            )
        actions.append("Open the Markets page to gate the best pick through compliance + geo.")
        return actions

    # ------------------------------------------------------------------
    # Field helpers
    # ------------------------------------------------------------------

    def _infer_brand(self, title: str, ps: dict[str, Any]) -> str:
        explicit = ps.get("brand")
        if explicit:
            return self._normalize_brand(str(explicit))
        # Scan leading title tokens for a known brand (handles "Philips BT3221 …").
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9'&]{1,}", title)
        for word in tokens[:4]:
            low = word.lower()
            if low in _BRAND_STOP:
                continue
            canon = self._normalize_brand(low)
            if canon in _KNOWN_BRANDS:
                return canon
        # Fall back to the first meaningful token, normalized.
        for word in tokens:
            low = word.lower()
            if low not in _BRAND_STOP and len(low) > 1:
                return self._normalize_brand(low)
        return "unbranded"

    @staticmethod
    def _normalize_brand(raw: str) -> str:
        """Fold casing, aliases and near-typos into a canonical brand name."""
        b = re.sub(r"[^a-z0-9 ]", "", raw.strip().lower()).strip()
        if not b:
            return "unbranded"
        if b in _BRAND_ALIASES:
            return _BRAND_ALIASES[b]
        if b in _KNOWN_BRANDS:
            return b
        first = b.split()[0]
        if first in _BRAND_ALIASES:
            return _BRAND_ALIASES[first]
        if first in _KNOWN_BRANDS:
            return first
        # Fuzzy match a typo ("adiddas" → "adidas", "phillips" → "philips").
        match = difflib.get_close_matches(first, _KNOWN_BRANDS, n=1, cutoff=0.86)
        return match[0] if match else first

    def _product_key(self, p: ProductRecord) -> str:
        words = [w for w in re.findall(r"[a-z0-9]+", p.title.lower()) if w not in _BRAND_STOP]
        return f"{p.brand}:{'-'.join(words[:4])}"

    @staticmethod
    def _platform_str(platform: Any) -> str:
        if platform is None:
            return ""
        return platform.value if hasattr(platform, "value") else str(platform)

    @staticmethod
    def _price_from_model(s: Any) -> float | None:
        price = getattr(s, "price", None)
        if price is not None and getattr(price, "amount", None) is not None:
            try:
                return float(price.amount)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _conf_from_model(s: Any) -> float:
        conf = getattr(s, "confidence", None)
        sc = getattr(conf, "source_confidence", None)
        try:
            return float(sc) if sc is not None else 0.5
        except (TypeError, ValueError):
            return 0.5

    @staticmethod
    def _coerce_float(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_int(v: Any) -> int | None:
        if v is None:
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def _data_quality(self, products: list[ProductRecord]) -> float:
        if not products:
            return 0.0
        priced = sum(1 for p in products if p.price)
        rated = sum(1 for p in products if p.rating is not None)
        n = len(products)
        return round(0.5 * (priced / n) + 0.5 * (rated / n), 3)
