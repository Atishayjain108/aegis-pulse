"""Cover aegis.compliance.cache.ComplianceCache (in-process TTL dict)."""

from __future__ import annotations

from aegis.compliance.cache import ComplianceCache


def test_make_key_is_deterministic_and_normalized() -> None:
    k1 = ComplianceCache.make_key("SKU1", "Cotton Tee", "CN", "US")
    k2 = ComplianceCache.make_key("SKU1", "  cotton tee  ", "CN", "US")
    assert k1 == k2  # title lower+strip normalized
    k3 = ComplianceCache.make_key("SKU2", "Cotton Tee", "CN", "US")
    assert k1 != k3


def test_set_get_and_miss() -> None:
    cache = ComplianceCache(ttl_seconds=100)
    key = ComplianceCache.make_key("s", "t", "CN", "US")
    assert cache.get(key) is None
    cache.set(key, {"risk": 0.2})
    assert cache.get(key) == {"risk": 0.2}
    assert len(cache) == 1


def test_ttl_expiry() -> None:
    cache = ComplianceCache(ttl_seconds=-1)  # already expired
    key = "k"
    cache.set(key, "v")
    assert cache.get(key) is None  # expired → evicted
    assert len(cache) == 0


def test_invalidate_and_clear() -> None:
    cache = ComplianceCache()
    cache.set("a", 1)
    cache.invalidate("a")
    assert cache.get("a") is None
    cache.set("b", 2)
    cache.clear()
    assert len(cache) == 0
