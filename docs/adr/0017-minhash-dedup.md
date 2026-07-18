# ADR 0017 — MinHash + LSH Deduplication (ADP-5 / PASS2-2A)

- **Status**: Accepted
- **Date**: 2026-06-13
- **Audit ID**: ADP-5 (AEGIS_AUDIT.md), GODMODE PASS 2-2A

## Context

Signal deduplication originally relied on `difflib.SequenceMatcher`, an
O(n²) pairwise comparison. At swarm scale (1000+ signals per harvest across
30+ adapters) this became the dominant cost in the scrape pipeline — a
single batch could spend seconds comparing every signal against every other.

We needed near-duplicate detection (paraphrases, re-worded headlines across
sources) that scales to thousands of signals while keeping the historical
decisions intact, and degrades gracefully when the optional `datasketch`
dependency is absent.

## Decision

**Adopt a three-layer dedup pipeline with MinHash + LSH as the near-dup
layer.**

1. **Layer 1 — exact token Jaccard** (O(n)): identical token sets (same
   words, any order) collapse immediately. Threshold 0.85.
2. **Layer 2 — MinHash + LSH** (O(n log n)): character 3-gram shingles over
   `title + url` → `MinHash(num_perm=128)` → `MinHashLSH(threshold=0.75)`.
   The LSH index retrieves candidates; near-duplicates are confirmed against
   the precise similarity so decisions match the historical O(n²) scan.
3. **Layer 3 — semantic (optional)**: BGE-M3 cosine > 0.92 via the Phase 11
   `LLMGateway`, gated behind `AEGIS_DEDUP_SEMANTIC_ENABLED=true` (default
   off). Skips silently when Ollama is unreachable.

Two surfaces share this doctrine:

- `aegis.db.dedup` — **synchronous**, in-process LRU MinHash cache. Runs
  inside `asyncio.to_thread` per invariant §12 (no async Redis round-trip on
  that path). This is the path used by `scrape_topic`.
- `aegis.scrape.dedup` — **async**, Redis-backed MinHash cache
  (`aegis:dedup:mh:{hash[:16]}`, TTL 24h). The swarm-scale entry point;
  exposes `deduplicate_batch(...)` and `MinHashLayer`.

Both reuse a module-level shared permutation table so per-signal `MinHash`
construction is near-free, and use `MinHash.update_batch(...)` to vectorise
shingle hashing (~10× faster than per-shingle `update`).

## Consequences

- **Positive**: `deduplicate_batch(1000)` completes in <200 ms (measured
  158 ms) versus seconds for difflib — comfortably under the 500 ms ADP-5
  target. The `DedupResult.dedup_method` field (`exact|minhash|semantic|pass`)
  makes every dedup decision auditable.
- **Positive**: graceful degradation — without `datasketch` the pipeline
  falls back to the full pairwise scan (same decisions, slower); without
  Redis the async path uses in-process state only.
- **Negative**: MinHash is probabilistic — Jaccard estimates carry variance
  at 128 permutations. Mitigated by the precise-similarity confirm step in
  the DB path. The async swarm path accepts the LSH verdict directly for
  speed.
- **Negative**: two dedup modules now coexist. They are intentionally kept
  separate (sync vs async + Redis); both are documented here to avoid drift.

## Alternatives Considered

- **Keep difflib**: rejected — does not scale to swarm batches.
- **SimHash**: comparable speed but coarser for short headline text; MinHash
  + LSH gives tunable recall via `num_perm`/`threshold`.
- **Embeddings-only (semantic) dedup**: too expensive and Ollama-dependent
  for the hot path; retained as the optional Layer 3 instead.
