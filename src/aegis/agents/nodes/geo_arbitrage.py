"""
GEO_ARBITRAGE agent — cross-market platform-asymmetry detection.

Hypothesis: a trend that's loud on one *kind* of platform but silent
on another represents an arbitrage opportunity — the audience on the
silent side hasn't seen it yet. The classic case is:

    "viral on TikTok / Reddit, invisible on Amazon search" →
        bring it to Amazon before competitors do.

We can't see "across markets" without a world-spanning data graph,
so we approximate from what we have: the `platforms` list on the
candidate. We classify each platform into a *role*:

    discovery_social     — TikTok, Reels, Shorts, Pinterest
    discussion_forum     — Reddit, HN, Bluesky, Mastodon
    commerce_marketplace — Amazon, Etsy, eBay, AliExpress, Shopify
    video_streaming      — YouTube, Twitch, Twitter
    news_media           — GDELT, news wires
    developer            — GitHub trending, Product Hunt

Then:
    * If the trend has signal on N≥2 *discovery* platforms but ZERO
      *commerce* platforms → strong arbitrage signal: the audience
      knows it exists but isn't buying it yet.
    * If discovery + commerce are both present → late stage; weaker
      signal (the arbitrage window may have closed).
    * If only commerce signals → no arbitrage (already a product).

Score in [0,1]:
    arbitrage_score = max(0, discovery_count/3) * (1 - 0.7 * commerce_present)

Verdicts:
    score >= 0.66  → PROCEED  (clear cross-market gap)
    score >= 0.33  → HOLD     (weak gap signal)
    else           → BLOCK    (no arbitrage; treat as info-only)

Like SENTINEL, GEO_ARBITRAGE never adds itself to `blocked_by`. Its
verdict is advisory; the supervisor weighs it alongside SCOUT for
priority assignment.

Author: AEGIS Pulse core team
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..llm import prompts
from ..schemas import AgentDecision, AgentVerdict, TrendCandidate
from .base import AgentNode

if TYPE_CHECKING:
    from ..state import GraphState

# Platform → role mapping. Lowercased lookup. Patterns are substring
# matches so "tiktok-creative-center" still maps to discovery_social.
_PLATFORM_ROLES: tuple[tuple[str, str], ...] = (
    ("tiktok", "discovery_social"),
    ("reels", "discovery_social"),
    ("instagram", "discovery_social"),
    ("shorts", "discovery_social"),
    ("pinterest", "discovery_social"),
    ("snapchat", "discovery_social"),
    ("reddit", "discussion_forum"),
    ("hacker", "discussion_forum"),
    ("hn", "discussion_forum"),
    ("bluesky", "discussion_forum"),
    ("mastodon", "discussion_forum"),
    ("nitter", "discussion_forum"),
    ("amazon", "commerce_marketplace"),
    ("etsy", "commerce_marketplace"),
    ("ebay", "commerce_marketplace"),
    ("aliexpress", "commerce_marketplace"),
    ("dhgate", "commerce_marketplace"),
    ("shopify", "commerce_marketplace"),
    ("walmart", "commerce_marketplace"),
    ("youtube", "video_streaming"),
    ("twitch", "video_streaming"),
    ("twitter", "video_streaming"),
    ("x_twitter", "video_streaming"),
    ("gdelt", "news_media"),
    ("news", "news_media"),
    ("github", "developer"),
    ("product_hunt", "developer"),
    ("producthunt", "developer"),
)


def _classify_platforms(platforms: list[str]) -> dict[str, list[str]]:
    """Bucket platforms into role groups."""
    out: dict[str, list[str]] = {
        "discovery_social": [],
        "discussion_forum": [],
        "commerce_marketplace": [],
        "video_streaming": [],
        "news_media": [],
        "developer": [],
        "unclassified": [],
    }
    for p in platforms or []:
        key = (p or "").lower()
        if not key:
            continue
        matched = False
        for needle, role in _PLATFORM_ROLES:
            if needle in key:
                out[role].append(p)
                matched = True
                break
        if not matched:
            out["unclassified"].append(p)
    return out


_PROCEED_THRESHOLD = 0.66
_HOLD_THRESHOLD = 0.33


class GeoArbitrageAgent(AgentNode):
    """Geo-arbitrage: cross-market platform-asymmetry detector."""

    name = "geo_arbitrage"

    async def _decide_heuristic(
        self,
        candidate: TrendCandidate,
        state: GraphState,
    ) -> AgentDecision:
        roles = _classify_platforms(candidate.platforms)

        discovery_count = len(roles["discovery_social"]) + len(roles["discussion_forum"])
        video_count = len(roles["video_streaming"])
        commerce_count = len(roles["commerce_marketplace"])

        commerce_present = 1.0 if commerce_count > 0 else 0.0

        # discovery_total caps at 3 so a single category-saturated
        # trend doesn't dominate.
        discovery_total = discovery_count + 0.5 * video_count
        discovery_signal = min(1.0, discovery_total / 3.0)

        # Arbitrage = strong discovery × weak commerce.
        arbitrage_score = discovery_signal * (1.0 - 0.7 * commerce_present)
        arbitrage_score = max(0.0, min(1.0, arbitrage_score))

        if arbitrage_score >= _PROCEED_THRESHOLD:
            verdict = AgentVerdict.PROCEED
        elif arbitrage_score >= _HOLD_THRESHOLD:
            verdict = AgentVerdict.HOLD
        else:
            verdict = AgentVerdict.BLOCK

        # Confidence rises with platform breadth and signal_count.
        breadth = min(5, len(candidate.platforms or []))
        confidence = 0.4 + 0.4 * (breadth / 5.0) + 0.2 * min(1.0, candidate.signal_count / 50.0)
        confidence = max(0.0, min(1.0, confidence))

        reasoning = (
            f"discovery={discovery_count} video={video_count} commerce={commerce_count}; "
            f"arbitrage_score={arbitrage_score:.2f}"
        )

        details: dict[str, Any] = {
            "arbitrage_score": arbitrage_score,
            "platform_roles": {k: list(v) for k, v in roles.items() if v},
            "discovery_count": discovery_count,
            "commerce_count": commerce_count,
            "video_count": video_count,
        }

        return AgentDecision(
            agent=self.name,
            trend_id=candidate.trend_id,
            correlation_id=candidate.correlation_id,
            verdict=verdict,
            score=arbitrage_score,
            confidence=confidence,
            reasoning=reasoning,
            details=details,
        )

    async def _augment_with_llm(
        self,
        candidate: TrendCandidate,
        state: GraphState,
        heuristic: AgentDecision,
    ) -> AgentDecision | None:
        if heuristic.verdict is not AgentVerdict.HOLD:
            return None
        try:
            user_text, _ver = prompts.render(
                "geo_arbitrage",
                title=candidate.title,
                summary=(candidate.summary or "")[:400],
                platforms=", ".join(candidate.platforms or []) or "<none>",
                arbitrage_score=round(heuristic.details.get("arbitrage_score", 0.0), 3),
                discovery_count=heuristic.details.get("discovery_count", 0),
                commerce_count=heuristic.details.get("commerce_count", 0),
            )
        except Exception:
            return None
        system_text = (
            "You are GEO_ARBITRAGE. Reply JSON only: "
            '{"reasoning": "<≤80 words on cross-market gap>", "confidence_factor": <0.5..1.0>}'
        )
        resp = await self._llm_complete(system=system_text, user=user_text)
        return self._llm_apply(heuristic, resp)

    def _extra_state(self, decision: AgentDecision) -> dict[str, Any]:
        return {
            "geo_arbitrage_score": float(decision.details.get("arbitrage_score", 0.0)),
        }

    def _merge_partial(
        self,
        decision: AgentDecision,
        state: GraphState,
    ) -> dict[str, Any]:
        # Advisory only — never adds to blocked_by.
        partial: dict[str, Any] = {"decisions": [decision]}
        partial.update(self._extra_state(decision))
        return partial
