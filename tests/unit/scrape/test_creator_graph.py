"""Pass 9D — NetworkX creator influence graph tests."""

from __future__ import annotations

from aegis.scrape import creator_graph
from aegis.scrape.creator_graph import CreatorGraph, CreatorGraphMetrics


def _sig(author: str, title: str, platform: str = "reddit") -> dict:
    return {"author": author, "title": title, "platform": platform}


def test_build_returns_false_without_networkx(monkeypatch):
    monkeypatch.setattr(creator_graph, "_NX_AVAILABLE", False)
    g = CreatorGraph()
    assert g.build_from_signals([_sig("a", "hello world")]) is False
    assert g.compute_metrics() is None


def test_empty_signals_builds_empty_graph():
    g = CreatorGraph()
    assert g.build_from_signals([]) is True
    metrics = g.compute_metrics()
    # fewer than 3 nodes → conservative organic-only metrics
    assert isinstance(metrics, CreatorGraphMetrics)
    assert metrics.node_count == 0
    assert metrics.organic_score == 1.0


def test_small_graph_is_organic():
    sigs = [_sig("alice", "machine learning models"), _sig("bob", "gardening tips")]
    g = CreatorGraph()
    g.build_from_signals(sigs)
    metrics = g.compute_metrics()
    assert metrics is not None
    assert metrics.node_count == 2
    assert metrics.coordination_score == 0.0
    assert metrics.organic_score == 1.0


def test_coordinated_network_detected():
    # Many creators posting the SAME topic words → dense, clustered graph.
    shared = "wireless earbuds bluetooth audio trending market"
    sigs = [_sig(f"bot{i}", shared, platform="reddit") for i in range(8)]
    g = CreatorGraph()
    g.build_from_signals(sigs)
    metrics = g.compute_metrics()
    assert metrics is not None
    assert metrics.node_count == 8
    assert metrics.edge_count > 0
    assert metrics.coordination_score > 0.0
    # coordination + organic are complementary
    assert abs((metrics.coordination_score + metrics.organic_score) - 1.0) < 1e-6


def test_pagerank_populated_for_large_graph():
    shared = "alpha beta gamma delta epsilon trending"
    sigs = [_sig(f"creator{i}", shared) for i in range(5)]
    g = CreatorGraph()
    g.build_from_signals(sigs)
    metrics = g.compute_metrics()
    assert metrics is not None
    assert len(metrics.top_creators_by_pagerank) >= 1
    assert all(isinstance(s, float) for _, s in metrics.top_creators_by_pagerank)


def test_signals_without_author_are_skipped():
    sigs = [{"title": "no author", "platform": "x"}, _sig("a", "topic words here")]
    g = CreatorGraph()
    g.build_from_signals(sigs)
    metrics = g.compute_metrics()
    assert metrics is not None
    assert metrics.node_count == 1
