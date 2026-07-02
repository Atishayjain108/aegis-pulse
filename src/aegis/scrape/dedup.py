"""
Signal deduplication pipeline (async, swarm-scale).

Phase 0 scrape layer. Three-layer pipeline: exact token -> MinHash LSH ->
(optional) semantic. Replaces O(n^2) difflib with O(n log n) MinHash for
swarm-scale deduplication.

This is the async, Redis-cache-aware entry point specified by GODMODE PASS 2-2A.
The synchronous, in-process-LRU variant lives in :mod:`aegis.db.dedup` and is the
path used inside ``asyncio.to_thread`` by the scrape topic pipeline (invariant
§12). Both share the same 3-layer doctrine and ``DedupResult`` shape; this module
adds the Redis-backed MinHash cache + async surface the swarm orchestrator uses.
"""

from __future__ import annotations

import contextlib
import hashlib
import pickle
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

_log = structlog.get_logger("aegis.scrape.dedup")

# MinHash + LSH (datasketch — optional import, graceful fallback)
try:
    from datasketch import MinHash, MinHashLSH

    _MINHASH_AVAILABLE = True
except ImportError:
    _MINHASH_AVAILABLE = False
    MinHash = None  # type: ignore[assignment,misc]
    MinHashLSH = None  # type: ignore[assignment,misc]


# Shared permutation table: MinHash.__init__ regenerates 2xnum_perm random
# vectors per instance (~1ms each) unless given precomputed permutations.
# All signatures share the same permutations to be comparable — reusing them
# turns per-signal MinHash construction from ~1.5ms into a near-free op.
_SHARED_PERMUTATIONS: Any = None


def _shared_permutations(n_perm: int) -> Any:
    global _SHARED_PERMUTATIONS  # noqa: PLW0603 - lazy module-level singleton
    if _SHARED_PERMUTATIONS is None and _MINHASH_AVAILABLE:
        _SHARED_PERMUTATIONS = MinHash(num_perm=n_perm).permutations
    return _SHARED_PERMUTATIONS


@dataclass
class DedupResult:
    """Result of deduplication for a single signal."""

    is_duplicate: bool
    dedup_method: Literal["exact", "minhash", "semantic", "pass"]
    matched_id: str | None = None
    similarity: float = 0.0


@dataclass
class MinHashLayer:
    """
    Layer 2 of the dedup pipeline: MinHash + LSH near-duplicate detection.

    Character 3-gram shingles over lowercased title+url -> MinHash(128 permutations)
    -> LSH forest (Jaccard threshold 0.75). Redis-backed hash cache (TTL 24h).
    """

    n_perm: int = 128
    threshold: float = 0.75
    _lsh: Any = field(default=None, init=False, repr=False)
    _session_index: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if _MINHASH_AVAILABLE:
            self._lsh = MinHashLSH(threshold=self.threshold, num_perm=self.n_perm)

    def _make_shingles(self, text: str) -> set[str]:
        """3-gram character shingles over lowercase text."""
        t = re.sub(r"\s+", " ", text.lower().strip())
        if len(t) < 3:
            return {t}
        return {t[i : i + 3] for i in range(len(t) - 2)}

    def _build_minhash(self, text: str) -> Any | None:
        if not _MINHASH_AVAILABLE:
            return None
        m = MinHash(num_perm=self.n_perm, permutations=_shared_permutations(self.n_perm))
        # update_batch vectorises hashing across all shingles — ~10x faster than
        # per-shingle update() on 1000-signal batches.
        m.update_batch([s.encode("utf-8") for s in self._make_shingles(text)])
        return m

    async def get_or_build(self, content_hash: str, text: str, redis: Any | None) -> Any | None:
        """Return cached MinHash or compute + cache it."""
        if not _MINHASH_AVAILABLE:
            return None
        cache_key = f"aegis:dedup:mh:{content_hash[:16]}"
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    return pickle.loads(cached)  # noqa: S301 - trusted self-written cache
            except Exception:
                pass  # Redis unavailable / corrupt entry -> compute fresh
        m = self._build_minhash(text)
        if m is not None and redis:
            with contextlib.suppress(Exception):
                await redis.set(cache_key, pickle.dumps(m), ex=86400)
        return m

    def is_duplicate(self, signal_id: str, m: Any) -> tuple[bool, str | None]:
        """Check LSH index. Insert if not duplicate. Returns (is_dup, matched_id)."""
        if not _MINHASH_AVAILABLE or self._lsh is None or m is None:
            return False, None
        try:
            matches = self._lsh.query(m)
            matches = [x for x in matches if x != signal_id]
            if matches:
                return True, matches[0]
        except Exception:
            pass
        # Not a duplicate — add to index for future checks
        try:
            if signal_id not in self._session_index:
                self._lsh.insert(signal_id, m)
                self._session_index[signal_id] = m
        except Exception:
            pass
        return False, None


async def deduplicate_batch(
    signals: list[dict],
    *,
    redis: Any | None = None,
    semantic_enabled: bool = False,
) -> tuple[list[dict], list[DedupResult]]:
    """
    Deduplicate a batch of signals through the 3-layer pipeline.

    Returns (unique_signals, results) where results[i] corresponds to signals[i].
    Performance target: 1000 signals < 500ms on CPU.
    """
    if not signals:
        return [], []

    layer2 = MinHashLayer()
    results: list[DedupResult] = []
    seen_tokens: dict[frozenset, str] = {}
    unique: list[dict] = []

    for sig in signals:
        sid = sig.get("url") or sig.get("id") or str(id(sig))
        title = str(sig.get("title", ""))
        url = str(sig.get("url", ""))
        content = f"{title} {url}".strip()
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        # Layer 1: token Jaccard
        token_set = frozenset(title.lower().split())
        dup_id = None
        for existing_tokens, existing_id in seen_tokens.items():
            if len(token_set) == 0 or len(existing_tokens) == 0:
                continue
            jaccard = len(token_set & existing_tokens) / len(token_set | existing_tokens)
            if jaccard >= 0.85:
                dup_id = existing_id
                break
        if dup_id:
            results.append(DedupResult(True, "exact", dup_id, 1.0))
            continue

        # Layer 2: MinHash LSH
        if _MINHASH_AVAILABLE:
            mhash = await layer2.get_or_build(content_hash, content, redis)
            is_dup, matched = layer2.is_duplicate(sid, mhash)
            if is_dup:
                results.append(DedupResult(True, "minhash", matched))
                continue

        # Layer 3: semantic (optional, expensive) — gated, runs only when an
        # embedding gateway is available. Uses the FAISS-backed SemanticSignalIndex
        # (Pass 9B); falls back silently to a pass-through when faiss / the embedder
        # is absent so the swarm path never blocks fatally.
        if semantic_enabled:
            try:
                from aegis.scrape.semantic_index import get_signal_index

                index = get_signal_index()
                is_dup, matched = await index.is_duplicate(content)
                if is_dup:
                    results.append(DedupResult(True, "semantic", matched))
                    continue
                await index.add(sid, content, {"title": title, "url": url})
            except Exception:  # semantic layer is best-effort
                pass

        seen_tokens[token_set] = sid
        results.append(DedupResult(False, "pass"))
        unique.append(sig)

    _log.debug(
        "dedup.batch_complete",
        total=len(signals),
        unique=len(unique),
        dup_exact=sum(1 for r in results if r.dedup_method == "exact"),
        dup_minhash=sum(1 for r in results if r.dedup_method == "minhash"),
    )
    return unique, results
