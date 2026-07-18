"""
aegis.llm.routing.semantic_router — SemanticRouter
===================================================

Rule-based fast-path router that answers simple, deterministic queries
**without calling any LLM**.  This eliminates latency and API cost for
queries that never needed a neural model in the first place.

Architecture:
  Each ``Route`` has:
  - A name and a fixed response template.
  - A set of ``utterances`` — example phrases for this route.
  - An embedded centroid computed from the utterances at startup.

  At query time:
  - The query is embedded (via local sentence-transformers or Ollama).
  - Cosine similarity is computed against each centroid.
  - If max similarity > ``threshold``, return the route response immediately.

  This mirrors the architecture of the ``semantic-router`` OSS library
  but is implemented natively to avoid an extra dependency and to give
  full observability control.

Author: AEGIS Engineering
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from aegis.llm.constants import (
    SEMANTIC_ROUTER_THRESHOLD,
)
from aegis.llm.errors import RouterFailed

_log = structlog.get_logger("aegis.llm.routing.semantic_router")


# ---------------------------------------------------------------------------
# Embedding backend (lazy import — falls back gracefully)
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity (no numpy dependency)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    """Compute element-wise mean of a list of vectors."""
    if not vectors:
        return []
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]


# ---------------------------------------------------------------------------
# Route dataclass
# ---------------------------------------------------------------------------


@dataclass
class Route:
    """
    A semantic route: a named fast-path with fixed response.

    Attributes
    ----------
    name:
        Unique route identifier.
    utterances:
        Example queries that should trigger this route.
    response:
        Static response returned when this route fires.
        Use ``{query}`` as a placeholder if you want the original query echoed.
    threshold:
        Per-route override for the global similarity threshold.
    metadata:
        Arbitrary key-value data passed through in the response meta.
    """

    name: str
    utterances: list[str]
    response: str
    threshold: float = SEMANTIC_ROUTER_THRESHOLD
    metadata: dict[str, Any] = field(default_factory=dict)

    # Set after ``SemanticRouter.compile()``
    _centroid: list[float] = field(default_factory=list, repr=False)

    def fingerprint(self) -> str:
        """SHA-256 of utterances — used to detect stale compiled centroids."""
        payload = json.dumps(sorted(self.utterances), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# SemanticRouter
# ---------------------------------------------------------------------------


class SemanticRouter:
    """
    Fast-path semantic router that intercepts simple queries.

    Parameters
    ----------
    routes:
        List of ``Route`` objects defining the fast paths.
    embed_fn:
        Async callable ``(texts: list[str]) -> list[list[float]]``.
        Typically wired to ``OllamaProvider.embed()`` or a local
        sentence-transformers model.
    threshold:
        Global similarity threshold; per-route threshold takes precedence.

    Example
    -------
    .. code-block:: python

        router = SemanticRouter(routes=[
            Route(
                name="health_check",
                utterances=["are you healthy", "system status", "ping"],
                response="All systems operational.",
            ),
        ], embed_fn=ollama.embed)
        await router.compile()
        result = await router.route("system status?")
        # result.matched is True, result.route_name == "health_check"
    """

    def __init__(
        self,
        routes: list[Route],
        *,
        embed_fn: Any,  # Callable[[list[str]], Awaitable[list[list[float]]]]
        threshold: float = SEMANTIC_ROUTER_THRESHOLD,
    ) -> None:
        self._routes = routes
        self._embed_fn = embed_fn
        self._threshold = threshold
        self._compiled = False

    # ------------------------------------------------------------------
    # Compilation (builds centroids)
    # ------------------------------------------------------------------

    async def compile(self) -> None:
        """
        Embed all route utterances and compute centroids.

        Must be called once before ``route()``.  Idempotent — safe to
        call again if routes are updated.
        """
        all_texts: list[str] = []
        route_slices: list[tuple[int, int]] = []

        for route in self._routes:
            start = len(all_texts)
            all_texts.extend(route.utterances)
            route_slices.append((start, len(all_texts)))

        if not all_texts:
            self._compiled = True
            return

        try:
            embeddings = await self._embed_fn(all_texts)
        except Exception as exc:
            raise RouterFailed(f"Embedding failed during compile: {exc}") from exc

        for route, (start, end) in zip(self._routes, route_slices, strict=True):
            route._centroid = _mean_vector(embeddings[start:end])

        self._compiled = True
        _log.info(
            "semantic_router.compiled",
            route_count=len(self._routes),
            utterance_count=len(all_texts),
        )

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def route(self, query: str) -> RouterResult:
        """
        Attempt to route ``query`` to a fast-path.

        Returns a ``RouterResult`` with ``matched=False`` when no route
        exceeds the similarity threshold — the gateway then falls through
        to LLM completion.
        """
        if not self._compiled:
            raise RouterFailed("Router not compiled — call compile() first")

        if not self._routes:
            return RouterResult(matched=False, query=query)

        try:
            query_vec = (await self._embed_fn([query]))[0]
        except Exception as exc:
            _log.warning("semantic_router.embed_failed", query=query[:100], error=str(exc))
            return RouterResult(matched=False, query=query)

        best_score = 0.0
        best_route: Route | None = None

        for route in self._routes:
            if not route._centroid:
                continue
            score = _cosine_similarity(query_vec, route._centroid)
            if score > best_score:
                best_score = score
                best_route = route

        threshold = (
            best_route.threshold if best_route else self._threshold
        )

        if best_route and best_score >= threshold:
            response_text = best_route.response.replace("{query}", query)
            _log.debug(
                "semantic_router.hit",
                route=best_route.name,
                score=round(best_score, 4),
                threshold=threshold,
            )
            return RouterResult(
                matched=True,
                query=query,
                route_name=best_route.name,
                response=response_text,
                score=best_score,
                metadata=best_route.metadata,
            )

        _log.debug(
            "semantic_router.miss",
            best_score=round(best_score, 4),
            best_route=best_route.name if best_route else None,
        )
        return RouterResult(matched=False, query=query, score=best_score)

    # ------------------------------------------------------------------
    # Dynamic management
    # ------------------------------------------------------------------

    def add_route(self, route: Route) -> None:
        """Add a route at runtime — requires re-compile() to take effect."""
        self._routes.append(route)
        self._compiled = False

    def remove_route(self, name: str) -> None:
        """Remove a route by name — requires re-compile() to take effect."""
        self._routes = [r for r in self._routes if r.name != name]
        self._compiled = False


# ---------------------------------------------------------------------------
# RouterResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RouterResult:
    """Result of a ``SemanticRouter.route()`` call."""

    matched: bool
    query: str
    route_name: str | None = None
    response: str | None = None
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
