"""
Creator influence graph for coordinated behaviour detection.

Phase 0 scrape layer. Builds a directed graph of creator→signal relationships
to detect coordinated posting campaigns and measure organic reach.

Uses NetworkX (already installed). Zero new dependencies.

Key metrics:
  - PageRank: identifies most influential creators
  - Clustering coefficient: high clustering = coordinated network
  - Bridge score: rare creators that connect separate communities = organic KOLs
  - Coordination score: many creators posting same content simultaneously = suspicious
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger("aegis.scrape.creator_graph")

try:
    import networkx as nx

    _NX_AVAILABLE = True
except ImportError:
    _NX_AVAILABLE = False


@dataclass
class CreatorGraphMetrics:
    """Metrics extracted from the creator influence graph."""
    node_count: int
    edge_count: int
    top_creators_by_pagerank: list[tuple[str, float]]  # (creator_id, score)
    avg_clustering: float        # 0-1; high = coordinated
    coordination_score: float    # 0-1; high = suspicious coordination
    organic_score: float         # 0-1; high = genuinely organic
    bridge_creators: list[str]   # creators connecting separate communities
    computed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.computed_at is None:
            self.computed_at = datetime.now(UTC)


class CreatorGraph:
    """
    Build and analyze creator influence graphs from signal batches.

    A creator graph has:
    - Nodes: creator IDs (author handles/IDs from signals)
    - Edges: A→B when creator A's content is shared/referenced by creator B
    - Weights: edge weight = number of co-occurrence / shared-topic events
    """

    def __init__(self) -> None:
        self._g: Any = None

    def build_from_signals(self, signals: list[dict]) -> bool:
        """Build graph from a list of signal dicts. Returns True if successful."""
        if not _NX_AVAILABLE:
            return False
        try:
            self._g = nx.DiGraph()
            # Add creator nodes
            creators: dict[str, dict] = {}
            for sig in signals:
                author = sig.get("author_id") or sig.get("author") or ""
                if not author:
                    continue
                platform = sig.get("platform", "unknown")
                key = f"{platform}:{author}"
                if key not in creators:
                    creators[key] = {"signals": [], "platform": platform}
                creators[key]["signals"].append(sig)

            for creator_id, data in creators.items():
                self._g.add_node(
                    creator_id,
                    platform=data["platform"],
                    signal_count=len(data["signals"]),
                )

            # Add edges: creators who posted on the same topic are considered part
            # of the same propagation event.
            creator_list = list(creators.items())
            for i, (c1_id, c1_data) in enumerate(creator_list):
                for c2_id, c2_data in creator_list[i + 1:]:
                    if c1_id == c2_id:
                        continue
                    overlap = self._topic_overlap(c1_data["signals"], c2_data["signals"])
                    if overlap > 0:
                        self._g.add_edge(c1_id, c2_id, weight=overlap)
            return True
        except Exception as exc:
            _log.warning("creator_graph.build_failed", error=str(exc))
            return False

    def _topic_overlap(self, sigs_a: list[dict], sigs_b: list[dict]) -> int:
        """Count shared topic keywords between two creator's signal sets."""
        words_a: set[str] = set()
        words_b: set[str] = set()
        for s in sigs_a:
            words_a.update(re.findall(r"\b[a-z]{5,}\b", s.get("title", "").lower()))
        for s in sigs_b:
            words_b.update(re.findall(r"\b[a-z]{5,}\b", s.get("title", "").lower()))
        return len(words_a & words_b)

    def compute_metrics(self) -> CreatorGraphMetrics | None:
        """Compute graph metrics. Returns None if graph not built or too small."""
        if not _NX_AVAILABLE or self._g is None:
            return None
        if len(self._g.nodes) < 3:
            return CreatorGraphMetrics(
                node_count=len(self._g.nodes),
                edge_count=len(self._g.edges),
                top_creators_by_pagerank=[],
                avg_clustering=0.0,
                coordination_score=0.0,
                organic_score=1.0,
                bridge_creators=[],
            )
        try:
            # PageRank (who drives the most engagement propagation)
            pagerank = nx.pagerank(self._g, alpha=0.85, weight="weight")
            top_creators = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:10]

            # Clustering (high = coordinated posting)
            undirected = self._g.to_undirected()
            avg_clustering = nx.average_clustering(undirected)

            # Coordination score: high avg_clustering + few bridge nodes = coordinated
            bridges = list(nx.bridges(undirected)) if len(undirected.edges) > 0 else []
            bridge_nodes: set[str] = set()
            for u, v in bridges:
                bridge_nodes.update([u, v])

            density = nx.density(self._g)
            # Coordination: dense, highly clustered, no bridges = bot network
            coordination_score = min(1.0, (avg_clustering * 0.6 + density * 0.4))
            # Organic: diverse, low clustering, many bridges = real influencers
            organic_score = max(0.0, 1.0 - coordination_score)

            return CreatorGraphMetrics(
                node_count=len(self._g.nodes),
                edge_count=len(self._g.edges),
                top_creators_by_pagerank=top_creators,
                avg_clustering=round(avg_clustering, 4),
                coordination_score=round(coordination_score, 4),
                organic_score=round(organic_score, 4),
                bridge_creators=list(bridge_nodes)[:10],
            )
        except Exception as exc:
            _log.warning("creator_graph.metrics_failed", error=str(exc))
            return None
