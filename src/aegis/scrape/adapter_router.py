"""Adapter Router — selects optimal adapters for a topic query.

Phase 0 scrape layer. Combines topic-type affinity from
:mod:`aegis.scrape.topic_classifier`, real-time adapter health from
``SwarmAgentPool``, and a conservative yield prior to rank adapters.

The router is the answer to "which 8 of our 35 adapters should we use for
THIS query?"
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, cast

import structlog

from aegis.scrape.topic_classifier import ADAPTER_AFFINITY, TopicClassifier, TopicType

_log = structlog.get_logger("aegis.scrape.adapter_router")

# Adapters that require credentials not set by default
_CRED_REQUIRED: dict[str, str] = {
    "reddit": "AEGIS_REDDIT_CLIENT_ID",
    "youtube": "AEGIS_YOUTUBE_API_KEY",
    "instagram": "AEGIS_INSTAGRAM_SESSION",
}

# Adapters that require FlareSolverr for their protected HTML paths
_FLARESOLVERR_REQUIRED = frozenset({"flipkart", "myntra"})

# Health score used for adapters with no recorded history — optimistic so new
# adapters get a chance, but below a proven-HEALTHY adapter's 1.0.
_DEFAULT_HEALTH = 0.7

# Health-state → score mapping for SwarmAgentPool's AgentHealth enum.
_HEALTH_STATE_SCORES: dict[str, float] = {
    "healthy": 1.0,
    "unknown": _DEFAULT_HEALTH,
    "degraded": 0.45,
    "down": 0.1,
}

# Conservative yield prior; constant for now so ranking is affinity × health.
_EST_YIELD = 15


@dataclass
class AdapterRecommendation:
    """One ranked adapter suggestion for a topic query."""

    adapter_name: str
    priority: int  # 1 = highest
    topic_type: TopicType
    affinity_score: float  # base affinity from matrix
    health_score: float  # current health tracker score
    final_score: float  # affinity × health × log(yield+1)/log(101)
    reason: str  # human-readable
    requires_credentials: bool
    credentials_available: bool
    requires_flaresolverr: bool


class AdapterRouter:
    """Route topic queries to optimal adapters.

    Args:
        health_tracker: ``SwarmAgentPool`` (or compatible) instance for
            real-time health scores. Optional — without it every adapter
            gets the optimistic default health.
        redis: Redis client reserved for historical yield lookup. Optional.
        classifier: ``TopicClassifier`` instance (created if None).
    """

    def __init__(
        self,
        health_tracker: Any | None = None,
        redis: Any | None = None,
        classifier: TopicClassifier | None = None,
    ) -> None:
        self._health = health_tracker
        self._redis = redis
        self._classifier = classifier or TopicClassifier()

    def route(
        self,
        topic: str,
        *,
        top_n: int = 10,
        exclude_credentials_required: bool = False,
        exclude_flaresolverr: bool = False,
        explicit_topic_type: TopicType | None = None,
    ) -> list[AdapterRecommendation]:
        """Return ranked adapter recommendations for a topic query.

        Args:
            topic: The search query or topic.
            top_n: Maximum adapters to return.
            exclude_credentials_required: Skip adapters needing unset API keys.
            exclude_flaresolverr: Skip adapters needing FlareSolverr.
            explicit_topic_type: Override auto-detected topic type.

        Returns:
            Ranked list of :class:`AdapterRecommendation`, highest score first.
        """
        topic_type = explicit_topic_type or self._classifier.classify(topic)
        affinity_map = ADAPTER_AFFINITY.get(
            topic_type, ADAPTER_AFFINITY[TopicType.MARKET_RESEARCH]
        )

        health_scores = self._get_health_scores()
        recommendations: list[AdapterRecommendation] = []

        for adapter_name, base_affinity in affinity_map.items():
            cred_required = adapter_name in _CRED_REQUIRED
            cred_available = self._check_credentials(adapter_name)
            flare_required = adapter_name in _FLARESOLVERR_REQUIRED

            if exclude_credentials_required and cred_required and not cred_available:
                continue
            if exclude_flaresolverr and flare_required:
                continue

            health = health_scores.get(adapter_name, _DEFAULT_HEALTH)
            final = base_affinity * health * math.log(_EST_YIELD + 1) / math.log(101)

            recommendations.append(
                AdapterRecommendation(
                    adapter_name=adapter_name,
                    priority=0,
                    topic_type=topic_type,
                    affinity_score=base_affinity,
                    health_score=health,
                    final_score=round(final, 4),
                    reason=self._explain(topic_type, base_affinity, health),
                    requires_credentials=cred_required,
                    credentials_available=cred_available,
                    requires_flaresolverr=flare_required,
                )
            )

        recommendations.sort(key=lambda r: r.final_score, reverse=True)
        top = recommendations[:top_n]
        for i, rec in enumerate(top):
            rec.priority = i + 1

        _log.info(
            "adapter_router.routed",
            topic=topic,
            topic_type=topic_type.value,
            top_adapters=[r.adapter_name for r in top[:3]],
            total_candidates=len(recommendations),
        )
        return top

    def _get_health_scores(self) -> dict[str, float]:
        """Extract per-adapter health scores from the tracker.

        Supports two interfaces: a generic ``get_all_agents()`` returning
        objects with ``success_rate``, or ``SwarmAgentPool`` whose ``agents``
        dict holds ``ScraperAgent`` objects with an ``AgentHealth`` enum and
        cooling state. Any failure degrades to an empty map (default health).
        """
        if self._health is None:
            return {}
        try:
            tracker: Any = self._health
            getter: Any = getattr(tracker, "get_all_agents", None)
            if callable(getter):
                agents = cast("dict[str, Any]", getter())
                return {
                    str(name): min(1.0, max(0.1, float(agent.success_rate)))
                    for name, agent in agents.items()
                }
            pool_agents: Any = getattr(tracker, "agents", None)
            if isinstance(pool_agents, dict):
                scores: dict[str, float] = {}
                name: Any
                agent: Any
                for name, agent in pool_agents.items():
                    if getattr(agent, "is_cooling", False):
                        scores[str(name)] = 0.1
                        continue
                    state = getattr(getattr(agent, "health", None), "value", "unknown")
                    scores[str(name)] = _HEALTH_STATE_SCORES.get(state, _DEFAULT_HEALTH)
                return scores
        except Exception as exc:
            _log.debug("adapter_router.health_lookup_failed", error=str(exc))
        return {}

    def _check_credentials(self, adapter_name: str) -> bool:
        env_key = _CRED_REQUIRED.get(adapter_name)
        if env_key is None:
            return True
        return bool(os.environ.get(env_key, "").strip())

    def _explain(self, topic_type: TopicType, affinity: float, health: float) -> str:
        topic_label = topic_type.value.replace("_", " ")
        if affinity >= 0.90:
            return f"Primary source for {topic_label}"
        if affinity >= 0.75:
            return f"Strong secondary for {topic_label} ({int(health * 100)}% health)"
        return f"Supplementary coverage ({int(affinity * 100)}% affinity)"


__all__ = ["AdapterRecommendation", "AdapterRouter"]
