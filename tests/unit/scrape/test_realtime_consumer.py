"""Unit tests for aegis.scrape.realtime_consumer (ORPH-3 / CONN-4)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aegis.scrape import realtime_consumer as rc
from aegis.scrape.realtime_consumer import SignalStreamConsumer, _build_candidate
from aegis.scrape.stream_bridge import CONSUMER_GROUP, PHASE0_STREAM


class _FakeRedis:
    def __init__(self) -> None:
        self.acked: list[tuple[str, str, str]] = []

    async def xack(self, stream: str, group: str, entry_id: str) -> int:
        self.acked.append((stream, group, entry_id))
        return 1


def _sig(platform: str, age_h: float, author: str = "a") -> dict:
    ts = (datetime.now(UTC) - timedelta(hours=age_h)).isoformat()
    return {
        "platform": platform,
        "author": author,
        "scraped_at": ts,
        "sentiment_score": 0.4,
        "commercial_intent": 0.6,
    }


def test_build_candidate_velocity_windows():
    signals = [_sig("reddit", 0.5), _sig("hn", 3.0, "b"), _sig("reddit", 20.0, "c")]
    cand = _build_candidate(signals, "ai chips")
    assert cand.title == "ai chips"
    assert cand.signal_count == 3
    assert cand.unique_authors == 3
    assert set(cand.platforms) == {"reddit", "hn"}
    # 1 signal within 1h, 2 within 6h, all 3 within 24h.
    assert cand.velocity_1h == 1.0
    assert cand.velocity_6h == 2.0
    assert cand.velocity_24h == 3.0
    assert cand.trend_id.startswith("rt-")


def test_build_candidate_handles_missing_and_bad_timestamps():
    signals = [{"platform": "x"}, {"platform": "y", "scraped_at": "not-a-date"}]
    cand = _build_candidate(signals, "")
    assert cand.title == "realtime-harvest"
    assert cand.signal_count == 2
    # No author keys → zero unique authors.
    assert cand.unique_authors == 0


async def test_process_entry_empty_signals_acks_and_skips(monkeypatch):
    called = {"run": False}

    async def _fake_run_trend(*a, **k):
        called["run"] = True

    monkeypatch.setattr(rc, "run_trend", _fake_run_trend)
    redis = _FakeRedis()
    consumer = SignalStreamConsumer(redis_client=redis, redis_client_p4=redis)
    await consumer._process_entry("1-0", {"body": '{"signals": [], "topic": "t"}'})
    assert called["run"] is False
    assert redis.acked == [(PHASE0_STREAM, CONSUMER_GROUP, "1-0")]


async def test_process_entry_dispatches_and_acks(monkeypatch):
    captured = {}

    async def _fake_run_trend(candidate, **kwargs):
        captured["candidate"] = candidate
        captured["kwargs"] = kwargs

    monkeypatch.setattr(rc, "run_trend", _fake_run_trend)
    redis = _FakeRedis()
    consumer = SignalStreamConsumer(redis_client=redis, redis_client_p4=redis, timeout_s=12.0)
    payload = '{"signals": [{"platform": "reddit", "author": "z"}], "topic": "gpu", "tenant_id": "tnt"}'
    await consumer._process_entry("2-0", {"body": payload})
    assert captured["candidate"].title == "gpu"
    assert captured["kwargs"]["tenant_id"] == "tnt"
    assert captured["kwargs"]["timeout_s"] == 12.0
    assert redis.acked == [(PHASE0_STREAM, CONSUMER_GROUP, "2-0")]


async def test_process_entry_failure_does_not_ack(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("graph blew up")

    monkeypatch.setattr(rc, "run_trend", _boom)
    redis = _FakeRedis()
    consumer = SignalStreamConsumer(redis_client=redis, redis_client_p4=redis)
    payload = '{"signals": [{"platform": "reddit", "author": "z"}], "topic": "gpu"}'
    # Should swallow the exception (entry stays in PEL for redelivery).
    await consumer._process_entry("3-0", {"body": payload})
    assert redis.acked == []
