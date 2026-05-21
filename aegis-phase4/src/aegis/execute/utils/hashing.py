"""Content-addressable hashing for Phase 4.

Determinism rule: a given semantic alert must always produce the same
`alert_id`. This is the foundation of idempotency at the outbox layer.
"""

from __future__ import annotations

import hashlib
from typing import Final
from uuid import UUID

ALERT_ID_HEX_LEN: Final[int] = 32  # half of SHA-256, 128 bits — collision-safe at scale
INTENT_ID_HEX_LEN: Final[int] = 32


def _norm(s: str) -> str:
    """Normalise a string for hashing: lowercase, strip whitespace."""
    return s.strip().lower()


def compute_alert_id(
    *,
    tenant_id: UUID,
    trend_id: str,
    decision_window: str,
    verdict: str,
    priority: int,
) -> str:
    """Compute a deterministic alert ID.

    Two alerts with the same tuple produce the same ID, which makes the
    outbox upsert (`ON CONFLICT (alert_id) DO NOTHING`) collapse retries
    into a single row.

    Returns a 32-hex-char string (128-bit prefix of SHA-256).
    """
    payload = "|".join(
        [
            str(tenant_id),
            _norm(trend_id),
            _norm(decision_window),
            _norm(verdict),
            str(int(priority)),
        ]
    )
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return h[:ALERT_ID_HEX_LEN]


def compute_intent_id(*, alert_id: str, kind: str) -> str:
    """Compute a deterministic intent ID derived from its parent alert."""
    payload = f"{_norm(alert_id)}|{_norm(kind)}"
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return h[:INTENT_ID_HEX_LEN]


def compute_dedup_hash(*, alert_id: str, channel: str) -> str:
    """Per-channel dedup hash (Redis SET NX key suffix)."""
    return hashlib.sha256(f"{alert_id}|{_norm(channel)}".encode()).hexdigest()


__all__ = [
    "ALERT_ID_HEX_LEN",
    "INTENT_ID_HEX_LEN",
    "compute_alert_id",
    "compute_dedup_hash",
    "compute_intent_id",
]
