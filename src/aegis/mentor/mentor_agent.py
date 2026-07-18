"""MentorAgent — the orchestrator brain.

Routes the user's request to the operator fleet, runs them, then synthesizes a
grounded :class:`Counsel`. Two non-negotiables shape every method:

* **Heuristic-first** — the counsel skeleton (summary, next-steps, claims,
  confidence) is built deterministically from operator results. The LLM only
  rewrites the *prose* of the summary; it can never add a claim, a step, or
  change the confidence.
* **Adaptive altitude** — ``autonomy_preference`` decides which operators run
  and how much detail surfaces: ``guide_me`` (do-it-for-me) runs the full fleet
  and lists concrete steps; ``answer_me`` (brief-me) runs lean and stays sharp.
"""

from __future__ import annotations

import structlog

from aegis.mentor.config import MentorSettings
from aegis.mentor.operators.base import Operator, OperatorContext, OperatorResult
from aegis.mentor.operators.customer import CustomerOp
from aegis.mentor.operators.explore import ExploreOp
from aegis.mentor.operators.finance import FinanceOp
from aegis.mentor.operators.knowledge import KnowledgeOp
from aegis.mentor.operators.ops import OpsOp
from aegis.mentor.operators.research import ResearchOp
from aegis.mentor.operators.supplier import SupplierOp
from aegis.mentor.persuasion import Evidence, Persuader
from aegis.mentor.schemas import AutonomyPreference, Counsel, UserProfile
from aegis.mentor.sector import SectorRouter

_log = structlog.get_logger("aegis.mentor.mentor_agent")

# Depth per altitude: novices get it run thoroughly *for* them; an operator
# wanting sharp analysis also gets deep; the co-pilot gets a standard pass.
_DEPTH_BY_AUTONOMY = {
    AutonomyPreference.GUIDE_ME: "deep",
    AutonomyPreference.COACH_ME: "standard",
    AutonomyPreference.ANSWER_ME: "deep",
}

# Max next-steps surfaced per altitude — brief-me stays terse.
_MAX_STEPS_BY_AUTONOMY = {
    AutonomyPreference.GUIDE_ME: 8,
    AutonomyPreference.COACH_ME: 5,
    AutonomyPreference.ANSWER_ME: 3,
}

# Keyword → operator routing. A request matching none defaults to research+knowledge.
_EXPLORE_KEYWORDS = (
    "explore", "discover", "new market", "niche", "what should i", "ideas",
    "opportunit", "find me", "trending",
)
_KNOWLEDGE_KEYWORDS = (
    "how", "what is", "explain", "learn", "teach", "regulation", "gst", "rules",
    "ground reality", "basics", "guide",
)
_SUPPLIER_KEYWORDS = (
    "supplier", "source", "vendor", "manufacturer", "wholesale", "moq",
    "sourcing", "fulfil", "fulfill", "dropship",
)
_CUSTOMER_KEYWORDS = (
    "customer", "lead", "acquire", "acquisition", "audience", "persona",
    "marketing", "outreach", "retention", "sell to", "convince", "persuade",
)
_OPS_KEYWORDS = (
    "price", "pricing", "fulfil", "fulfill", "ship", "operation", "ops",
    "settle", "settlement", "execute plan", "how much should i charge",
)
_FINANCE_KEYWORDS = (
    "finance", "pnl", "p&l", "profit", "unit economics", "margin", "capital",
    "runway", "break-even", "breakeven", "cost", "budget", "afford",
)


class MentorAgent:
    """Orchestrator that turns a request + profile into grounded counsel."""

    def __init__(
        self,
        *,
        pool: object | None = None,
        redis: object | None = None,
        settings: MentorSettings | None = None,
        router: SectorRouter | None = None,
        operators: dict[str, Operator] | None = None,
        persuader: Persuader | None = None,
    ) -> None:
        self._pool = pool
        self._redis = redis
        self._settings = settings or MentorSettings()
        self._router = router or SectorRouter()
        self._persuader = persuader or Persuader()
        self._operators: dict[str, Operator] = operators or {
            "research": ResearchOp(),
            "explore": ExploreOp(),
            "knowledge": KnowledgeOp(),
            "supplier": SupplierOp(),
            "customer": CustomerOp(),
            "ops": OpsOp(),
            "finance": FinanceOp(),
        }

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _route(self, request: str, profile: UserProfile) -> list[str]:
        """Pick which operators to run for this request + altitude."""
        text = (request or "").lower()
        chosen: list[str] = []

        if any(k in text for k in _EXPLORE_KEYWORDS):
            chosen.append("explore")
        if any(k in text for k in _KNOWLEDGE_KEYWORDS):
            chosen.append("knowledge")
        if any(k in text for k in _SUPPLIER_KEYWORDS):
            chosen.append("supplier")
        if any(k in text for k in _CUSTOMER_KEYWORDS):
            chosen.append("customer")
        if any(k in text for k in _OPS_KEYWORDS):
            chosen.append("ops")
        if any(k in text for k in _FINANCE_KEYWORDS):
            chosen.append("finance")
        # Research is the default backbone unless the request is purely explore/knowledge.
        if "research" in text or "market" in text or "analyze" in text or not chosen:
            chosen.insert(0, "research")

        # Altitude shaping: do-it-for-me runs the full fleet; brief-me stays lean.
        if profile.autonomy_preference == AutonomyPreference.GUIDE_ME:
            for op in (
                "research", "knowledge", "explore", "supplier", "customer", "ops", "finance"
            ):
                if op not in chosen:
                    chosen.append(op)
        elif profile.autonomy_preference == AutonomyPreference.ANSWER_ME and (
            "explore" not in text and "discover" not in text
        ):
            # Sharp + lean: drop exploration unless explicitly asked for it.
            chosen = [c for c in chosen if c != "explore"]

        # De-dup, keep order, only keep operators we actually have.
        seen: set[str] = set()
        return [c for c in chosen if c in self._operators and not (c in seen or seen.add(c))]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def advise(self, profile: UserProfile, request: str) -> Counsel:
        """Run the routed operators and synthesize grounded counsel."""
        context = OperatorContext(
            pool=self._pool,
            redis=self._redis,
            sector_pack=self._router.route(profile.sector),
            use_llm=self._settings.llm_enrichment_enabled,
            depth=_DEPTH_BY_AUTONOMY.get(profile.autonomy_preference, "standard"),
        )

        op_names = self._route(request, profile)
        results: list[OperatorResult] = []
        for name in op_names:
            try:
                results.append(await self._operators[name].run(profile, request, context))
            except Exception as exc:  # one operator failing never sinks the rest
                _log.warning("mentor.operator.failed", operator=name, error=str(exc)[:200])

        counsel = self._synthesize(profile, request, results)
        _log.info(
            "mentor.advised",
            sector=profile.sector,
            operators=op_names,
            confidence=counsel.confidence,
            claims=len(counsel.claims),
        )
        if context.use_llm:
            counsel = await self._enrich_prose(counsel, profile, request)
        return counsel

    # ------------------------------------------------------------------
    # Synthesis (deterministic — grounded or silent)
    # ------------------------------------------------------------------

    def _synthesize(
        self,
        profile: UserProfile,
        request: str,
        results: list[OperatorResult],
    ) -> Counsel:
        grounded = [r for r in results if r.has_signal]
        claims: list[str] = []
        steps: list[str] = []
        sources: set[str] = set()
        for r in grounded:
            claims.extend(r.findings)
            steps.extend(r.actions)
            sources.update(r.sources)

        # Confidence = mean of grounded operators' confidence (0 when none).
        confidence = (
            round(sum(r.confidence for r in grounded) / len(grounded), 3)
            if grounded
            else 0.0
        )

        max_steps = _MAX_STEPS_BY_AUTONOMY.get(profile.autonomy_preference, 5)
        # De-dup steps preserving order.
        seen: set[str] = set()
        unique_steps = [s for s in steps if not (s in seen or seen.add(s))][:max_steps]

        summary = self._deterministic_summary(profile, request, grounded, confidence)

        # Conviction engine: build an evidence-first, objection-aware case from the
        # grounded numbers the operators already produced. Never adds a claim — it
        # only strengthens what is grounded, and stays silent when evidence is absent.
        persuasion = self._build_persuasion(grounded)

        return Counsel(
            summary=summary,
            matches=[],  # OpportunityMatcher arrives in a later phase
            next_steps=unique_steps,
            claims=claims,
            sources=sorted(sources),
            persuasion=persuasion,
            confidence=confidence,
            altitude=profile.autonomy_preference,
        )

    def _build_persuasion(self, grounded: list[OperatorResult]) -> list[str]:
        """Harvest grounded evidence from operator data and run the Persuader.

        Operators that surface real numbers attach them as
        ``data["persuasion_evidence"]`` (a list of ``Evidence``-compatible dicts).
        CustomerOp already builds the richest case; here we fold in any operator's
        grounded numbers. Returns persuasion lines, or empty when nothing grounds.
        """
        evidence: list[Evidence] = []
        free_channels: list[str] = []
        for r in grounded:
            for raw in r.data.get("persuasion_evidence", []) or []:
                try:
                    evidence.append(Evidence(**raw))
                except Exception:  # malformed → skip, never fabricate
                    continue
            channels = r.data.get("acquisition_channels")
            if channels and not free_channels:
                free_channels = list(channels)
        if not evidence:
            return []
        case = self._persuader.build_case(evidence, free_channels=free_channels)
        return case.render_lines() if case.has_case else []

    @staticmethod
    def _deterministic_summary(
        profile: UserProfile,
        request: str,
        grounded: list[OperatorResult],
        confidence: float,
    ) -> str:
        if not grounded:
            return (
                f"I couldn't ground an answer for '{request or profile.sector}' yet — "
                "the live sources returned nothing usable. Rather than guess, I'd rather "
                "re-run when there's real data. (Grounded-or-silent.)"
            )
        ops = ", ".join(r.operator for r in grounded)
        n_claims = sum(len(r.findings) for r in grounded)
        return (
            f"For '{request or profile.sector}' I consulted {ops} and grounded "
            f"{n_claims} findings (confidence {confidence}). See the claims and steps below."
        )

    # ------------------------------------------------------------------
    # LLM prose enrichment (text only — never changes claims/steps/confidence)
    # ------------------------------------------------------------------

    async def _enrich_prose(
        self,
        counsel: Counsel,
        profile: UserProfile,
        request: str,
    ) -> Counsel:
        # Nothing grounded → stay silent; never let the LLM fill the void.
        if not counsel.claims:
            return counsel
        try:
            from aegis.llm.bridge.agents_bridge import complete_for_agent
        except Exception:
            return counsel

        facts = {
            "request": request,
            "sector": profile.sector,
            "autonomy": profile.autonomy_preference.value,
            "claims": counsel.claims,
            "confidence": counsel.confidence,
        }
        system = (
            "You are AEGIS Mentor. Rewrite ONLY the summary prose for a user, "
            "grounded strictly in the supplied claims. Never add a fact, number, or "
            "claim not present. Match the altitude: guide_me=encouraging+simple, "
            "coach_me=collaborative, answer_me=terse+sharp. 2-4 sentences."
        )
        try:
            import json

            text = await complete_for_agent(
                "mentor",
                [{"role": "system", "content": system},
                 {"role": "user", "content": json.dumps(facts, default=str)}],
                temperature=self._settings.llm_temperature,
                max_tokens=self._settings.llm_max_tokens,
            )
        except Exception:
            return counsel

        text = (text or "").strip()
        if not text:
            return counsel
        # Immutable model → return a copy with only the prose swapped.
        return counsel.model_copy(update={"summary": text})
