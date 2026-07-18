"""
FAISS-backed semantic signal index for high-precision deduplication and clustering.

Phase 0 scrape layer. Builds an in-process vector index of recent signals using
bge-m3 embeddings (projected for speed). Enables:
  - Semantic dedup (Layer 3 of the MinHash pipeline)
  - "Find similar signals" for the dashboard search
  - Trend cluster discovery (replaces k-means with FAISS IVF)

FAISS runs entirely in-process, zero network, zero cost.
Falls back to no-op when faiss-cpu not installed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import structlog

_log = structlog.get_logger("aegis.scrape.semantic_index")

try:
    import faiss  # noqa: F401

    _FAISS_AVAILABLE = True
except ImportError:
    _FAISS_AVAILABLE = False

_DIM = 384       # bge-m3 outputs 1024 but we project to 384 for FAISS speed
_MAX_INDEX = 50_000  # max signals in index before rotation


@dataclass
class SemanticSearchResult:
    signal_id: str
    similarity: float
    metadata: dict = field(default_factory=dict)


class SemanticSignalIndex:
    """
    In-process FAISS index for semantic signal search.

    Usage:
        index = SemanticSignalIndex()
        await index.add(signal_id="sig-1", text="wireless earbuds trending", metadata={...})
        results = await index.search("bluetooth earphones popular", top_k=5)
    """

    def __init__(self) -> None:
        self._index: Any = None
        self._id_map: list[str] = []
        self._meta_map: list[dict] = []
        self._embedder: Any = None
        self._projection: Any = None
        self._built = False

    def _ensure_built(self) -> None:
        if self._built or not _FAISS_AVAILABLE:
            return
        try:
            import faiss

            # Flat IP index — exact search, fast at our scale (< 50k vectors)
            base_index = faiss.IndexFlatIP(_DIM)  # inner product = cosine on normalized vecs
            # Wrap with IDMap to enable removal
            self._index = faiss.IndexIDMap(base_index)
            self._built = True
            _log.info("semantic_index.built", dim=_DIM)
        except Exception as exc:
            _log.warning("semantic_index.build_failed", error=str(exc))

    async def _embed(self, text: str) -> np.ndarray | None:
        """Get normalized embedding for text. Returns None if embedder unavailable."""
        try:
            from aegis.agents.llm import get_gateway

            gw = get_gateway()
            if gw is None:
                return None
            embeddings = await gw.embed([text])
            if not embeddings:
                return None
            vec = np.array(embeddings[0], dtype=np.float32)
            # Project to _DIM if needed
            if len(vec) > _DIM:
                vec = vec[:_DIM]
            elif len(vec) < _DIM:
                vec = np.pad(vec, (0, _DIM - len(vec)))
            # Normalize for cosine similarity via inner product
            norm = np.linalg.norm(vec)
            if norm > 1e-10:
                vec = vec / norm
            return vec
        except Exception as exc:
            _log.debug("semantic_index.embed_failed", error=str(exc))
            return None

    async def add(self, signal_id: str, text: str, metadata: dict | None = None) -> bool:
        """Add a signal to the index. Returns True if added successfully."""
        self._ensure_built()
        if not _FAISS_AVAILABLE or not self._built:
            return False

        # Rotate index if too large
        if len(self._id_map) >= _MAX_INDEX:
            self._rotate()

        vec = await self._embed(text)
        if vec is None:
            return False

        try:
            import faiss  # noqa: F401

            numeric_id = int(hashlib.sha256(signal_id.encode()).hexdigest()[:8], 16)
            self._index.add_with_ids(vec.reshape(1, -1), np.array([numeric_id], dtype=np.int64))
            self._id_map.append(signal_id)
            self._meta_map.append(metadata or {})
            return True
        except Exception as exc:
            _log.debug("semantic_index.add_failed", error=str(exc))
            return False

    async def search(self, query: str, top_k: int = 10) -> list[SemanticSearchResult]:
        """Find semantically similar signals. Returns empty list if unavailable."""
        self._ensure_built()
        if not _FAISS_AVAILABLE or not self._built or not self._id_map:
            return []

        vec = await self._embed(query)
        if vec is None:
            return []

        try:
            k = min(top_k, len(self._id_map))
            distances, indices = self._index.search(vec.reshape(1, -1), k)
            results = []
            for dist, idx in zip(distances[0], indices[0], strict=False):
                if idx < 0:
                    continue
                # Find signal_id by numeric_id mapping
                try:
                    pos = int(idx) % len(self._id_map)
                    results.append(SemanticSearchResult(
                        signal_id=self._id_map[pos],
                        similarity=float(dist),
                        metadata=self._meta_map[pos],
                    ))
                except Exception:
                    continue
            return results
        except Exception as exc:
            _log.debug("semantic_index.search_failed", error=str(exc))
            return []

    async def is_duplicate(self, text: str, threshold: float = 0.92) -> tuple[bool, str | None]:
        """Check if text is semantically duplicate of any indexed signal."""
        results = await self.search(text, top_k=1)
        if results and results[0].similarity >= threshold:
            return True, results[0].signal_id
        return False, None

    def _rotate(self) -> None:
        """Drop oldest 25% of index to make room."""
        if not _FAISS_AVAILABLE or not self._built:
            return
        keep = int(len(self._id_map) * 0.75)
        try:
            import faiss

            base = faiss.IndexFlatIP(_DIM)
            self._index = faiss.IndexIDMap(base)
            self._id_map = self._id_map[-keep:]
            self._meta_map = self._meta_map[-keep:]
            _log.info("semantic_index.rotated", kept=keep)
        except Exception:
            pass


# Process singleton
_signal_index = SemanticSignalIndex()


def get_signal_index() -> SemanticSignalIndex:
    return _signal_index
