"""CustomerOp — lead generation, acquisition, retention (grounded or silent).

The customer-function operator. It does competitive framing, derives a target
persona, surfaces **proxy** leads from real machinery, lists ₹0-budget
acquisition channels, drafts outreach, and proposes retention angles — then hands
the grounded numbers to the cross-cutting :class:`~aegis.mentor.persuasion.Persuader`
to build an objection-aware case.

Grounded sources only — nothing about customers is invented:

* **Audience signals** (``signals`` DB via ``fetch_recent_signals``) — real posts
  discussing the user's space. These are **demand/audience PROXIES**, explicitly
  labeled, *never* presented as confirmed buyers or purchase intent.
* **Demand proxy** (:class:`aegis.execution_intel.buyer.BuyerIntel`) — the honest
  ``BuyerDemandProxy`` keyed by (region, category). ``buyer_trust`` stays
  UNVERIFIED until real orders exist; the operator reuses that discipline and
  labels demand a proxy.
* **SectorPack** — real channel playbooks, marketplaces, CAC and return-rate
  benchmarks drive competitive framing, persona, acquisition channels, retention.

Heuristic-first: persona, channels, drafts and the persuasion case are all
deterministic; no LLM is required. Everything is injectable so tests run with
fakes and zero network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from aegis.mentor.operators.base import OperatorContext, OperatorResult
from aegis.mentor.persuasion import Evidence, Persuader

if TYPE_CHECKING:
    from aegis.mentor.schemas import UserProfile

_log = structlog.get_logger("aegis.mentor.operators.customer")

# How many proxy leads / outreach drafts to surface per altitude.
_LEADS_BY_DEPTH = {"surface": 3, "standard": 5, "deep": 8}

# ₹0-budget acquisition channels that work without ad spend. Online vs local is
# tuned from the profile; the sector pack's channel playbooks are layered on top.
_FREE_ONLINE_CHANNELS = (
    "Organic marketplace listing (Amazon.in / Flipkart) for free demand validation",
    "Short-form video (Instagram Reels / YouTube Shorts) — zero ad spend",
    "Relevant subreddit / niche community participation (no spam)",
    "WhatsApp / Telegram broadcast to an existing contact list",
)
_FREE_LOCAL_CHANNELS = (
    "Word-of-mouth + referrals from first customers",
    "Local WhatsApp groups and community boards",
    "Partner with one complementary local shop for cross-referral",
)


class CustomerOp:
    """Customer / lead-gen / acquisition / retention operator."""

    name = "customer"

    def __init__(
        self,
        *,
        buyer_intel: Any | None = None,
        signal_source: Any | None = None,
        persuader: Persuader | None = None,
    ) -> None:
        # All injectable so tests run with fakes and zero network.
        self._buyer_intel = buyer_intel        # BuyerIntel-like: demand_proxy(region, cat)
        self._signal_source = signal_source    # async fn(profile, request, ctx) -> rows
        self._persuader = persuader or Persuader()

    # ------------------------------------------------------------------
    # Lazy dependency resolution
    # ------------------------------------------------------------------

    def _get_buyer_intel(self, context: OperatorContext) -> Any | None:
        if self._buyer_intel is not None:
            return self._buyer_intel
        if context.pool is None:
            return None
        try:
            from aegis.execution_intel.buyer import BuyerIntel

            return BuyerIntel(context.pool)
        except Exception as exc:
            _log.debug("mentor.customer.buyer_intel_unavailable", error=str(exc)[:200])
            return None

    # ------------------------------------------------------------------
    # Grounded lead sourcing (proxies, honestly labeled)
    # ------------------------------------------------------------------

    async def _gather_leads(
        self, profile: UserProfile, request: str, context: OperatorContext
    ) -> list[dict[str, Any]]:
        if self._signal_source is not None:
            try:
                rows = await self._signal_source(profile, request, context)
                return list(rows or [])
            except Exception as exc:
                _log.warning("mentor.customer.signal_source_failed", error=str(exc)[:200])
                return []
        if context.pool is None:
            return []
        return await self._signals_from_db(profile, request, context)

    async def _signals_from_db(
        self, profile: UserProfile, request: str, context: OperatorContext
    ) -> list[dict[str, Any]]:
        try:
            from aegis.db.signals import fetch_recent_signals
        except Exception as exc:
            _log.debug("mentor.customer.signals_import_failed", error=str(exc)[:200])
            return []
        try:
            tenant = UUID(profile.tenant_id)
        except Exception:
            return []
        try:
            rows = await fetch_recent_signals(context.pool, tenant_id=tenant, limit=60)
        except Exception as exc:
            _log.warning("mentor.customer.signals_query_failed", error=str(exc)[:200])
            return []
        keywords = self._keywords(profile, request)
        return self._filter_relevant(rows, keywords)

    @staticmethod
    def _keywords(profile: UserProfile, request: str) -> list[str]:
        raw = f"{request or ''} {profile.sector_raw or ''}".lower()
        return [w for w in {t.strip() for t in raw.split()} if len(w) > 3]

    @staticmethod
    def _filter_relevant(
        rows: list[dict[str, Any]], keywords: list[str]
    ) -> list[dict[str, Any]]:
        if not keywords:
            return rows
        out: list[dict[str, Any]] = []
        for r in rows:
            blob = f"{r.get('title', '')} {r.get('raw_text', '')}".lower()
            if any(k in blob for k in keywords):
                out.append(r)
        return out

    # ------------------------------------------------------------------
    # Deterministic competitive / persona / channel / retention framing
    # ------------------------------------------------------------------

    def _free_channels(self, profile: UserProfile, context: OperatorContext) -> list[str]:
        from aegis.mentor.schemas import Channel

        base: list[str] = []
        if profile.channels in (Channel.LOCAL, Channel.BOTH):
            base.extend(_FREE_LOCAL_CHANNELS)
        if profile.channels in (Channel.ONLINE_ONLY, Channel.BOTH):
            base.extend(_FREE_ONLINE_CHANNELS)
        if not base:  # defensive — never leave the user without a free path
            base.extend(_FREE_ONLINE_CHANNELS)
        # Layer the active sector's real channel playbooks on top (grounded).
        pack = context.sector_pack
        if pack is not None:
            try:
                for pb in (pack.knowledge().get("channel_playbooks") or [])[:3]:
                    base.append(f"Sector playbook: {pb}")
            except Exception as exc:
                _log.debug("mentor.customer.pack_channels_failed", error=str(exc)[:200])
        # De-dup, preserve order.
        seen: set[str] = set()
        return [c for c in base if not (c in seen or seen.add(c))]

    @staticmethod
    def _persona(profile: UserProfile, context: OperatorContext) -> str:
        space = (profile.sector_raw or profile.sector or "this space").strip()
        region = profile.region or "IN"
        bits = [f"buyers in {region} shopping for {space}"]
        pack = context.sector_pack
        if pack is not None:
            try:
                markets = pack.knowledge().get("marketplaces") or []
                if markets:
                    bits.append(f"reachable on {', '.join(markets[:3])}")
                cac = pack.benchmarks().get("typical_cac_inr")
                if isinstance(cac, int | float):
                    bits.append(f"benchmark CAC ~₹{cac:g}")
            except Exception as exc:
                _log.debug("mentor.customer.persona_pack_failed", error=str(exc)[:200])
        return "Target persona (grounded): " + "; ".join(bits) + "."

    # ------------------------------------------------------------------
    # Outreach drafts (deterministic, no fabricated numbers)
    # ------------------------------------------------------------------

    @staticmethod
    def _drafts(profile: UserProfile, leads: list[dict[str, Any]], cap: int) -> list[str]:
        space = (profile.sector_raw or profile.sector or "your product").strip()
        drafts: list[str] = []
        for r in leads[:cap]:
            platform = str(r.get("platform", "")) or "the platform"
            drafts.append(
                f"Outreach draft ({platform}): reply to the discussion about {space} "
                "with a genuine, non-spammy answer and a soft mention of your offer."
            )
        if not drafts:
            drafts.append(
                f"Outreach draft: introduce {space} to one warm contact and ask for "
                "honest feedback before any paid push."
            )
        return drafts

    # ------------------------------------------------------------------
    # Operator entrypoint
    # ------------------------------------------------------------------

    async def run(
        self,
        profile: UserProfile,
        request: str,
        context: OperatorContext,
    ) -> OperatorResult:
        cap = _LEADS_BY_DEPTH.get(context.depth, 5)
        leads = await self._gather_leads(profile, request, context)
        demand = await self._demand_proxy(profile, context)

        findings: list[str] = []
        sources: set[str] = set()
        evidence: list[Evidence] = []

        # 1. Persona (grounded in sector reality).
        findings.append(self._persona(profile, context))
        if context.sector_pack is not None:
            sources.add(f"sector_pack:{getattr(context.sector_pack, 'sector_tag', 'baseline')}")

        # 2. Proxy leads — labeled, never confirmed buyers.
        lead_rows: list[dict[str, Any]] = []
        for r in leads[:cap]:
            label = (r.get("title") or r.get("raw_text") or "").strip()[:90]
            platform = str(r.get("platform", "")) or "signal"
            if not label:
                continue
            findings.append(
                f"(proxy lead — audience signal, NOT a confirmed buyer) {platform}: "
                f"“{label}”"
            )
            lead_rows.append(
                {"platform": platform, "title": label, "url": r.get("url"),
                 "proxy": True, "verified_buyer": False}
            )
        if lead_rows:
            sources.add("signals:audience_proxy")
            evidence.append(
                Evidence(
                    label="audience signals discussing this space",
                    value=float(len(lead_rows)),
                    unit=" signals",
                    source="signals:audience_proxy",
                    kind="audience",
                )
            )

        # 3. Demand proxy (honest UNVERIFIED labeling).
        if demand is not None and demand.demand_intensity is not None:
            findings.append(
                f"(demand PROXY — observed, not verified purchase intent) "
                f"intensity {round(demand.demand_intensity, 3)} for "
                f"{demand.region}/{demand.category}."
            )
            sources.add("execution_intel:buyer_demand_proxy")
            evidence.append(
                Evidence(
                    label="demand-intensity proxy",
                    value=round(float(demand.demand_intensity), 3),
                    unit="",
                    source="execution_intel:buyer_demand_proxy",
                    kind="demand",
                )
            )

        # 4. Competitive / benchmark evidence from the deep sector pack.
        evidence.extend(self._pack_evidence(context, findings, sources))

        # 5. Acquisition channels (₹0-budget) + outreach drafts + retention.
        free_channels = self._free_channels(profile, context)
        drafts = self._drafts(profile, lead_rows, cap)
        retention = self._retention(context, sources)

        actions: list[str] = []
        actions.extend(f"Free channel: {c}" for c in free_channels[:cap])
        actions.extend(drafts)
        actions.extend(retention)

        # 6. Conviction engine — grounded objection-aware case (or silent).
        case = self._persuader.build_case(evidence, free_channels=free_channels)
        persuasion_lines = case.render_lines() if case.has_case else []
        actions.extend(o.next_step for o in case.objections)

        confidence = self._confidence(evidence, lead_rows)
        reasoning = (
            f"Customer intel for '{request or profile.sector}': "
            f"{len(lead_rows)} proxy lead(s), "
            f"{'demand proxy present' if evidence and any(e.kind == 'demand' for e in evidence) else 'no demand proxy'}, "
            f"{len(case.objections)} objection(s) answered with real numbers. "
            "Leads and demand are PROXIES — never confirmed purchase intent."
        )

        return OperatorResult(
            operator=self.name,
            findings=findings,
            actions=self._dedup(actions),
            sources=sorted(sources),
            reasoning=reasoning,
            confidence=confidence,
            data={
                "persona": self._persona(profile, context),
                "leads": lead_rows,
                "lead_count": len(lead_rows),
                "acquisition_channels": free_channels,
                "outreach_drafts": drafts,
                "retention_angles": retention,
                "demand_proxy": demand.model_dump() if demand is not None else None,
                "persuasion": persuasion_lines,
                "persuasion_refused": case.refused,
                # Raw grounded numbers so the MentorAgent's Persuader can fold them in.
                "persuasion_evidence": [e.model_dump() for e in evidence],
                "all_leads_are_proxies": True,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _demand_proxy(
        self, profile: UserProfile, context: OperatorContext
    ) -> Any | None:
        intel = self._get_buyer_intel(context)
        if intel is None:
            return None
        category = (profile.sector_raw or profile.sector or "general").strip()
        try:
            return await intel.demand_proxy(profile.region or "IN", category)
        except Exception as exc:
            _log.debug("mentor.customer.demand_proxy_failed", error=str(exc)[:200])
            return None

    @staticmethod
    def _pack_evidence(
        context: OperatorContext, findings: list[str], sources: set[str]
    ) -> list[Evidence]:
        pack = context.sector_pack
        if pack is None:
            return []
        out: list[Evidence] = []
        try:
            bench = pack.benchmarks()
        except Exception:
            return []
        tag = getattr(pack, "sector_tag", "baseline")
        margin = bench.get("min_viable_gross_margin_pct")
        if isinstance(margin, int | float):
            findings.append(
                f"Competitive benchmark: minimum viable gross margin "
                f"{round(margin * 100, 1)}% (sector reality, not a saturation guess)."
            )
            sources.add(f"sector_pack:{tag}")
            out.append(
                Evidence(
                    label="minimum viable gross margin",
                    value=round(margin * 100, 1),
                    unit="%",
                    source=f"sector_pack:{tag}",
                    kind="margin",
                )
            )
        cac = bench.get("typical_cac_inr")
        if isinstance(cac, int | float):
            out.append(
                Evidence(
                    label="benchmark customer-acquisition cost",
                    value=round(float(cac), 1),
                    unit=" INR",
                    source=f"sector_pack:{tag}",
                    kind="cost",
                )
            )
        return out

    @staticmethod
    def _retention(context: OperatorContext, sources: set[str]) -> list[str]:
        pack = context.sector_pack
        angles: list[str] = []
        if pack is not None:
            try:
                rr = pack.benchmarks().get("expected_return_rate")
                if isinstance(rr, int | float):
                    angles.append(
                        f"Retention: returns/RTO run ~{round(rr * 100, 1)}% in this "
                        "sector — fix sizing/quality issues first; a kept customer beats "
                        "a refunded one."
                    )
                    sources.add(f"sector_pack:{getattr(pack, 'sector_tag', 'baseline')}")
            except Exception:
                pass
        if not angles:
            angles.append(
                "Retention: follow up with first customers for feedback — a repeat "
                "buyer costs nothing to re-acquire."
            )
        return angles

    @staticmethod
    def _dedup(items: list[str]) -> list[str]:
        seen: set[str] = set()
        return [i for i in items if not (i in seen or seen.add(i))]

    @staticmethod
    def _confidence(evidence: list[Evidence], leads: list[dict[str, Any]]) -> float:
        if not evidence and not leads:
            return 0.0
        # Grounded but proxy-heavy: deliberately moderate, never overclaimed.
        score = 0.15 * len(evidence) + 0.04 * min(len(leads), 8)
        return round(min(0.75, score), 3)
