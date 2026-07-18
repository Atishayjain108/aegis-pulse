"""Targeted tests for aegis.scrape.confidence internal helpers."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from aegis.scrape.confidence import (
    _author_diversity_score,
    _platform_str,
    _posted_ts,
    _safe_get,
    _title_len,
)


def test_safe_get_dict_and_object_and_missing():
    assert _safe_get({"title": "x"}, "title") == "x"
    assert _safe_get(SimpleNamespace(title="y"), "title") == "y"
    # Falls through all attrs → None.
    assert _safe_get({"a": None}, "title", "name") is None


def test_title_len_strips():
    assert _title_len({"title": "  hello  "}) == 5
    assert _title_len({}) == 0


def test_platform_str_enum_and_plain():
    assert _platform_str({"platform": "reddit"}) == "reddit"
    assert _platform_str(SimpleNamespace(platform=SimpleNamespace(value="hn"))) == "hn"
    assert _platform_str({}) == "unknown"


def test_posted_ts_from_posted_at_attr():
    now = datetime.now(UTC)
    sig = SimpleNamespace(posted_at=now)
    assert _posted_ts(sig) == now.timestamp()


def test_posted_ts_from_provenance():
    now = datetime.now(UTC)
    sig = SimpleNamespace(posted_at=None, provenance=SimpleNamespace(scraped_at=now))
    assert _posted_ts(sig) == now.timestamp()


def test_posted_ts_from_dict():
    now = datetime.now(UTC)
    assert _posted_ts({"captured_at": now}) == now.timestamp()
    assert _posted_ts({}) is None


def test_author_diversity_no_metadata_returns_one():
    assert _author_diversity_score([{"title": "a"}, {"title": "b"}]) == 1.0


def test_author_diversity_healthy_ratio():
    sigs = [{"author": f"u{i}"} for i in range(10)]
    assert _author_diversity_score(sigs) == 1.0


def test_author_diversity_astroturf_penalised():
    # 1 unique author across 20 signals → ratio 0.05 → score 0.5.
    sigs = [{"author": "spammer"} for _ in range(20)]
    assert _author_diversity_score(sigs) == 0.5
