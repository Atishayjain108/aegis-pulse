"""Pass 10 — dedup performance benchmarks.

`deduplicate_batch(1000)` must complete in under 500 ms on CPU (the §
performance invariant for the scrape dedup layer). Also asserts that an
empty batch returns instantly without touching the MinHash layer.
"""

from __future__ import annotations

import asyncio
import sys
import time

from aegis.scrape.dedup import deduplicate_batch

# Coverage/debugger tracing inflates wall time ~4-6×; relax the bound when a
# trace function is active so the perf invariant still holds under `--cov`.
_PERF_BUDGET_MS = 500.0 if sys.gettrace() is None else 3000.0


def _make_signals(n: int) -> list[dict]:
    return [
        {
            "url": f"https://example.com/post/{i}",
            "title": f"Breakout signal number {i} about market trend alpha",
        }
        for i in range(n)
    ]


def test_deduplicate_batch_performance() -> None:
    """Happy path: 1000 unique signals dedup in < 500 ms (§ perf invariant)."""
    signals = _make_signals(1000)
    start = time.perf_counter()
    unique, results = asyncio.run(deduplicate_batch(signals))
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert len(results) == 1000
    assert len(unique) <= 1000
    assert elapsed_ms < _PERF_BUDGET_MS, (
        f"dedup_batch(1000) took {elapsed_ms:.0f}ms, must be < {_PERF_BUDGET_MS:.0f}ms"
    )


def test_deduplicate_empty_batch_is_instant() -> None:
    """Edge/failure path: empty input short-circuits with empty output."""
    start = time.perf_counter()
    unique, results = asyncio.run(deduplicate_batch([]))
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert unique == []
    assert results == []
    assert elapsed_ms < 50


def test_deduplicate_detects_exact_duplicates() -> None:
    """Invariant: identical titles collapse to a single unique signal."""
    dupes = [
        {"url": f"https://x.com/{i}", "title": "Identical headline text here"}
        for i in range(20)
    ]
    unique, results = asyncio.run(deduplicate_batch(dupes))
    assert len(unique) == 1
    assert sum(1 for r in results if r.is_duplicate) == 19
