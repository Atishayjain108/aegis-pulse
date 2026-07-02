"""PASS2-2A: MinHash + LSH three-layer dedup pipeline tests.

Signals are passed as plain dicts — ``_sig_title`` duck-types ProductSignal
attrs and dict keys, which keeps these tests free of the heavyweight frozen
Pydantic model construction.
"""
from __future__ import annotations

import time

import pytest

from aegis.db.dedup import (
    DedupResult,
    _get_or_build_minhash,
    clear_minhash_cache,
    deduplicate_batch,
    deduplicate_batch_detailed,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_minhash_cache()
    yield
    clear_minhash_cache()


def _sig(title: str) -> dict:
    return {"title": title}


class TestLayer1Exact:
    def test_identical_titles_same_words_different_order(self):
        kept, results = deduplicate_batch_detailed(
            [
                _sig("NVIDIA record quarterly revenue posted"),
                _sig("posted quarterly revenue record NVIDIA"),
            ]
        )
        assert len(kept) == 1
        assert results[0].dedup_method == "pass"
        assert results[1].is_duplicate is True
        assert results[1].dedup_method == "exact"
        assert results[1].similarity == 1.0


class TestLayer2MinHash:
    def test_near_duplicate_paraphrase_caught(self):
        kept, results = deduplicate_batch_detailed(
            [
                _sig("NVIDIA posts record quarterly revenue for Q3 2026"),
                _sig("NVIDIA posts record quarterly revenues for Q3 2026!"),
            ]
        )
        assert len(kept) == 1
        assert results[1].is_duplicate is True
        assert results[1].dedup_method == "minhash"
        assert results[1].similarity >= 0.82

    def test_genuinely_different_signals_not_flagged(self):
        kept, results = deduplicate_batch_detailed(
            [
                _sig("NVIDIA posts record quarterly revenue"),
                _sig("Bitcoin falls below 40k amid regulatory fears"),
                _sig("New WHO guidance on sugar consumption published"),
            ]
        )
        assert len(kept) == 3
        assert all(r.dedup_method == "pass" for r in results)

    def test_minhash_cache_hit_skips_recompute(self):
        title = "Some very specific cached title about AI chips"
        m1 = _get_or_build_minhash(title)
        m2 = _get_or_build_minhash(title)
        assert m1 is not None
        # Cache hit returns the identical object — no recompute.
        assert m1 is m2

    def test_datasketch_unavailable_graceful_fallback(self, monkeypatch):
        import aegis.db.dedup as dd

        monkeypatch.setattr(dd, "_MINHASH_AVAILABLE", False)
        kept, results = deduplicate_batch_detailed(
            [
                _sig("NVIDIA posts record quarterly revenue for Q3 2026"),
                _sig("NVIDIA posts record quarterly revenues for Q3 2026!"),
                _sig("Completely unrelated story about sea turtles"),
            ]
        )
        # Pairwise fallback still catches the near-dup — no exception.
        assert len(kept) == 2
        assert results[1].is_duplicate is True
        assert results[1].dedup_method == "minhash"


class TestPipelineContract:
    def test_dedup_method_populated_for_each_path(self):
        kept, results = deduplicate_batch_detailed(
            [
                _sig("alpha beta gamma delta epsilon"),
                _sig("epsilon delta gamma beta alpha"),  # exact (same tokens)
                _sig("alpha beta gamma delta epsilons!"),  # minhash near-dup
                _sig("totally different topic entirely here"),  # pass
            ]
        )
        methods = [r.dedup_method for r in results]
        assert methods == ["pass", "exact", "minhash", "pass"]
        assert len(kept) == 2

    def test_untitled_signals_always_pass(self):
        kept, results = deduplicate_batch_detailed([_sig(""), _sig(""), {"id": 1}])
        assert len(kept) == 3
        assert all(r.dedup_method == "pass" for r in results)

    def test_empty_batch_returns_empty(self):
        kept, results = deduplicate_batch_detailed([])
        assert kept == []
        assert results == []
        kept2, dropped = deduplicate_batch([])
        assert kept2 == [] and dropped == 0

    def test_wrapper_dropped_count_matches_detailed(self):
        sigs = [
            _sig("one two three four five"),
            _sig("five four three two one"),
            _sig("something else entirely different"),
        ]
        kept, dropped = deduplicate_batch(sigs)
        _, results = deduplicate_batch_detailed(sigs)
        assert dropped == sum(1 for r in results if r.is_duplicate) == 1
        assert len(kept) == 2

    def test_results_align_with_input_order(self):
        sigs = [_sig(f"unique title number {i} about topic {i}") for i in range(5)]
        _, results = deduplicate_batch_detailed(sigs)
        assert len(results) == 5
        assert all(isinstance(r, DedupResult) for r in results)


class TestPerformance:
    def test_thousand_signals_under_500ms(self):
        import hashlib

        def _words(seed: str, n: int = 6) -> str:
            # Deterministic pseudo-random word soup — genuinely distinct
            # titles (low char overlap), unlike templated f-strings.
            return " ".join(
                hashlib.sha256(f"{seed}:{j}".encode()).hexdigest()[:8] for j in range(n)
            )

        distinct = [_sig(_words(f"t{i}")) for i in range(700)]
        # 300 near-duplicates: copy of an earlier title with one word changed.
        near_dups = [
            _sig(_words(f"t{i}", n=5) + " extraword") for i in range(300)
        ]
        sigs = distinct + near_dups
        # Coverage line-tracing slows pure-Python loops ~3-4×; keep the spec's
        # 500 ms budget for uninstrumented runs, relax under instrumentation.
        import sys

        budget = 2.0 if (sys.gettrace() is not None or sys.monitoring.get_tool(1)) else 0.5
        start = time.perf_counter()
        kept, results = deduplicate_batch_detailed(sigs)
        elapsed = time.perf_counter() - start
        assert elapsed < budget, f"dedup took {elapsed:.3f}s for 1000 signals (budget {budget}s)"
        assert len(results) == 1000
        # The 700 distinct titles all survive; near-dups mostly collapse.
        assert 700 <= len(kept) < 1000


class TestSemanticLayer:
    async def test_semantic_disabled_by_default(self, monkeypatch):
        from aegis.db.dedup import _semantic_dedup_pass

        monkeypatch.delenv("AEGIS_DEDUP_SEMANTIC_ENABLED", raising=False)
        sigs = [_sig("a"), _sig("b")]
        kept, dropped = await _semantic_dedup_pass(sigs)
        assert kept == sigs
        assert dropped == 0

    async def test_semantic_gateway_failure_is_graceful(self, monkeypatch):
        from aegis.db.dedup import _semantic_dedup_pass

        monkeypatch.setenv("AEGIS_DEDUP_SEMANTIC_ENABLED", "true")

        class _BoomGateway:
            async def embed(self, text):
                raise RuntimeError("ollama down")

        import aegis.llm.bridge.agents_bridge as bridge

        monkeypatch.setattr(bridge, "get_gateway", lambda: _BoomGateway())
        sigs = [_sig("first title here"), _sig("second title here")]
        kept, dropped = await _semantic_dedup_pass(sigs)
        # Embed failure → layer skipped, signals unchanged.
        assert kept == sigs
        assert dropped == 0

    async def test_semantic_drops_paraphrase_above_cosine(self, monkeypatch):
        from aegis.db.dedup import _semantic_dedup_pass

        monkeypatch.setenv("AEGIS_DEDUP_SEMANTIC_ENABLED", "true")

        vectors = {
            "GPU prices surge worldwide": [1.0, 0.0, 0.1],
            "Graphics card costs spike globally": [0.99, 0.0, 0.1],
            "Coffee production drops in Brazil": [0.0, 1.0, 0.0],
        }

        class _FakeGateway:
            async def embed(self, text):
                return vectors[text]

        import aegis.llm.bridge.agents_bridge as bridge

        monkeypatch.setattr(bridge, "get_gateway", lambda: _FakeGateway())
        sigs = [_sig(t) for t in vectors]
        kept, dropped = await _semantic_dedup_pass(sigs)
        assert dropped == 1
        assert len(kept) == 2
