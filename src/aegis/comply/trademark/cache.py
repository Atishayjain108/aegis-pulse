"""SQLite-backed cache for live trademark lookups (stdlib only).

Caches normalized (term -> matches JSON) with a TTL so repeated live lookups for
the same term avoid hitting the registry. Defaults to an in-memory DB; pass a
path for persistence (e.g. ``~/.aegis/comply/trademark_cache.sqlite3``).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from aegis.comply.constants import TRADEMARK_CACHE_TTL_S
from aegis.comply.schemas import TrademarkMatch


class TrademarkCache:
    """Tiny TTL cache over SQLite."""

    def __init__(self, path: str | Path = ":memory:", *, ttl_s: int = TRADEMARK_CACHE_TTL_S) -> None:
        self._ttl = ttl_s
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS tm_cache ("
            "term TEXT PRIMARY KEY, payload TEXT NOT NULL, stored_at REAL NOT NULL)"
        )
        self._conn.commit()

    def get(self, term: str, *, now: float | None = None) -> list[TrademarkMatch] | None:
        """Return cached matches for ``term`` if present and unexpired."""
        now = time.time() if now is None else now
        row = self._conn.execute(
            "SELECT payload, stored_at FROM tm_cache WHERE term = ?", (term.lower(),)
        ).fetchone()
        if row is None:
            return None
        payload, stored_at = row
        if (now - stored_at) > self._ttl:
            return None
        return [TrademarkMatch.model_validate(m) for m in json.loads(payload)]

    def put(self, term: str, matches: list[TrademarkMatch], *, now: float | None = None) -> None:
        """Store ``matches`` for ``term``."""
        now = time.time() if now is None else now
        payload = json.dumps([m.model_dump(mode="json") for m in matches])
        self._conn.execute(
            "INSERT OR REPLACE INTO tm_cache (term, payload, stored_at) VALUES (?, ?, ?)",
            (term.lower(), payload, now),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
