"""Unit tests for the autonomous MarketSentinel discovery loop."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from aegis.scheduler.sentinel import (
    SENTINEL_STREAM,
    MarketSentinel,
    SentinelConfig,
    SentinelScan,
)


@dataclass
class _FakePattern:
    label: str
    velocity_slope: float
    acceleration: float
    confidence: float
    is_breakout: bool
    pattern_type: str = "accelerating"
    platforms: list[str] | None = None


def _cfg(**kw: object) -> SentinelConfig:
    base = {
        "radar_limit": 60,
        "min_slope": 1.5,
        "min_confidence": 0.45,
        "top_k": 3,
        "require_breakout": True,
        "report_depth": "standard",
    }
    base.update(kw)
    return SentinelConfig(**base)  # type: ignore[arg-type]


def test_rank_filters_weak_and_noise_patterns() -> None:
    sentinel = MarketSentinel(config=_cfg())
    patterns = [
        _FakePattern("strong breakout", 4.0, 2.0, 0.9, True),
        _FakePattern("slow", 0.5, 0.1, 0.9, True),            # slope below min
        _FakePattern("low conf", 3.0, 1.0, 0.2, True),        # confidence below min
        _FakePattern("not breakout", 3.0, 1.0, 0.9, False),   # not a breakout
        _FakePattern("x", 9.0, 9.0, 0.9, True),               # label too short
    ]
    kept = sentinel._rank_breakouts(patterns)
    assert [p.label for p in kept] == ["strong breakout"]


def test_rank_orders_by_acceleration_then_confidence() -> None:
    sentinel = MarketSentinel(config=_cfg())
    patterns = [
        _FakePattern("alpha", 3.0, 1.0, 0.6, True),
        _FakePattern("bravo", 3.0, 5.0, 0.6, True),
        _FakePattern("charlie", 3.0, 5.0, 0.9, True),
    ]
    kept = sentinel._rank_breakouts(patterns)
    # Highest acceleration first; ties broken by confidence.
    assert [p.label for p in kept] == ["charlie", "bravo", "alpha"]


def test_require_breakout_disabled_keeps_non_breakout() -> None:
    sentinel = MarketSentinel(config=_cfg(require_breakout=False))
    patterns = [_FakePattern("emerging trend", 2.0, 0.5, 0.6, False)]
    kept = sentinel._rank_breakouts(patterns)
    assert [p.label for p in kept] == ["emerging trend"]


async def test_scan_with_no_radar_signals_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = MarketSentinel(config=_cfg())

    async def _empty_sweep(_scan: SentinelScan) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(sentinel, "_sweep_radar", _empty_sweep)
    scan = await sentinel.scan()
    assert scan.radar_signals == 0
    assert scan.reports == []
    assert scan.errors == []


async def test_scan_investigates_breakouts_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[tuple[str, dict[str, object]]] = []

    class _FakeRedis:
        async def xadd(self, stream: str, fields: dict[str, object], **_: object) -> None:
            published.append((stream, fields))

    sentinel = MarketSentinel(redis=_FakeRedis(), config=_cfg(top_k=1))

    async def _sweep(_scan: SentinelScan) -> list[dict[str, object]]:
        return [{"title": "wireless earbuds", "platform": "gdelt"}]

    monkeypatch.setattr(sentinel, "_sweep_radar", _sweep)
    monkeypatch.setattr(
        sentinel,
        "_detect_breakouts",
        lambda _s: [_FakePattern("wireless earbuds", 4.0, 2.0, 0.9, True)],
    )

    async def _fake_investigate(pattern: object, _scan: SentinelScan) -> dict[str, object]:
        return {"query": getattr(pattern, "label", ""), "product_count": 5}

    monkeypatch.setattr(sentinel, "_investigate", _fake_investigate)

    scan = await sentinel.scan()
    assert scan.breakouts == 1
    assert len(scan.reports) == 1
    assert scan.reports[0]["query"] == "wireless earbuds"
    assert published and published[0][0] == SENTINEL_STREAM


def test_scan_to_dict_is_serializable() -> None:
    scan = SentinelScan(started_at=datetime.now(UTC), radar_signals=10, breakouts=2)
    d = scan.to_dict()
    assert d["radar_signals"] == 10
    assert d["breakouts"] == 2
    assert d["report_count"] == 0
