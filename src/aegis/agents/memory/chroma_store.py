"""
Per-agent semantic memory backed by ChromaDB.

Each agent has its own collection (e.g. `agent.scout.memories`).
On ingest, we embed the text via a sentence-transformers model
(local, default: BAAI/bge-m3-small or the BGE-M3 family). On
retrieval, we cosine-search top-k.

If sentence-transformers is unavailable (CPU-only, model not
downloaded, network-restricted), we fall back to a deterministic
hashing-based pseudo-embedding so unit tests still pass and the
system stays functional. Retrieval quality drops, but the
HISTORIAN agent will surface this in its confidence score and
the heuristic path stays correct.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

_log = structlog.get_logger("aegis.agents.memory.chroma")


# Embedding model state stored in a container to avoid PLW0603
# (global statement discouraged). Never replace the container itself.
class _EmbedState:
    model: Any | None = None
    attempted: bool = False
    lock: threading.Lock = threading.Lock()


_embed_state = _EmbedState()
_DEFAULT_ST_MODEL = os.environ.get("AEGIS_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
_FALLBACK_DIM = 384  # match bge-small dim so collections stay compatible


def _try_load_sentence_transformers() -> Any | None:
    """Lazy-load the embedding model exactly once per process."""
    if _embed_state.attempted:
        return _embed_state.model
    with _embed_state.lock:
        if _embed_state.attempted:
            return _embed_state.model
        _embed_state.attempted = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

            _embed_state.model = SentenceTransformer(_DEFAULT_ST_MODEL)
            _log.info("memory.embed.model_loaded", model=_DEFAULT_ST_MODEL)
        except Exception as exc:
            _log.warning(
                "memory.embed.fallback_to_hash",
                error=str(exc),
                note="install sentence-transformers for real embeddings",
            )
            _embed_state.model = None
    return _embed_state.model


def _hash_embedding(text: str, dim: int = _FALLBACK_DIM) -> list[float]:
    """Deterministic, low-quality embedding for fallback / tests.

    Method: take SHA256 of (text || i) for i in [0, dim/8), unpack
    each byte to a [0,1] float, then L2-normalize. Stable across
    runs and Python versions.
    """
    raw_bytes = bytearray()
    chunks = (dim // 8) + 1
    for i in range(chunks):
        h = hashlib.sha256(f"{i}:{text}".encode()).digest()
        raw_bytes.extend(h)
    floats = [b / 255.0 - 0.5 for b in raw_bytes[:dim]]
    norm_sq = sum(f * f for f in floats)
    norm = norm_sq**0.5 if norm_sq > 0 else 1.0
    return [f / norm for f in floats]


def _embed_one(text: str) -> list[float]:
    model = _try_load_sentence_transformers()
    if model is None:
        return _hash_embedding(text)
    try:
        vec = model.encode(text, normalize_embeddings=True)
        # numpy ndarray → list[float]
        return [float(x) for x in vec]
    except Exception:  # pragma: no cover — encode() failures are optional-dep dependent
        _log.exception("memory.embed.encode_failed")
        return _hash_embedding(text)


def _embed_many(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = _try_load_sentence_transformers()
    if model is None:
        return [_hash_embedding(t) for t in texts]
    try:
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)
        return [[float(x) for x in v] for v in vecs]
    except Exception:  # pragma: no cover — batch encode failures are optional-dep dependent
        _log.exception("memory.embed.batch_failed")
        return [_hash_embedding(t) for t in texts]


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One row of agent memory."""

    id: str
    text: str
    metadata: dict[str, Any]
    score: float = 0.0  # populated on query


class ChromaMemoryStore:
    """Per-agent ChromaDB collection wrapper.

    Note: Chroma's Python client is synchronous; we run all calls in
    a thread pool to keep the event loop free. Use one instance per
    agent (or pass `agent` per call).
    """

    def __init__(
        self,
        *,
        persist_dir: str | None = None,
        agent: str = "default",
        client: Any | None = None,
    ) -> None:
        self.agent = agent
        self.persist_dir = persist_dir or os.environ.get(
            "AEGIS_CHROMA_DIR", "/var/lib/aegis/chroma"
        )
        self._client = client  # injected for tests; lazy otherwise
        self._collection: Any | None = None
        self._init_lock = asyncio.Lock()

    async def _ensure_collection(self) -> Any | None:
        """Lazy collection bootstrap. Returns None if Chroma is missing."""
        if self._collection is not None:
            return self._collection
        async with self._init_lock:
            if self._collection is not None:
                return self._collection
            client = self._client
            if client is None:
                try:
                    import chromadb  # type: ignore[import-not-found]
                except ImportError:
                    _log.warning(
                        "memory.chroma_unavailable",
                        agent=self.agent,
                        note="install chromadb or memory becomes a no-op",
                    )
                    return None
                try:
                    Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
                    client = await asyncio.to_thread(
                        chromadb.PersistentClient, path=self.persist_dir
                    )
                except Exception:  # pragma: no cover — chromadb init errors are external
                    _log.exception("memory.chroma_init_failed", agent=self.agent)
                    return None
                self._client = client

            collection_name = f"agent_{self.agent}_memories"
            try:
                self._collection = await asyncio.to_thread(
                    client.get_or_create_collection,
                    collection_name,
                    metadata={"agent": self.agent, "created_by": "aegis-phase2"},
                )
            except Exception:  # pragma: no cover — collection creation errors are external
                _log.exception("memory.chroma_collection_failed", agent=self.agent)
                return None
        return self._collection

    async def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> str | None:
        """Insert one memory. Returns the assigned id, or None on failure."""
        coll = await self._ensure_collection()
        if coll is None:
            return None
        rid = record_id or str(uuid.uuid4())
        emb = await asyncio.to_thread(_embed_one, text)
        # Chroma rejects non-primitive metadata values.
        meta = _sanitize_metadata(metadata or {})
        try:
            await asyncio.to_thread(
                coll.add,
                ids=[rid],
                documents=[text],
                embeddings=[emb],
                metadatas=[meta],
            )
        except Exception:
            _log.exception("memory.add_failed", agent=self.agent, id=rid)
            return None
        return rid

    async def add_batch(self, items: Iterable[tuple[str, dict[str, Any]]]) -> int:
        """Bulk insert. Returns count successfully written."""
        coll = await self._ensure_collection()
        if coll is None:
            return 0
        items_list = list(items)
        if not items_list:
            return 0
        ids = [str(uuid.uuid4()) for _ in items_list]
        texts = [t for t, _ in items_list]
        metas = [_sanitize_metadata(m) for _, m in items_list]
        embs = await asyncio.to_thread(_embed_many, texts)
        try:
            await asyncio.to_thread(
                coll.add,
                ids=ids,
                documents=texts,
                embeddings=embs,
                metadatas=metas,
            )
        except Exception:
            _log.exception("memory.add_batch_failed", agent=self.agent, count=len(items_list))
            return 0
        return len(items_list)

    async def query(
        self,
        query_text: str,
        *,
        k: int = 5,
        filter_: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        """Top-k similarity search. Returns [] if Chroma is unavailable."""
        coll = await self._ensure_collection()
        if coll is None:
            return []
        emb = await asyncio.to_thread(_embed_one, query_text)
        try:
            res = await asyncio.to_thread(
                coll.query,
                query_embeddings=[emb],
                n_results=int(k),
                where=filter_ or None,
            )
        except Exception:
            _log.exception("memory.query_failed", agent=self.agent)
            return []

        # Chroma returns parallel lists indexed by [query_idx][result_idx].
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]

        out: list[MemoryRecord] = []
        for i, _id in enumerate(ids):
            doc = docs[i] if i < len(docs) else ""
            meta = metas[i] if i < len(metas) and metas[i] else {}
            dist = dists[i] if i < len(dists) else 0.0
            score = max(0.0, min(1.0, 1.0 - float(dist)))
            out.append(MemoryRecord(id=_id, text=doc or "", metadata=meta or {}, score=score))
        return out

    async def count(self) -> int:
        coll = await self._ensure_collection()
        if coll is None:
            return 0
        try:
            return int(await asyncio.to_thread(coll.count))
        except Exception:  # pragma: no cover
            return 0


def _sanitize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Chroma only accepts str/int/float/bool/None metadata values.

    Convert everything else to strings, dropping keys that are
    unrepresentable.
    """
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if v is None or isinstance(v, str | int | float | bool):
            out[str(k)] = v
        else:
            try:
                out[str(k)] = str(v)
            except Exception:  # pragma: no cover — str() on unknown types
                continue
    return out
