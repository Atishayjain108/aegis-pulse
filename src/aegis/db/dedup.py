"""Semantic deduplication for signals.

Two layers of deduplication:

1. **Exact dedup** (already handled by the DB ``ON CONFLICT`` clause) — same
   (platform, external_id, ts) triple.

2. **Semantic dedup** (this module) — two signals about the same story from
   different platforms or with slightly different titles. Uses token-level
   Jaccard similarity on the title so it works without any ML dependencies.

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

import re
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from uuid import UUID

    from aegis.db.pool import PgPool
    from aegis.schemas.signal import ProductSignal

_log = structlog.get_logger("aegis.db.dedup")

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


def deduplicate_batch(
    signals: list[ProductSignal],
    *,
    threshold: float = 0.82,
) -> tuple[list[ProductSignal], int]:
    """Remove near-duplicates WITHIN the incoming batch itself (no DB needed).

    Returns (deduplicated_list, dropped_count).
    Keeps the first occurrence of each near-duplicate cluster.
    """
    if not signals:
        return [], 0

    kept: list[ProductSignal] = []
    kept_titles: list[tuple[frozenset[str], str]] = []
    dropped = 0

    for sig in signals:
        title = sig.title or ""
        if not title:
            kept.append(sig)
            continue

        toks = _tokenise(title)
        is_dup = False
        for existing_toks, existing_title in kept_titles:
            sim = max(_jaccard(toks, existing_toks), _sequence_sim(title, existing_title))
            if sim >= threshold:
                is_dup = True
                _log.debug(
                    "dedup.batch_duplicate",
                    title=title[:80],
                    existing=existing_title[:80],
                    similarity=round(sim, 3),
                )
                break

        if is_dup:
            dropped += 1
        else:
            kept.append(sig)
            kept_titles.append((toks, title))

    return kept, dropped


async def deduplicate_signals(
    pool: PgPool,
    tenant_id: UUID,
    signals: list[ProductSignal],
    *,
    threshold: float = 0.82,
    lookback_hours: int = 72,
) -> tuple[list[ProductSignal], int]:
    """Remove signals that are semantically duplicate of recent DB entries.

    Step 1: dedup within the incoming batch.
    Step 2: dedup against recent DB signals (last ``lookback_hours`` hours).

    Returns (unique_signals, total_dropped_count).
    """
    if not signals:
        return [], 0

    # Step 1: intra-batch dedup
    signals, intra_dropped = deduplicate_batch(signals, threshold=threshold)

    # Step 2: compare against recent DB titles
    try:
        recent = await _fetch_recent_titles(pool, tenant_id, lookback_hours=lookback_hours)
    except Exception as exc:
        _log.warning("dedup.db_fetch_failed", error=str(exc))
        return signals, intra_dropped

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

    total_dropped = intra_dropped + inter_dropped
    _log.info(
        "dedup.complete",
        input=len(signals) + intra_dropped,
        unique=len(unique),
        dropped=total_dropped,
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
