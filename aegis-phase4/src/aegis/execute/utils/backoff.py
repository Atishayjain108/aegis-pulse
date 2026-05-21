"""Decorrelated jitter backoff.

Reference: AWS Architecture Blog, "Exponential Backoff and Jitter" (2015).
Decorrelated jitter is empirically a better choice than full jitter for
contention-heavy retry storms.

Formula:
    sleep = min(cap, random_between(base, prev * 3))
"""

from __future__ import annotations

import random
from typing import Final

from aegis.execute.constants import OUTBOX_BACKOFF_BASE_S, OUTBOX_BACKOFF_MAX_S


def next_backoff_seconds(
    *,
    previous: float,
    base: float = OUTBOX_BACKOFF_BASE_S,
    cap: float = OUTBOX_BACKOFF_MAX_S,
    rng: random.Random | None = None,
) -> float:
    """Return next sleep duration in seconds.

    `previous` is the duration used for the prior attempt (0.0 for first).
    `rng` is injectable for deterministic tests.
    """
    if previous < 0:
        raise ValueError("previous must be >= 0")
    if base <= 0 or cap <= 0:
        raise ValueError("base and cap must be > 0")
    r = rng if rng is not None else random.Random()
    upper = max(base, previous * 3.0)
    return min(cap, r.uniform(base, upper))


__all__: Final = ["next_backoff_seconds"]
