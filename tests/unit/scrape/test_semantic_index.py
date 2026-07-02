"""Pass 9B — FAISS semantic signal index tests (graceful without faiss/embedder)."""

from __future__ import annotations

import numpy as np
import pytest

from aegis.scrape import semantic_index
from aegis.scrape.semantic_index import (
    SemanticSearchResult,
    SemanticSignalIndex,
    get_signal_index,
)


async def test_add_returns_false_without_faiss(monkeypatch):
    monkeypatch.setattr(semantic_index, "_FAISS_AVAILABLE", False)
    idx = SemanticSignalIndex()
    assert await idx.add("sig-1", "wireless earbuds trending") is False


async def test_search_returns_empty_without_faiss(monkeypatch):
    monkeypatch.setattr(semantic_index, "_FAISS_AVAILABLE", False)
    idx = SemanticSignalIndex()
    assert await idx.search("anything", top_k=5) == []


async def test_is_duplicate_false_when_empty():
    idx = SemanticSignalIndex()
    is_dup, sid = await idx.is_duplicate("brand new signal text")
    assert is_dup is False
    assert sid is None


@pytest.mark.skipif(not semantic_index._FAISS_AVAILABLE, reason="faiss-cpu not installed")
async def test_add_and_search_round_trip(monkeypatch):
    # Stub the embedder so the test never needs a live gateway.
    async def _fake_embed(self, text):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.standard_normal(semantic_index._DIM).astype(np.float32)
        return v / np.linalg.norm(v)

    monkeypatch.setattr(SemanticSignalIndex, "_embed", _fake_embed)
    idx = SemanticSignalIndex()
    assert await idx.add("sig-1", "wireless earbuds", {"k": "v"}) is True
    results = await idx.search("wireless earbuds", top_k=1)
    assert results
    assert isinstance(results[0], SemanticSearchResult)
    assert results[0].signal_id == "sig-1"


@pytest.mark.skipif(not semantic_index._FAISS_AVAILABLE, reason="faiss-cpu not installed")
async def test_exact_match_is_duplicate(monkeypatch):
    async def _fake_embed(self, text):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.standard_normal(semantic_index._DIM).astype(np.float32)
        return v / np.linalg.norm(v)

    monkeypatch.setattr(SemanticSignalIndex, "_embed", _fake_embed)
    idx = SemanticSignalIndex()
    await idx.add("sig-1", "identical text here")
    is_dup, sid = await idx.is_duplicate("identical text here", threshold=0.9)
    assert is_dup is True
    assert sid == "sig-1"


def test_module_singleton_stable():
    assert get_signal_index() is get_signal_index()
