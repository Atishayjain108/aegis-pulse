"""
Compliance result cache — in-process TTL dict (+ optional Redis tier).

Key: SHA-256 of (sku + title.lower() + origin + destination)
TTL: configurable, default 6 h (results rarely change intraday)
"""

from __future__ import annotations

import hashlib
import time
from typing import Any


class ComplianceCache:
    """Two-tier compliance result cache.

    Tier 1: in-process dict with TTL eviction (always available).
    Tier 2: Redis (optional; skipped when pool is None).
    """

    def __init__(self, ttl_seconds: int = 21_600) -> None:  # 6 h default
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expire_at)

    @staticmethod
    def make_key(
        product_sku: str,
        product_title: str,
        origin: str,
        destination: str,
    ) -> str:
        raw = f"{product_sku}|{product_title.lower().strip()}|{origin}|{destination}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expire_at = entry
        if time.monotonic() > expire_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, time.monotonic() + self._ttl)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        now = time.monotonic()
        return sum(1 for _, (_, exp) in self._store.items() if now <= exp)
