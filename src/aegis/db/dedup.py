"""Semantic deduplication for signals.

PASS2-2A three-layer pipeline (Phase 0/1 boundary):

1. **Exact dedup** — identical token sets (same words, any order). The DB
   ``ON CONFLICT`` clause separately handles identical (platform, url) rows.

2. **Near-dup (MinHash + LSH)** — character 3-gram MinHash (128 permutations)
   with an LSH index retrieves candidate matches in O(n log n); each candidate
   is then *confirmed* with the precise token-Jaccard / SequenceMatcher
   similarity, so dedup decisions are identical to the historical O(n²) scan,
   just without scanning every pair. Falls back to the full pairwise scan when
   ``datasketch`` is not installed.

3. **Semantic (optional)** — BGE-M3 embeddings via the Phase 11 LLMGateway,
   cosine > 0.92 → paraphrase duplicate. Gated by
   ``AEGIS_DEDUP_SEMANTIC_ENABLED=true`` (default off) and only runs inside
   the async :func:`deduplicate_signals`; skips silently when the gateway or
   Ollama is unreachable.

Deviation from the GODMODE spec: MinHash signatures are cached in a bounded
in-process LRU rather than Redis — :func:`deduplicate_batch` is synchronous
(it runs inside ``asyncio.to_thread`` per invariant §12), so an async Redis
round-trip is not available on this path.

The threshold is tunable; the default 0.82 catches rewrites like:
    "NVIDIA posts record quarterly revenue" vs
    "Nvidia reports record quarterly earnings"
while letting through genuinely distinct stories at 0.70 similarity.

Usage::

    from aegis.db.dedup import deduplicate_signals

    unique, dropped = await deduplicate_signals(pool, tenant_id, new_signals)
    await insert_signals(pool, unique, tenant_id=tenant_id)
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    from uuid import UUID

    from aegis.db.pool import PgPool
    from aegis.schemas.signal import ProductSignal

_log = structlog.get_logger("aegis.db.dedup")

# MinHash + LSH (datasketch) — optional import, graceful fallback to the
# historical pairwise scan when absent.
try:
    from datasketch import MinHash, MinHashLSH

    _MINHASH_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on env
    _MINHASH_AVAILABLE = False
    MinHash = None  # type: ignore[assignment,misc]
    MinHashLSH = None  # type: ignore[assignment,misc]

# MinHash tuning: 128 permutations; LSH retrieval threshold is set BELOW the
# confirm threshold so the index over-fetches candidates and the precise
# similarity check makes the final call (recall over precision at this stage).
_MINHASH_PERMS = 128
_LSH_RETRIEVAL_THRESHOLD = 0.5

# Bounded in-process MinHash cache: content-hash[:16] → MinHash.
_MINHASH_CACHE: OrderedDict[str, Any] = OrderedDict()
_MINHASH_CACHE_MAX = 8192

# Shared permutation table: MinHash.__init__ regenerates 2×num_perm random
# vectors per instance (~1ms each) unless given precomputed permutations.
# All signatures MUST share the same permutations to be comparable anyway.
_SHARED_PERMUTATIONS: Any = None


def _shared_permutations() -> Any:
    global _SHARED_PERMUTATIONS  # noqa: PLW0603 - lazy module-level singleton
    if _SHARED_PERMUTATIONS is None:
        _SHARED_PERMUTATIONS = MinHash(num_perm=_MINHASH_PERMS).permutations
    return _SHARED_PERMUTATIONS

_SWEEP_BATCH_LIMIT = 10_000
_SWEEP_COMPARE_WINDOW = 500

# Stopwords stripped before similarity comparison — they add noise to Jaccard.
_STOPWORDS = frozenset(
    ["a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would", "could", "should", "may", "might", "shall", "can", "need", "dare", "used", "to", "of", "in", "on", "at", "by", "for", "with", "about", "against", "between", "into", "through", "during", "before", "after", "above", "below", "to", "from", "up", "down", "out", "off", "over", "under", "again", "further", "then", "once", "here", "there", "when", "where", "why", "how", "all", "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only", "same", "so", "than", "too", "very", "just"]
)


def _tokenise(text: str) -> frozenset[str]:
    """Lower-case, strip punctuation, remove stopwords → token set."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(t for t in tokens if t not in _STOPWORDS and len(t) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _sequence_sim(a: str, b: str) -> float:
    """SequenceMatcher ratio — catches word-order rewrites better than Jaccard."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _title_similarity(title_a: str, title_b: str) -> float:
    """Combined similarity: max of Jaccard(tokens) and SequenceMatcher(chars).

    Taking the max means either token-overlap OR character-overlap is enough
    to flag a near-duplicate — catches both paraphrase and typographic variants.
    """
    toks_a = _tokenise(title_a)
    toks_b = _tokenise(title_b)
    return max(_jaccard(toks_a, toks_b), _sequence_sim(title_a, title_b))


async def _fetch_recent_titles(
    pool: PgPool,
    tenant_id: UUID,
    *,
    lookback_hours: int,
) -> list[tuple[str, str]]:
    """Fetch (signal_id, title) for signals inserted within ``lookback_hours``."""
    async with pool.acquire(tenant_id=tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT signal_id::text, title
            FROM   signals
            WHERE  tenant_id = $1
              AND  title IS NOT NULL
              AND  scraped_at >= NOW() - ($2 || ' hours')::INTERVAL
            ORDER  BY scraped_at DESC
            LIMIT  5000
            """,
            tenant_id,
            str(lookback_hours),
        )
    return [(str(r["signal_id"]), r["title"]) for r in rows if r["title"]]


@dataclass
class DedupResult:
    """Per-signal outcome of the PASS2-2A dedup pipeline."""

    is_duplicate: bool
    dedup_method: Literal["exact", "minhash", "semantic", "pass"]
    matched_title: str | None = None
    similarity: float = 0.0


def _sig_title(sig: Any) -> str:
    """Duck-typed title accessor — ProductSignal attr or plain dict."""
    if isinstance(sig, dict):
        return str(sig.get("title") or "")
    return str(getattr(sig, "title", None) or "")


def _sig_url(sig: Any) -> str:
    """Duck-typed URL accessor — ProductSignal attr or plain dict."""
    if isinstance(sig, dict):
        return str(sig.get("url") or "").strip()
    return str(getattr(sig, "url", None) or "").strip()


async def _fetch_recent_urls(
    pool: PgPool,
    tenant_id: UUID,
    *,
    lookback_hours: int,
) -> set[str]:
    """Fetch the set of exact URLs seen within ``lookback_hours``.

    The TimescaleDB unique constraint is ``(platform, external_id, ts)`` — ts is
    in the key because the partition column must be, so a product re-scraped on a
    later day always gets a fresh ts and re-inserts. This exact-URL set lets the
    dedup pass drop those same-window re-observations that the (ts-bound)
    constraint and the fuzzy-title pass both miss.
    """
    async with pool.acquire(tenant_id=tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT url
            FROM   signals
            WHERE  tenant_id = $1
              AND  url IS NOT NULL
              AND  scraped_at >= NOW() - ($2 || ' hours')::INTERVAL
            LIMIT  20000
            """,
            tenant_id,
            str(lookback_hours),
        )
    return {str(r["url"]).strip() for r in rows if r["url"]}


def _shingles(text: str) -> set[str]:
    """Character 3-gram shingles over whitespace-normalised lowercase text."""
    t = re.sub(r"\s+", " ", text.lower().strip())
    if len(t) < 3:
        return {t} if t else set()
    return {t[i : i + 3] for i in range(len(t) - 2)}


def _get_or_build_minhash(title: str) -> Any | None:
    """Return a (possibly cached) MinHash for *title*.

    Cache is a bounded in-process LRU keyed by sha256(title)[:16] — see the
    module docstring for why this is not Redis-backed on the sync path.
    """
    if not _MINHASH_AVAILABLE:
        return None
    key = hashlib.sha256(title.encode()).hexdigest()[:16]
    cached = _MINHASH_CACHE.get(key)
    if cached is not None:
        _MINHASH_CACHE.move_to_end(key)
        return cached
    m = MinHash(num_perm=_MINHASH_PERMS, permutations=_shared_permutations())
    # update_batch vectorises hashing across all shingles — ~10× faster than
    # per-shingle update() calls on 1000-signal batches.
    m.update_batch([s.encode("utf-8") for s in _shingles(title)])
    _MINHASH_CACHE[key] = m
    if len(_MINHASH_CACHE) > _MINHASH_CACHE_MAX:
        _MINHASH_CACHE.popitem(last=False)
    return m


def clear_minhash_cache() -> None:
    """Test/maintenance hook: drop all cached MinHash signatures."""
    _MINHASH_CACHE.clear()


def deduplicate_batch_detailed(
    signals: list[Any],
    *,
    threshold: float = 0.82,
) -> tuple[list[Any], list[DedupResult]]:
    """Three-layer intra-batch dedup with per-signal method attribution.

    Layer 1: identical token sets → ``exact``.
    Layer 2: MinHash LSH candidate retrieval + precise similarity confirm
             → ``minhash`` (falls back to the full pairwise scan without
             datasketch — same decisions, O(n²)).
    Untitled signals always pass.

    Returns (kept_signals, results) where results[i] corresponds to
    signals[i]. Keeps the first occurrence of each near-duplicate cluster.
    """
    if not signals:
        return [], []

    use_lsh = _MINHASH_AVAILABLE
    lsh = MinHashLSH(threshold=_LSH_RETRIEVAL_THRESHOLD, num_perm=_MINHASH_PERMS) if use_lsh else None

    kept: list[ProductSignal] = []
    results: list[DedupResult] = []
    # Layer 1 index: exact token-set → first title seen.
    exact_index: dict[frozenset[str], str] = {}
    # Layer 2 state: key → (tokens, title); keys are "k{i}" LSH labels.
    kept_titles: dict[str, tuple[frozenset[str], str]] = {}

    for sig in signals:
        title = _sig_title(sig)
        if not title:
            kept.append(sig)
            results.append(DedupResult(False, "pass"))
            continue

        toks = _tokenise(title)

        # ---- Layer 1: exact token-set duplicate (same words, any order).
        prior = exact_index.get(toks)
        if prior is not None:
            results.append(DedupResult(True, "exact", prior, 1.0))
            _log.debug("dedup.batch_duplicate", title=title[:80], existing=prior[:80], layer="exact")
            continue

        # ---- Layer 2: near-duplicate.
        matched: tuple[str, float] | None = None
        if use_lsh and lsh is not None:
            mhash = _get_or_build_minhash(title)
            candidate_keys = lsh.query(mhash) if mhash is not None else []
            for ck in candidate_keys:
                c_toks, c_title = kept_titles[ck]
                sim = max(_jaccard(toks, c_toks), _sequence_sim(title, c_title))
                if sim >= threshold:
                    matched = (c_title, sim)
                    break
        else:
            for c_toks, c_title in kept_titles.values():
                sim = max(_jaccard(toks, c_toks), _sequence_sim(title, c_title))
                if sim >= threshold:
                    matched = (c_title, sim)
                    break

        if matched is not None:
            results.append(DedupResult(True, "minhash", matched[0], round(matched[1], 4)))
            _log.debug(
                "dedup.batch_duplicate",
                title=title[:80],
                existing=matched[0][:80],
                similarity=round(matched[1], 3),
                layer="minhash",
            )
            continue

        # Not a duplicate — index it for subsequent signals.
        key = f"k{len(kept_titles)}"
        exact_index[toks] = title
        kept_titles[key] = (toks, title)
        if use_lsh and lsh is not None:
            mhash = _get_or_build_minhash(title)
            if mhash is not None:
                lsh.insert(key, mhash)
        kept.append(sig)
        results.append(DedupResult(False, "pass"))

    _log.debug(
        "dedup.batch_complete",
        total=len(signals),
        unique=len(kept),
        dup_exact=sum(1 for r in results if r.dedup_method == "exact"),
        dup_minhash=sum(1 for r in results if r.dedup_method == "minhash"),
        lsh=use_lsh,
    )
    return kept, results


def deduplicate_batch(
    signals: list[ProductSignal],
    *,
    threshold: float = 0.82,
) -> tuple[list[ProductSignal], int]:
    """Remove near-duplicates WITHIN the incoming batch itself (no DB needed).

    Returns (deduplicated_list, dropped_count).
    Keeps the first occurrence of each near-duplicate cluster.
    Thin wrapper over :func:`deduplicate_batch_detailed`.
    """
    kept, results = deduplicate_batch_detailed(signals, threshold=threshold)
    return kept, sum(1 for r in results if r.is_duplicate)


_SEMANTIC_COSINE_THRESHOLD = 0.92


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=False))
    da = sum(x * x for x in a) ** 0.5
    db = sum(y * y for y in b) ** 0.5
    if da == 0.0 or db == 0.0:
        return 0.0
    return num / (da * db)


async def _semantic_dedup_pass(
    signals: list[ProductSignal],
) -> tuple[list[ProductSignal], int]:
    """PASS2-2A Layer 3: paraphrase dedup via BGE-M3 embeddings (optional).

    Gated by ``AEGIS_DEDUP_SEMANTIC_ENABLED=true``. Embeds each title through
    the Phase 11 LLMGateway and drops signals whose embedding cosine against
    an earlier kept signal exceeds 0.92. Any failure (gateway absent, Ollama
    unreachable, embed error) skips the layer entirely — never raises.
    """
    if os.environ.get("AEGIS_DEDUP_SEMANTIC_ENABLED", "false").lower() != "true":
        return signals, 0
    if len(signals) < 2:
        return signals, 0
    try:
        from aegis.llm.bridge.agents_bridge import get_gateway

        gateway = get_gateway()
        if gateway is None:
            return signals, 0
        kept: list[ProductSignal] = []
        kept_vecs: list[list[float]] = []
        dropped = 0
        for sig in signals:
            title = _sig_title(sig)
            if not title:
                kept.append(sig)
                continue
            vec = await gateway.embed(title)
            if not vec:
                kept.append(sig)
                continue
            if any(_cosine(vec, kv) > _SEMANTIC_COSINE_THRESHOLD for kv in kept_vecs):
                dropped += 1
                _log.debug("dedup.semantic_duplicate", title=title[:80])
                continue
            kept.append(sig)
            kept_vecs.append(vec)
        if dropped:
            _log.info("dedup.semantic_pass", dropped=dropped, kept=len(kept))
        return kept, dropped
    except Exception as exc:
        _log.debug("dedup.semantic_skipped", error=str(exc))
        return signals, 0


async def deduplicate_signals(
    pool: PgPool,
    tenant_id: UUID,
    signals: list[ProductSignal],
    *,
    threshold: float = 0.82,
    lookback_hours: int = 72,
) -> tuple[list[ProductSignal], int]:
    """Remove signals that are semantically duplicate of recent DB entries.

    Step 1: dedup within the incoming batch (exact + MinHash layers).
    Step 2: dedup against recent DB signals (last ``lookback_hours`` hours).
    Step 3: optional semantic (embedding) layer — see _semantic_dedup_pass.

    Returns (unique_signals, total_dropped_count).
    """
    if not signals:
        return [], 0

    # Step 1: intra-batch dedup
    signals, intra_dropped = deduplicate_batch(signals, threshold=threshold)

    # Step 1b: exact-URL dedup against the DB. The (platform, external_id, ts)
    # unique constraint cannot dedup across time (ts is in the key), so a
    # product re-scraped on a later day always re-inserts. This precise pass
    # drops same-window re-observations the fuzzy-title pass misses — it was the
    # cause of the ~11% duplicate-URL bloat (Amazon bestsellers re-scraped daily).
    url_dropped = 0
    try:
        recent_urls = await _fetch_recent_urls(
            pool, tenant_id, lookback_hours=lookback_hours
        )
    except Exception as exc:
        _log.warning("dedup.url_fetch_failed", error=str(exc))
        recent_urls = set()
    if recent_urls:
        kept: list[ProductSignal] = []
        seen_in_batch: set[str] = set()
        for sig in signals:
            u = _sig_url(sig)
            if u and (u in recent_urls or u in seen_in_batch):
                url_dropped += 1
                continue
            if u:
                seen_in_batch.add(u)
            kept.append(sig)
        signals = kept

    # Step 2: compare against recent DB titles
    try:
        recent = await _fetch_recent_titles(pool, tenant_id, lookback_hours=lookback_hours)
    except Exception as exc:
        _log.warning("dedup.db_fetch_failed", error=str(exc))
        return signals, intra_dropped + url_dropped

    if not recent:
        return signals, intra_dropped

    # Pre-tokenise the DB titles once
    db_index: list[tuple[frozenset[str], str]] = [
        (_tokenise(title), title) for _, title in recent
    ]

    unique: list[ProductSignal] = []
    inter_dropped = 0

    for sig in signals:
        title = sig.title or ""
        if not title:
            unique.append(sig)
            continue

        toks = _tokenise(title)
        is_dup = any(
            max(_jaccard(toks, db_toks), _sequence_sim(title, db_title)) >= threshold
            for db_toks, db_title in db_index
        )

        if is_dup:
            inter_dropped += 1
            _log.debug("dedup.db_duplicate", title=title[:80])
        else:
            unique.append(sig)
            # Add to index so subsequent signals in this batch don't match it either
            db_index.append((toks, title))

    # Step 3: optional semantic layer (no-op unless explicitly enabled).
    unique, semantic_dropped = await _semantic_dedup_pass(unique)

    total_dropped = intra_dropped + url_dropped + inter_dropped + semantic_dropped
    _log.info(
        "dedup.complete",
        input=len(signals) + intra_dropped + url_dropped,
        unique=len(unique),
        dropped=total_dropped,
        url_dropped=url_dropped,
        semantic_dropped=semantic_dropped,
    )
    return unique, total_dropped


async def sweep_db_duplicates(
    pool: PgPool,
    tenant_id: UUID,
    *,
    lookback_hours: int = 168,
    threshold: float = 0.85,
    dry_run: bool = True,
) -> dict[str, int]:
    """Scan the DB for existing semantic duplicates and optionally remove them.

    Processes signals in ascending chronological order so the oldest version
    of each story is kept and newer duplicates are targeted for deletion.

    Uses a sliding comparison window of ``_SWEEP_COMPARE_WINDOW`` to keep
    the operation O(n × window) rather than O(n²).

    Args:
        pool: Live PgPool.
        tenant_id: RLS tenant UUID.
        lookback_hours: How far back to scan (default: 7 days).
        threshold: Similarity threshold; 0.85 catches near-identical
            rewrites while leaving genuinely distinct stories alone.
        dry_run: If True, report duplicates but do not delete them.

    Returns:
        Dict with keys: ``total_checked``, ``duplicates_found``,
        ``deleted`` (0 when dry_run=True).
    """
    _log.info(
        "sweep.starting",
        lookback_hours=lookback_hours,
        threshold=threshold,
        dry_run=dry_run,
    )

    async with pool.acquire(tenant_id=tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT signal_id::text, title, platform, scraped_at
            FROM   signals
            WHERE  tenant_id = $1
              AND  title IS NOT NULL
              AND  scraped_at >= NOW() - ($2 || ' hours')::INTERVAL
            ORDER  BY scraped_at ASC
            LIMIT  $3
            """,
            tenant_id,
            str(lookback_hours),
            _SWEEP_BATCH_LIMIT,
        )

    total_checked = len(rows)
    if total_checked == 0:
        _log.info("sweep.empty", lookback_hours=lookback_hours)
        return {"total_checked": 0, "duplicates_found": 0, "deleted": 0}

    # Greedy dedup with a sliding comparison window to bound cost
    kept_window: list[tuple[frozenset[str], str, str]] = []  # (tokens, title, signal_id)
    dup_ids: list[str] = []

    for row in rows:
        raw_title = row["title"] or ""
        sig_id = str(row["signal_id"])
        if not raw_title.strip():
            continue

        toks = _tokenise(raw_title)
        is_dup = any(
            max(_jaccard(toks, kt), _sequence_sim(raw_title, kt_str)) >= threshold
            for kt, kt_str, _ in kept_window
        )

        if is_dup:
            dup_ids.append(sig_id)
            _log.debug("sweep.duplicate_found", signal_id=sig_id, title=raw_title[:80])
        else:
            kept_window.append((toks, raw_title, sig_id))
            # Trim window to bound comparison cost
            if len(kept_window) > _SWEEP_COMPARE_WINDOW:
                kept_window.pop(0)

    duplicates_found = len(dup_ids)
    deleted = 0

    if not dry_run and dup_ids:
        # Delete in safe batches of 500 to avoid long-running transactions
        _BATCH = 500
        async with pool.acquire(tenant_id=tenant_id) as conn:
            for start in range(0, len(dup_ids), _BATCH):
                batch = dup_ids[start : start + _BATCH]
                await conn.execute(
                    "DELETE FROM signals WHERE signal_id = ANY($1::uuid[])",
                    batch,
                )
                deleted += len(batch)

    _log.info(
        "sweep.complete",
        total_checked=total_checked,
        duplicates_found=duplicates_found,
        deleted=deleted,
        dry_run=dry_run,
    )
    return {
        "total_checked": total_checked,
        "duplicates_found": duplicates_found,
        "deleted": deleted,
    }
