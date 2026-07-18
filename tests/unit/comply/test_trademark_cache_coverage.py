"""Cover aegis.comply.trademark.cache.TrademarkCache (SQLite TTL cache)."""

from __future__ import annotations

from aegis.comply.schemas import TrademarkMatch
from aegis.comply.trademark.cache import TrademarkCache


def _match() -> TrademarkMatch:
    return TrademarkMatch(mark="Nike", owner="Nike Inc", similarity=0.95, source="uspto")


def test_put_get_roundtrip() -> None:
    cache = TrademarkCache(ttl_s=100)
    assert cache.get("nike") is None
    cache.put("Nike", [_match()])
    out = cache.get("nike")  # case-insensitive
    assert out is not None
    assert out[0].mark == "Nike"
    cache.close()


def test_expired_entry_returns_none() -> None:
    cache = TrademarkCache(ttl_s=10)
    cache.put("nike", [_match()], now=0.0)
    # query far in the future → expired
    assert cache.get("nike", now=1_000.0) is None
    cache.close()


def test_replace_existing_term() -> None:
    cache = TrademarkCache(ttl_s=100)
    cache.put("nike", [_match()])
    cache.put("nike", [])  # overwrite
    assert cache.get("nike") == []
    cache.close()
